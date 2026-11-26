"""Speech to text with ffmpeg's own `whisper` filter (whisper.cpp), fully offline.

Needs an ffmpeg built with whisper.cpp (8.0+; `whisper` shows in `ffmpeg -filters`) and a model
from `nookcut models install` (user-run; NookCut never downloads on its own).

whisper.cpp stretches the first words after a pause back to the start of its audio chunk, up to
several seconds early. So before transcribing, the quiet stretches between speech are cut out
(found with a speech-band energy envelope that ffmpeg computes in about a second per 30 minutes),
and the word times are mapped back to the original timeline. With loud non-stop background sound
nothing can be cut, and the audio is transcribed as it is.

transcript.json: {"model": "base", "language": "auto", "tracks": [0], "words": [[t0, t1, "text"], ...]}
"""
from __future__ import annotations

import bisect
import codecs
import json
import math
import os
import re
import struct
import wave

from .errors import MediaError
from .tasks import Task, filter_quote, run

ENV_RATE = 100      # envelope frames per second
SR = 16000          # whisper's sample rate
_NOISE = re.compile(r"[\[\]\(\)\*♪♫]|^\s*(-+|\.+)\s*$")  # sound labels: [music], (laughs), *noises*
# whisper's well-known inventions on music or noise; dropped only when they are a whole sentence
_HALLUCINATIONS = {"thank you for watching", "thanks for watching", "thank you", "please subscribe",
                   "subtitles by the amaraorg community", "you", "bye", "music", "applause"}
_END = (".", "?", "!", "…")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def parse(lines: str | bytes) -> list[list]:
    """The filter's JSON lines ({"start": ms, "end": ms, "text": ...}) -> [[t0, t1, word], ...].
    Sound labels whisper writes as text ([music], (laughs), *noises*) are dropped, also when they
    are split over several words.

    whisper.cpp writes raw token bytes, so a character can be split over two words or a stray byte
    can appear (often in invented foreign-language text). Pass the file's bytes: a split character
    is joined back into the word it starts, and bytes that make no character are dropped."""
    if isinstance(lines, bytes):
        lines = lines.decode("utf-8", "surrogateescape")
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    words, label, pend = [], False, None   # pend: (start, text) of a word whose last character runs on
    for line in lines.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        raw = str(item.get("text", "")).encode("utf-8", "surrogateescape")
        text = decoder.decode(raw).replace("�", "")
        if pend:
            item, text, pend = {**item, "start": pend[0]}, pend[1] + text, None
        if decoder.getstate()[0]:      # ends inside a character: the rest comes with the next word
            pend = (item["start"], text)
            continue
        text = text.strip()
        opens = text[:1] in "[(*♪♫" if text else False
        closes = text[-1:] in "])*♪♫" if text else False
        if label or opens:
            label = not closes or (opens and len(text) == 1)
            continue
        if not text or _NOISE.search(text):
            continue
        words.append([round(item["start"] / 1000, 3), round(item["end"] / 1000, 3), text])
    return drop_hallucinations(words)


def sentences(words: list[list], gap: float = 0.3) -> list[list[list]]:
    """Split at sentence punctuation or a pause longer than `gap` seconds."""
    out, cur = [], []
    for k, w in enumerate(words):
        cur.append(w)
        nxt = words[k + 1] if k + 1 < len(words) else None
        if not nxt or w[2].endswith(_END) or nxt[0] - w[1] > gap:
            out.append(cur)
            cur = []
    return out


def drop_hallucinations(words: list[list]) -> list[list]:
    return [w for s in sentences(words) if _norm(" ".join(x[2] for x in s)) not in _HALLUCINATIONS for w in s]


def _audio_in(tracks: list[int]) -> str:
    labels = "".join(f"[0:a:{t}]" for t in tracks)
    return f"{labels}amix=inputs={len(tracks)}:normalize=0," if len(tracks) > 1 else labels


