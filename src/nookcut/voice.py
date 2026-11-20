"""Voiceover: lines written for segments, spoken by a voice on this computer, mixed over the bed.

Engines (set on the server only, never from a brief or edit, so a remote caller can't run commands):
  system    the operating system's own voice: macOS `say`; Windows' System.Speech and Linux
            espeak-ng are wired up but untested
  command   your own text-to-speech program (Kokoro, Piper, ...) from NOOKCUT_TTS_COMMAND, an argv
            template with {text_file} {out_wav} {voice} {speed} {language}; run without a shell.
            `nookcut config kokoro` sets it up for Kokoro (adapters/kokoro_say.py)
NOOKCUT_TTS=auto|system|command|off picks one (auto: command if set, else system).

The text always goes through a file (never a command line), and every run goes through
tasks.run, so cancel and cleanup work as for ffmpeg.
"""
from __future__ import annotations

import functools
import hashlib
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile

from .errors import EnvironmentNotReady, InputError
from .tasks import Task, run

# macOS voices that are effects or the old robotic Eloquence set: never picked automatically
_NOVELTY = {"albert", "bad news", "bahh", "bells", "boing", "bubbles", "cellos", "good news", "jester", "organ",
            "superstar", "trinoids", "whisper", "wobble", "zarvox", "fred", "junior", "ralph", "kathy",
            "eddy", "flo", "reed", "rocko", "sandy", "shelley", "grandma", "grandpa"}
# preferred built-in voices per language, best first (the user can always name one)
_PREFER = {"en": ("samantha", "daniel", "karen", "moira", "tessa", "rishi"), "es": ("monica", "paulina"),
           "fr": ("thomas", "amelie"), "de": ("anna",), "hi": ("lekha",), "it": ("alice",), "pt": ("luciana",)}
_PS = r"""param($textFile, $outFile, $voice, $rate)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($voice) { $s.SelectVoice($voice) }
$s.Rate = [int]$rate
$s.SetOutputToWaveFile($outFile)
$s.Speak([IO.File]::ReadAllText($textFile))
$s.Dispose()
"""


def engine() -> str | None:
    want = os.getenv("NOOKCUT_TTS", "auto").strip().lower()
    if want == "off":
        return None
    if want in ("command", "auto") and os.getenv("NOOKCUT_TTS_COMMAND", "").strip():
        return "command"
    if want in ("system", "auto") and _system_tool():
        return "system"
    return None


def _system_tool() -> str | None:
    system = platform.system()
    if system == "Darwin":
        return shutil.which("say")
    if system == "Windows":
        return shutil.which("powershell") or shutil.which("pwsh")
    return shutil.which("espeak-ng") or shutil.which("espeak")


KOKORO = os.path.join(os.path.dirname(__file__), "adapters", "kokoro_say.py")


def kokoro_command(python: str, models: str) -> str:
    """The NOOKCUT_TTS_COMMAND that runs the bundled Kokoro adapter with this Python and model folder."""
    return " ".join(shlex.quote(p) for p in (python, KOKORO)) + " --text-file {text_file} --out {out_wav} " \
        "--voice {voice} --speed {speed} --language {language} --models " + shlex.quote(models)


@functools.lru_cache(maxsize=None)
def command_voices() -> list[dict]:
    """Voices of the configured command, when it's the Kokoro adapter (it can list them)."""
    cmd = shlex.split(os.getenv("NOOKCUT_TTS_COMMAND", ""))
    if not cmd or not any(p.endswith("kokoro_say.py") for p in cmd):
        return []
    keep = cmd[:2]   # the Python and the adapter, plus the model folder if one is given
    if "--models" in cmd[:-1]:
        keep += ["--models", cmd[cmd.index("--models") + 1]]
    try:
        out = subprocess.run(keep + ["--list"], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    langs = {"a": "en_US", "b": "en_GB", "e": "es", "f": "fr", "h": "hi", "i": "it", "p": "pt_BR", "j": "ja", "z": "zh"}
    return [{"name": v, "language": langs.get(v[:1], "?")} for v in out.split()]


@functools.lru_cache(maxsize=None)
def voices() -> list[dict]:
    """[{"name", "language"}] the system engine can speak with (macOS: `say -v ?`)."""
    tool = _system_tool()
    if not tool:
        return []
    try:
        if platform.system() == "Darwin":
            out = subprocess.run([tool, "-v", "?"], capture_output=True, text=True, timeout=20).stdout
            found = [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"^(.+?)\s+([a-z]{2}_[A-Z]{2,3})\s+#", out, re.M)]
        elif platform.system() == "Windows":
            out = subprocess.run([tool, "-NoProfile", "-Command",
                                  "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis."
                                  "SpeechSynthesizer).GetInstalledVoices() | % { $_.VoiceInfo.Name + '|' + "
                                  "$_.VoiceInfo.Culture.Name }"], capture_output=True, text=True, timeout=30).stdout
            found = [tuple(line.split("|", 1)) for line in out.splitlines() if "|" in line]
        else:
            out = subprocess.run([tool, "--voices"], capture_output=True, text=True, timeout=20).stdout
            found = [(cols[3], cols[1]) for cols in (line.split() for line in out.splitlines()[1:]) if len(cols) > 3]
    except (OSError, subprocess.TimeoutExpired):
        return []
    seen, out_list = set(), []
    for name, lang in found:
        if name not in seen:
            seen.add(name)
            out_list.append({"name": name, "language": lang.replace("-", "_")})
    return out_list