def envelope(source: str, tracks: list[int], task: Task | None = None) -> list[float]:
    """Speech-band (250-3500 Hz) power in dB, ENV_RATE frames per second."""
    graph = (_audio_in(tracks) + f"aformat=channel_layouts=mono,aresample={SR},highpass=f=250,lowpass=f=3500,"
             f"aeval=val(0)*val(0),aresample={ENV_RATE}[e]")
    raw = run(["ffmpeg", "-v", "error", "-i", source, "-filter_complex", graph, "-map", "[e]",
               "-f", "f32le", "-"], task, "measuring the voice", capture=True)
    n = len(raw) // 4
    return [round(10 * math.log10(max(v, 1e-10)), 1) for v in struct.unpack(f"<{n}f", raw[:n * 4])]


def speech_regions(env: list[float], rate: int = ENV_RATE, pad: float = 0.25, join: float = 0.6,
                   keep_all: float = 0.85) -> list[tuple[float, float]] | None:
    """Stretches that stand out from the local background (where speech can be), padded and
    merged. None when nearly everything stands out (non-stop sound): transcribe it all then."""
    if not env:
        return None
    blocks = [sorted(env[i:i + rate]) for i in range(0, len(env), rate)]
    floors = [b[len(b) // 10] for b in blocks]
    near = [sorted(floors[max(0, b - 5):b + 6]) for b in range(len(floors))]
    thr = [max(n[len(n) // 2] + 9, -55) for n in near]  # median background nearby, +9 dB
    out: list[list[float]] = []
    for k, v in enumerate(env):
        if v > thr[k // rate]:
            t = k / rate
            if out and t - pad - out[-1][1] <= join:
                out[-1][1] = t + 1 / rate + pad
            else:
                out.append([max(0.0, t - pad), t + 1 / rate + pad])
    kept = sum(b - a for a, b in out)
    if not out or kept > keep_all * len(env) / rate:
        return None
    end = len(env) / rate
    return [(round(a, 2), round(min(b, end), 2)) for a, b in out]


def _extract(source: str, tracks: list[int], regions, wav_path: str, task: Task | None) -> list[tuple]:
    """Write the kept audio (16 kHz mono) to wav_path; return [(out_start, src_start)] to map times back."""
    cuts = [(round(a * SR), round(b * SR)) for a, b in regions] if regions else [(0, 1 << 62)]
    spans, state = [], {"pos": 0, "i": 0, "out": 0, "carry": b""}
    w = wave.open(wav_path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)

    def chunk(data: bytes):
        data = state["carry"] + data
        n = len(data) // 2
        state["carry"] = data[n * 2:]
        pos, end = state["pos"], state["pos"] + n
        while state["i"] < len(cuts) and pos < end:
            a, b = cuts[state["i"]]
            lo, hi = max(a, pos), min(b, end)
            if lo < hi:
                if lo == a or not spans or spans[-1][2] != state["i"]:
                    spans.append((state["out"] / SR, lo / SR, state["i"]))
                w.writeframes(data[(lo - state["pos"]) * 2:(hi - state["pos"]) * 2])
                state["out"] += hi - lo
            if b <= end:
                state["i"] += 1
                pos = max(pos, b)
            else:
                break
        state["pos"] = end

    try:
        run(["ffmpeg", "-v", "error", "-i", source, "-filter_complex",
             _audio_in(tracks) + f"aformat=channel_layouts=mono,aresample={SR}[a]", "-map", "[a]",
             "-f", "s16le", "-"], task, "reading the speech", on_chunk=chunk)
    finally:
        w.close()
    return [(o, s) for o, s, _ in spans]


def to_source(t: float, spans: list[tuple]) -> float:
    """A time in the cut-down audio -> the same moment in the original recording."""
    i = max(0, bisect.bisect_right([o for o, _ in spans], t) - 1)
    return round(spans[i][1] + t - spans[i][0], 3) if spans else t


def map_word(a: float, b: float, spans: list[tuple]) -> tuple[float, float]:
    """A word's times in the cut-down audio -> the original recording. whisper often stretches the
    last word before a removed pause up to the next stretch; it can't end after its own stretch."""
    if not spans:
        return a, b
    i = max(0, bisect.bisect_right([o for o, _ in spans], a) - 1)
    sa, sb = to_source(a, spans), to_source(b, spans)
    if i + 1 < len(spans) and b > spans[i + 1][0]:
        sb = round(spans[i][1] + spans[i + 1][0] - spans[i][0], 3)  # end of the stretch it starts in
    return sa, max(sa, sb)


def transcribe(source: str, tracks: list[int], model: tuple[str, str], out_json: str, language: str = "auto",
               task: Task | None = None, duration: float = 0.0, env: list[float] | None = None) -> dict:
    """Transcribe the given audio tracks (mixed) to word-level timestamps; writes out_json.
    env: the envelope() of these tracks, when the caller already has it."""
    task = task or Task(track=False)
    name, path = model
    lang = re.sub(r"[^a-z]", "", (language or "auto").lower()) or "auto"
    wav, dest = out_json + ".speech.wav", out_json + ".whisper.jsonl"
    try:
        if env is None:
            task.begin("finding speech", note="cutting out the quiet parts")
            env = envelope(source, tracks, task)
        regions = speech_regions(env)
        spans = _extract(source, tracks, regions, wav, task)
        speech_s = os.path.getsize(wav) / (2 * SR)
        task.begin("transcribing speech", max(speech_s, 1),
                   note=f"{speech_s / 60:.1f} min of audio, whisper {name} model, language {lang}")

        def once(gpu: bool):
            if os.path.exists(dest):
                os.remove(dest)
            graph = (f"whisper=model={filter_quote(path)}:language={lang}:queue=20:use_gpu={int(gpu)}"
                     f":destination={filter_quote(dest)}:format=json:max_len=1")
            run(["ffmpeg", "-v", "error", "-nostdin", "-i", wav, "-af", graph, "-f", "null", "-"],
                task, "transcribing speech", progress=task.set)

        words = []
        if speech_s > 0.3:
            try:
                once(True)
            except MediaError:
                once(False)  # GPU backends can fail on some drivers; the CPU always works
            with open(dest, "rb") as f:
                words = parse(f.read())
    finally:
        for p in (wav, dest):
            if os.path.exists(p):
                os.remove(p)
    words = [[*map_word(a, b, spans), t] for a, b, t in words]
    result = {"model": name, "language": lang, "tracks": tracks, "words": words}
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return result


# ---------- using a transcript ----------

def between(words: list[list], a: float, b: float) -> list[list]:
    """Words whose middle falls inside [a, b)."""
    return [w for w in words if a <= (w[0] + w[1]) / 2 < b]


def caption_words(words: list[list], a: float, b: float) -> list[list]:
    """Words to caption in a cut [a, b): whole sentences, or the larger part of one. A word or two
    left over from a sentence that mostly falls outside the cut would read as noise on screen."""
    out = []
    for s in sentences(words):
        inside = between(s, a, b)
        if inside and (len(inside) == len(s) or len(inside) >= max(2, len(s) / 2)):
            out += inside
    return out


def text(words: list[list]) -> str:
    return " ".join(w[2] for w in words)


def phrases(words: list[list], max_chars: int = 16, max_words: int = 3, gap: float = 0.45) -> list[list[list]]:
    """Group words into short on-screen phrases: break at punctuation, pauses and size limits."""
    out, cur = [], []
    for w in words:
        if cur and (w[0] - cur[-1][1] > gap or len(cur) >= max_words
                    or len(text(cur + [w])) > max_chars or cur[-1][2].endswith(_END + (",",))):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def snap(a: float, b: float, words: list[list], tol: float, end: float) -> tuple[float, float]:
    """Move cut points out of the middle of words: extend to the whole word when it is within
    `tol` seconds, otherwise drop the partial word. `end` is the source duration."""
    def inside(t):
        return next((w for w in words if w[0] < t < w[1]), None)

    w = inside(a)
    if w:
        a = w[0] - 0.05 if a - w[0] <= tol else w[1]
        if inside(a):  # no room before a word that follows another directly
            a = w[0]
    w = inside(b)
    if w:
        b = w[1] + 0.08 if w[1] - b <= tol else w[0] - 0.02
        if inside(b):
            b = w[1] if b > w[1] else w[0]
    return round(max(0.0, a), 3), round(min(end, b), 3)


# ---------- the user's own words: names speech-to-text gets wrong ----------

TERM_SURE = 0.72      # a misheard run of 2-3 words this close to a term is replaced
TERM_LETTERS = 0.8    # a single word must also be spelled this close (else it may be a real word)
TERM_MAYBE = 0.66     # closer than this, but not sure: reported for the agent to check


def _letters(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _skeleton(s: str) -> str:
    """Consonants only, without a leading h: 'organonit' and 'argonaut' both read 'rgnnt'/'rgnt'."""
    return re.sub(r"[aeiouy]", "", re.sub(r"^h", "", s))


def _likeness(said: str, term: str) -> tuple[float, float]:
    from difflib import SequenceMatcher
    a, b = _letters(said), _letters(term)
    if not a or not b or not 0.6 <= len(a) / len(b) <= 1.6:
        return 0.0, 0.0
    letters = SequenceMatcher(None, a, b).ratio()
    return (letters + SequenceMatcher(None, _skeleton(a), _skeleton(b)).ratio()) / 2, letters


def apply_terms(words: list[list], terms: list[str]) -> tuple[list[list], list[dict]]:
    """Put the user's names (game terms, people, places) where speech-to-text misheard them:
    'an organ on it' -> 'an Argonaut'. Returns (words, maybes): sure matches are replaced (keeping
    the times and the punctuation after them); likely ones are only listed, since a single real
    word can sound alike ('argument')."""
    terms = [t.strip() for t in terms or [] if t and t.strip()]
    if not terms or not words:
        return words, []
    out, maybes, k = [], [], 0
    while k < len(words):
        sure_best, maybe_best = None, None
        for n in (3, 2, 1):
            run_ = words[k:k + n]
            if len(run_) < n:
                continue
            said = " ".join(w[2] for w in run_)
            for term in terms:
                if n > 1 and any(_letters(w[2]) == _letters(term) for w in run_):
                    continue   # a word already right is never merged with its neighbours
                first = _letters(run_[0][2])
                if n > 1 and len(first) <= 2 and not _letters(term).startswith(first):
                    continue   # "an organ on it": the name starts after the little word
                if said.strip(",.!?;:").lower() == term.lower():
                    score, letters = 1.0, 1.0
                else:
                    score, letters = _likeness(said, term)
                if n > 1:   # the first word must help: else the name starts later ("two argonauts")
                    rest, _ = _likeness(" ".join(w[2] for w in run_[1:]), term)
                    if rest >= score:
                        continue
                rank = score - 0.04 * (n - 1)   # on a tie, fewer words
                cand = (rank, score, n, term, said)
                if score >= 1.0 or (score >= TERM_SURE and (n > 1 or letters >= TERM_LETTERS)):
                    if not sure_best or rank > sure_best[0]:
                        sure_best = cand
                elif not maybe_best or rank > maybe_best[0]:
                    maybe_best = cand
        if sure_best:
            _, score, n, term, said = sure_best
            tail = re.search(r"[,.!?;:…]*$", words[k + n - 1][2]).group(0)
            plain, root = _letters(said), _letters(term)
            ending = plain[len(root):] if plain.startswith(root) else ""   # Argonauts keeps its s
            out.append([words[k][0], words[k + n - 1][1], term + ending + tail])
            k += n
            continue
        score, n, term, said = maybe_best[1:] if maybe_best else (0.0, 1, "", "")
        if score >= TERM_MAYBE and not (maybes and maybes[-1]["until"] > words[k][0]):
            maybes.append({"t": words[k][0], "said": said.strip(",.!?;:"), "term": term,
                           "until": words[k + n - 1][1]})
        out.append(words[k])
        k += 1
    return out, [{k: m[k] for k in ("t", "said", "term")} for m in maybes]