def pick_voice(language: str, wanted: str = "") -> str | None:
    """The voice to use: the one asked for if it exists, else the best natural-sounding built-in voice
    for the language, else None (no voice for that language)."""
    if engine() == "command":
        return wanted or ""
    names = voices()
    if wanted:
        hit = next((v["name"] for v in names if v["name"].lower() == wanted.lower()
                    or v["name"].lower().startswith(wanted.lower() + " (")), None)
        if not hit:
            raise InputError(f"There is no voice called {wanted!r} on this computer.", "Pick one from list_voices.")
        return hit
    lang = (language or "en").lower()[:2]
    usable = [v for v in names if v["language"].lower().startswith(lang)
              and v["name"].split(" (")[0].lower() not in _NOVELTY]
    for pref in _PREFER.get(lang, ()):
        hit = next((v["name"] for v in usable if v["name"].split(" (")[0].lower() == pref), None)
        if hit:
            return hit
    return usable[0]["name"] if usable else None


def _key(text: str, voice: str, speed: float) -> str:
    return hashlib.sha1(f"{engine()}|{os.getenv('NOOKCUT_TTS_COMMAND', '')}|{voice}|{speed}|{text}".encode()).hexdigest()[:16]


def speak(text: str, voice: str, speed: float, out_wav: str, task: Task | None = None, language: str = "en") -> str:
    """One line -> a 48 kHz mono wav at out_wav."""
    which = engine()
    if not which:
        raise EnvironmentNotReady("There is no voice to speak with on this computer.",
                                  "On macOS the built-in voice works; on Linux install espeak-ng; or set "
                                  "NOOKCUT_TTS_COMMAND to your own text-to-speech program.")
    work = tempfile.mkdtemp(prefix="nookcut_voice_")
    try:
        text_file = os.path.join(work, "line.txt")
        with open(text_file, "w", encoding="utf-8") as f:
            f.write(text)
        raw = os.path.join(work, "raw.aiff" if platform.system() == "Darwin" and which == "system" else "raw.wav")
        if which == "command":
            values = {"text_file": text_file, "out_wav": raw, "voice": voice or "", "speed": f"{speed:g}",
                      "language": (language or "en").lower()}
            cmd = [re.sub(r"\{(text_file|out_wav|voice|speed|language)\}", lambda m: values[m.group(1)], part)
                   for part in shlex.split(os.environ["NOOKCUT_TTS_COMMAND"])]
        elif platform.system() == "Darwin":
            cmd = ["say", "-v", voice, "-r", str(round(185 * speed)), "-f", text_file, "-o", raw]
        elif platform.system() == "Windows":
            script = os.path.join(work, "say.ps1")
            with open(script, "w", encoding="utf-8") as f:
                f.write(_PS)
            cmd = [_system_tool(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
                   text_file, raw, voice or "", str(round((speed - 1) * 10))]
        else:
            cmd = [_system_tool(), "-v", voice or "en", "-s", str(round(170 * speed)), "-f", text_file, "-w", raw]
        run(cmd, task, "speaking a voiceover line", timeout=120)
        if not os.path.exists(raw):
            raise InputError("The voice program finished without writing any sound.",
                             "Check NOOKCUT_TTS_COMMAND writes to {out_wav}.")
        run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", out_wav],
            task, "preparing a voiceover line")
        return out_wav
    finally:
        shutil.rmtree(work, ignore_errors=True)


@functools.lru_cache(maxsize=None)
def status() -> dict:
    """What check_environment reports: the engine and whether it really speaks here (test-run once)."""
    which = engine()
    out = {"engine": {"system": "the computer's own voice", "command": "your NOOKCUT_TTS_COMMAND"}.get(which, "none"),
           "ready": False}
    if not which:
        out["how"] = ("set NOOKCUT_TTS_COMMAND to a text-to-speech program, or install espeak-ng (Linux)")
        return out
    voice = pick_voice("en") if which == "system" else ""
    try:
        with tempfile.TemporaryDirectory() as d:
            speak("Test.", voice or "", 1.0, os.path.join(d, "t.wav"), language="en")
        out["ready"] = True
        out["default_voice"] = voice
    except Exception as e:  # report, never fail the environment check over an optional feature
        out["how"] = f"the voice didn't work: {getattr(e, 'message', e)}"
    return out


def cached(text: str, voice: str, speed: float, cache_dir: str, task: Task | None = None, language: str = "en") -> str:
    """speak(), cached by text, voice, speed, language and engine: editing one line re-speaks only that line."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, _key(f"{language}|{text}", voice, speed) + ".wav")
    if not os.path.exists(path):
        part = path + ".part.wav"
        try:
            speak(text, voice, speed, part, task, language)
            os.replace(part, path)
        finally:
            if os.path.exists(part):
                os.remove(part)
    return path
