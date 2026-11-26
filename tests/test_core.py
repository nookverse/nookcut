"""Fast tests with synthetic signals (no video decoding). Run: python -m unittest discover tests"""
import unittest

from nookcut.analyze import dark_events, gameplay, moment_scores, DEFAULTS
from nookcut.brief import Brief
from nookcut.edit import total, validate
from nookcut.export import edl
from nookcut.publish import posting_windows
from nookcut.story import choose_template, plan


def fake_signals(deaths=(100, 200, 300), win=400, length=460, rate=2.0):
    n = int(length * rate)
    B, R, M = [120.0] * n, [0.8] * n, [10.0] * n
    for t in (*deaths, win):
        k = int(t * rate)
        for j in range(k, k + 6):
            B[j] = 5.0
        if t != win:
            for j in range(k - 6, k):
                R[j] = 1.6      # red damage vignette before a death
    for t in range(330, 395, 7):
        M[int(t * rate)] = 40.0  # action spikes in the winning run
    return {"info": {"duration": length, "width": 3840, "height": 2160, "fps": 60, "timecode": "01:00:00:00",
                     "audio_tracks": []},
            "rate": rate, "brightness": B, "redness": R, "motion": M, "loudness": None}


def fake_analysis(**kw):
    sig = fake_signals(**kw)
    ev = dark_events(sig, DEFAULTS)
    return {"signals": sig, "params": DEFAULTS, "events": ev,
            "gameplay": gameplay(sig, ev, DEFAULTS, []), "scores": moment_scores(sig)}


class Detection(unittest.TestCase):
    def test_deaths_and_win(self):
        a = fake_analysis()
        self.assertEqual(len(a["gameplay"]["deaths"]), 3)
        self.assertAlmostEqual(a["gameplay"]["win"]["onset"], 400, delta=1)
        self.assertAlmostEqual(a["gameplay"]["final_attempt"]["start"], 303, delta=2)

    def test_summary_caps_long_lists(self):
        from nookcut.analyze import SUMMARY_LIST, summary
        a = fake_analysis()
        a["signals"]["info"] = {"duration": 9000.0, "width": 1280, "height": 720, "fps": 30.0}
        s = summary(a)
        self.assertEqual(s["deaths"]["count"], 3)
        self.assertEqual(len(s["deaths"]["at"]), 3)
        self.assertNotIn("note", s["deaths"])
        many = [{"onset": float(i), "start": float(i), "end": i + 0.5, "red_pre": i % 7, "kind": "death"}
                for i in range(170)]
        a["gameplay"]["deaths"], a["events"] = many, many
        s = summary(a)
        self.assertEqual(s["deaths"]["count"], 170)
        self.assertEqual(len(s["deaths"]["at"]), SUMMARY_LIST)
        self.assertEqual(s["deaths"]["at"], sorted(s["deaths"]["at"]))
        self.assertIn("analysis.json", s["deaths"]["note"])
        self.assertEqual(s["fades"]["count"], 0)


class Story(unittest.TestCase):
    def brief(self, **kw):
        return Brief(title="Boss fight", source="/x.mov", game="Test", **kw)

    def test_comeback_fits_budget(self):
        a = fake_analysis()
        for max_d in (30, 60, 90):
            e = plan(self.brief(max_duration=max_d), a)
            self.assertEqual(e["template"], "comeback")
            self.assertLessEqual(total(e), max_d)
            self.assertEqual(validate(e, 460), [])
            self.assertEqual([s["kind"] for s in e["segments"]][0], "hook")

    def test_single_fade_is_not_trusted_as_win(self):
        a = fake_analysis(deaths=())
        self.assertEqual(choose_template(self.brief(), a), "highlights")

    def test_non_gameplay_uses_highlights(self):
        self.assertEqual(choose_template(self.brief(video_type="talking"), fake_analysis()), "highlights")

    def test_brief_warnings(self):
        w = Brief(title="t", source="s", max_duration=200, platforms=["youtube_shorts"], orientation="wide").validate()
        self.assertTrue(any("180" in x for x in w))

    def test_vertical_cap(self):
        b = Brief(title="t", source="s", max_duration="3:00")
        self.assertEqual(b.max_duration, 140)
        self.assertTrue(any("capped at 140" in x for x in b.validate()))
        self.assertEqual(Brief(title="t", source="s", max_duration=139).max_duration, 139)
        self.assertEqual(Brief(title="t", source="s", max_duration=200, orientation="wide").max_duration, 200)
        e = plan(self.brief(max_duration=600), fake_analysis())
        self.assertLessEqual(total(e), 140)


class OfflineCopy(unittest.TestCase):
    def brief(self, **kw):
        return Brief(**{"title": "Malenia boss fight", "source": "/x.mov", "game": "Elden Ring", **kw})

    def test_every_caption_key_has_patterns(self):
        from nookcut.copywriting import caption_keys, load
        data = load("en")[0]
        for template in ("comeback", "victory", "highlights"):
            for vt in ("gameplay", "talking", "tutorial", "sports", "generic"):
                for kind in ("hook", "card", "finisher", "outro", "end"):
                    for keys in caption_keys(kind, template, vt, 3):
                        if keys:
                            self.assertTrue(any(data["captions"].get(k) for k in keys), (template, vt, kind, keys))

    def test_captions_fit_and_are_deterministic(self):
        a = fake_analysis()
        for vt in ("gameplay", "talking", "tutorial", "sports", "generic"):
            b = self.brief(video_type=vt, title="A really quite long title about something")
            e1, e2 = plan(b, a), plan(b, a)
            self.assertEqual([s["top"] for s in e1["segments"]], [s["top"] for s in e2["segments"]])
            for s in e1["segments"]:
                for line in s["top"].split("|"):
                    self.assertLessEqual(len(line), 16, (vt, s["top"]))
            self.assertTrue(e1["segments"][0]["top"], vt)

    def test_slots_skip_and_fill(self):
        from nookcut.copywriting import fill
        self.assertIsNone(fill("{game}: {title}", {"game": "", "title": "x"}))
        self.assertEqual(fill("{TITLE} {n}", {"title": "boss", "n": 3}), "BOSS 3")

    def test_suggest_without_internet(self):
        a = fake_analysis()
        b = self.brief()
        s = __import__("nookcut.copywriting", fromlist=["suggest"]).suggest(b, a, plan(b, a))
        self.assertEqual(s["source"], "built-in library (offline)")
        self.assertTrue(s["titles"] and s["description"])
        self.assertIn("3", s["titles"][1])
        self.assertEqual(s["file_name"], "elden-ring-malenia-boss-fight-3-deaths")
        for tags in s["hashtags"].values():
            self.assertEqual(len(tags), len(set(tags)))
            self.assertLessEqual(len(tags), 6)
            for t in tags:
                self.assertRegex(t, r"^#\w+$")
                self.assertEqual(t, t.lower())
        self.assertIn("#shorts", s["hashtags"]["youtube_shorts"])
        self.assertTrue(all(c.get("top_options") or c.get("bottom_options") for c in s["captions"]))

    def test_game_name_not_repeated_and_accents_folded(self):
        from nookcut.copywriting import suggest
        a = fake_analysis()
        b = self.brief(title="Ronin Okami duel", game="Ronin Ōkami")
        s = suggest(b, a, plan(b, a))
        self.assertEqual(s["file_name"], "ronin-okami-duel-3-deaths")
        self.assertTrue(all(t.lower().count("ronin") <= 1 for t in s["titles"]), s["titles"])
        self.assertIn("#roninokami", s["hashtags"]["tiktok"])
        self.assertEqual(s["description"].count("3 deaths"), 1, s["description"])

    def test_non_game_ignores_deaths(self):
        from nookcut.copywriting import suggest
        a = fake_analysis()
        b = self.brief(video_type="talking", game="", title="Sleep tips")
        s = suggest(b, a, plan(b, a))
        self.assertNotIn("deaths", s["file_name"])

    def test_unknown_language_falls_back_with_note(self):
        import os, tempfile
        from unittest import mock
        from nookcut.copywriting import init_pack, load
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"NOOKCUT_COPY_DIR": d}):
            data, code, note = load("Bengali")
            self.assertEqual((code, bool(note)), ("en", True))
            path = init_pack("bn")
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"captions": {"outro.top": ["জয়"]}}, f, ensure_ascii=False)
            data, code, note = load("bn-BD")
            self.assertEqual((code, note, data["captions"]["outro.top"]), ("bn", "", ["জয়"]))
            self.assertTrue(data["captions"]["hook.sub"])  # partial pack: rest falls back to English

    def test_forgiving_brief_inputs(self):
        b = Brief(title="t", source="s", orientation="9:16", platforms="Shorts, TikTok,reels",
                  video_type="Podcast", max_duration="1:30", music="", source_audio="background", online="yes")
        self.assertEqual((b.orientation, b.platforms, b.video_type, b.max_duration, b.music, b.source_audio, b.online),
                         ("vertical", ["youtube_shorts", "tiktok", "instagram_reels"], "talking", 90.0, "ai", "duck", True))
        for v, want in ((60, 60), ("45s", 45), ("1m", 60), ("1m30s", 90), ("90 sec", 90)):
            self.assertEqual(Brief(title="t", source="s", max_duration=v).max_duration, want)
        with self.assertRaises(ValueError) as e:
            Brief(title="t", source="s", orientation="diagonal").validate()
        self.assertIn("vertical", str(e.exception))
        self.assertFalse(Brief(title="t", source="s").online)

    def test_old_brief_files_still_load(self):
        import json, os, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "brief.json")
            with open(path, "w") as f:
                json.dump({"title": "t", "source": "s", "max_duration": 60, "removed_field": 1}, f)
            self.assertFalse(Brief.load(path).online)

    def test_kit_fills_from_library(self):
        import os, tempfile
        from nookcut import edit as ed, pipeline as pl
        a = fake_analysis()
        b = self.brief(region="Bangladesh")
        with tempfile.TemporaryDirectory() as d:
            pl.new_project(d, b)
            ed.save(plan(b, a), pl.paths(d)["edit"])
            with open(pl.make_kit(d, os.path.join(d, "v.mp4")), encoding="utf-8") as f:
                text = f.read()
        self.assertIn("Elden Ring", text)
        self.assertIn("- youtube_shorts: #eldenring", text)
        self.assertIn("built-in library (offline)", text)
        self.assertNotIn("(add titles)", text)

    def test_fixed_plan_keeps_overall_stages(self):
        from nookcut.tasks import Task
        t = Task("make", track=False)
        t.plan("a", "b", "c")
        t.fixed_plan = True
        t.plan("x")
        self.assertEqual([n for n, _ in t.stages], ["a", "b", "c"])


def fake_words(sentences, start=5.0, gap=1.5, per_word=0.4):
    """[[t0, t1, word], ...] for sentences spoken one after another with pauses between."""
    words, t = [], start
    for sentence in sentences:
        for w in sentence.split():
            words.append([round(t, 2), round(t + per_word - 0.05, 2), w])
            t += per_word
        t += gap
    return words


class TitleCopy(unittest.TestCase):
    def test_titles_do_not_restate_the_users_title(self):
        from nookcut.copywriting import repeats_title, suggest
        self.assertTrue(repeats_title("It took me 17 tries took me 18 tries | G", "It took me 17 tries"))
        self.assertTrue(repeats_title("G: It took me 17 tries after 17 deaths", "It took me 17 tries"))
        self.assertFalse(repeats_title("I died 17 times to get this (G)", "Boss fight"))
        b = Brief(title="It took me 17 tries", source="x.mp4", game="Ronin Okami")
        a = fake_analysis()
        titles = suggest(b, a, plan(b, a), 10)["titles"]
        self.assertTrue(titles)
        self.assertFalse([t for t in titles if repeats_title(t, b.title)], titles)


class Speech(unittest.TestCase):
    def test_parse_drops_noise_and_hallucinations(self):
        from nookcut.transcribe import parse
        lines = "\n".join(['{"start":0,"end":20000,"text":"[music]"}', '{"start":20000,"end":20400,"text":"Okay,"}',
                           '{"start":20400,"end":20800,"text":"go."}', '{"start":30000,"end":30500,"text":"Thank"}',
                           '{"start":30500,"end":31000,"text":"you."}', 'not json'])
        self.assertEqual(parse(lines), [[20.0, 20.4, "Okay,"], [20.4, 20.8, "go."]])
        self.assertEqual(parse('{"start":0,"end":500,"text":"noises*"}'), [])
        split = "\n".join(f'{{"start":{i * 100},"end":{i * 100 + 90},"text":"{w}"}}'
                          for i, w in enumerate(["*fangirling", "loud", "noises*", "Hello", "there."]))
        self.assertEqual([w[2] for w in parse(split)], ["Hello", "there."])

    def test_parse_survives_broken_utf8(self):
        from nookcut.transcribe import parse
        line = lambda t0, text: (b'{"start":%d,"end":%d,"text":"' % (t0, t0 + 90)) + text + b'"}'
        raw = b"\n".join([
            line(0, b" Don\xe2\x80"),     # "Don’t" with the apostrophe split over two words
            line(100, b"\x99t"),
            line(200, b" go\x99"),        # a stray byte that makes no character
            line(300, "감사합니다".encode()[:4]),   # a Korean character split in half
            line(400, "감사합니다".encode()[4:]),
        ])
        self.assertEqual(parse(raw), [[0.0, 0.19, "Don’t"], [0.2, 0.29, "go"], [0.3, 0.49, "감사합니다"]])

    def test_stray_words_are_not_captioned(self):
        from nookcut.transcribe import caption_words
        words = fake_words(["this is a long sentence that ends here.", "Short one."], start=0, gap=1)
        self.assertEqual(caption_words(words, 2.7, 6.0), [[4.2, 4.55, "Short"], [4.6, 4.95, "one."]])
        self.assertEqual(len(caption_words(words, 0.0, 2.0)), 5)   # most of the first sentence

    def test_speech_regions_cut_quiet_parts(self):
        from nookcut.transcribe import speech_regions, to_source
        env = [-100.0] * 500 + [-20.0] * 200 + [-100.0] * 800 + [-22.0] * 100 + [-100.0] * 300
        regions = speech_regions(env)
        self.assertEqual(len(regions), 2)
        self.assertAlmostEqual(regions[0][0], 4.75, delta=0.02)
        self.assertAlmostEqual(regions[1][1], 16.25, delta=0.02)
        self.assertIsNone(speech_regions([-30.0] * 1000))  # non-stop sound: transcribe everything
        spans = [(0.0, 4.75), (2.5, 14.75)]                  # (time in cut audio, time in source)
        self.assertEqual(to_source(1.0, spans), 5.75)
        self.assertEqual(to_source(3.0, spans), 15.25)
        from nookcut.transcribe import map_word
        self.assertEqual(map_word(2.0, 3.5, spans), (6.75, 7.25))   # can't run into the next stretch

    def test_phrases_and_snap(self):
        from nookcut.transcribe import phrases, snap
        words = fake_words(["one two three four. five six"], start=0, gap=0)
        groups = phrases(words, max_chars=16, max_words=3)
        self.assertEqual([len(g) for g in groups], [3, 1, 2])
        self.assertEqual(snap(0.5, 1.0, words, 0.6, 10), (0.35, 1.15))   # finish both words, stop before "four"
        self.assertEqual(snap(0.5, 1.0, words, 0.0, 10), (0.75, 0.78))   # no stretch: drop partial words

    def test_ass_and_srt(self):
        from nookcut.subtitles import ass, local_words, srt
        seg = {"src_in": 10.0, "src_out": 12.0, "speed": 0.5, "kind": "hook", "top": "", "bottom": "",
               "words": [[10.0, 10.4, "Watch"], [10.4, 10.9, "this,"], [11.5, 13.0, "now!"]]}
        local = local_words(seg)
        self.assertEqual(local[0][:2], [0.0, 0.8])          # slowed down 2x
        self.assertEqual(local[-1][1], 4.0)                 # clipped to the segment
        script = ass(local, 4.0, 1080, 1920, 1440, 112, "/nonexistent/Impact.ttf", 16)
        self.assertIn("Style: Speech,Impact,112", script)
        self.assertEqual(script.count("Dialogue:"), 3)
        self.assertIn("\\c&H0000D4FF&", script)
        self.assertIn("WATCH{\\r} THIS\n", script)  # spoken word highlighted, comma dropped
        out = srt({"segments": [seg], "subtitles": True})
        self.assertTrue(out.startswith("1\n00:00:00,000 --> "))
        self.assertIn("Watch this,", out)

    def test_talking_video_cuts_on_sentences(self):
        from nookcut.story import add_speech
        b = Brief(title="Boss guide", source="x.mp4", video_type="talking", max_duration=20)
        words = fake_words(["First you wait behind the pillar.", "Then you roll forward when he jumps!",
                            "This part took me eight tries.", "Hit him three times and back off.",
                            "Let me know in the comments below."], start=5, gap=2)
        e = plan(b, fake_analysis(), words=words)
        add_speech(e, words, b)
        self.assertEqual(e["cut_by"], "sentences")
        self.assertLessEqual(total(e), 20)
        self.assertNotIn("comments", " ".join(w[2] for w in e["segments"][0]["words"]))   # sign-off isn't the hook
        self.assertIn("comments", " ".join(w[2] for w in e["segments"][-1]["words"]))     # it closes the video
        for s in e["segments"]:
            spoken = [w[2] for w in s["words"]]
            self.assertTrue(spoken[0][0].isupper() and spoken[-1].endswith((".", "!")), spoken)  # whole sentences
        self.assertTrue(e["subtitles"])

    def test_gameplay_cuts_only_snap(self):
        from nookcut.story import add_speech
        b = Brief(title="t", source="x.mp4", game="G", max_duration=45)
        words = fake_words(["let's go " * 60], start=1, gap=0, per_word=0.37)
        e = add_speech(plan(b, fake_analysis(), words=words), words, b)
        self.assertEqual(e["template"], "comeback")
        self.assertLessEqual(total(e), 45)
        for s in e["segments"]:
            if s["kind"] not in ("death", "card"):
                self.assertFalse(any(w[0] < s["src_in"] < w[1] or w[0] < s["src_out"] < w[1] for w in words))

    def test_tighten_keeps_the_talk_and_cuts_pauses(self):
        from nookcut.story import add_speech
        b = Brief(title="Podcast", source="x.mp4", video_type="talking", max_duration=10)
        a = fake_analysis()
        a["voice"] = [(5.0, 8.0), (8.1, 8.2), (12.0, 20.0), (30.0, 45.0)]   # (8.1, 8.2) is a click
        e = plan(b, a, "tighten")                             # no transcript: the energy decides
        self.assertEqual([(x["src_in"], x["src_out"]) for x in e["segments"]], [(5.0, 8.0), (12.0, 20.0), (30.0, 45.0)])
        self.assertEqual(total(e), 26.0)                     # whole talk, not cut down to max_duration
        words = fake_words(["one two three four five six seven", "eight nine.", "ten eleven twelve"],
                           start=5.1, gap=5)
        e = add_speech(plan(b, a, "tighten", words), words, b)
        self.assertEqual(e["music"], "none")
        spoken = [w[2] for x in e["segments"] for w in x["words"]]
        self.assertEqual(spoken, [w[2] for w in words])     # every word captioned once
        for x in e["segments"]:
            self.assertTrue(all(x["src_in"] <= w[0] < w[1] <= x["src_out"] for w in x["words"]))
        a["voice"] = [(5.0, 8.0), (9.0, 10.0), (12.0, 20.0)]   # (9, 10): a sound with no words in it
        e = plan(b, a, "tighten", words)
        self.assertEqual([x["src_in"] for x in e["segments"]], [5.0, 12.0])
        a["voice"] = None
        with self.assertRaises(ValueError):
            plan(b, a, "tighten")
        with self.assertRaises(ValueError):
            plan(b, a, "montage")

    def test_models_registry(self):
        import tempfile, os
        from nookcut import models
        self.assertIn(models.DEFAULT, models.WHISPER)
        self.assertTrue(all(len(v[2]) == 64 for v in models.WHISPER.values()))
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("NOOKCUT_MODELS_DIR")
            os.environ["NOOKCUT_MODELS_DIR"] = d
            try:
                self.assertIsNone(models.whisper_model())
                with open(os.path.join(d, "ggml-tiny.bin"), "wb") as f:
                    f.write(b"x")                     # wrong size: not counted as installed
                self.assertEqual(models.installed(), [])
            finally:
                os.environ.pop("NOOKCUT_MODELS_DIR")
                if old:
                    os.environ["NOOKCUT_MODELS_DIR"] = old

    def test_brief_subtitles(self):
        self.assertEqual(Brief(title="t", source="x", subtitles=True).subtitles, "on")
        self.assertEqual(Brief(title="t", source="x", subtitles="No").subtitles, "off")
        self.assertEqual(Brief(title="t", source="x").subtitles, "auto")


class Export(unittest.TestCase):
    def test_edl_uses_source_timecode_and_speed(self):
        e = plan(Brief(title="t", source="/x.mov", game="g"), fake_analysis())
        text = edl(e, 60)
        self.assertIn("01:06:", text)          # 400 s + 1h source timecode
        self.assertIn("M2   AX       030.0", text)

    def test_proxy_is_half_size_prores_with_timecode_and_sound(self):
        from nookcut.export import proxy_cmd
        e = {"source": "/x.mov", "source_timecode": "01:00:00:00"}
        cmd = proxy_cmd(e, "/out.mov", ("prores_videotoolbox", ["-profile:v", "proxy"], "GPU"), apple=True)
        self.assertEqual(cmd[cmd.index("-hwaccel") + 1], "videotoolbox")
        self.assertEqual(cmd[cmd.index("-timecode") + 1], "01:00:00:00")
        self.assertIn("scale=trunc(iw/4)*2:-2", cmd)
        self.assertIn("0:a?", cmd)
        self.assertEqual(cmd[-1], "/out.mov")
        cpu = proxy_cmd({"source": "/x.mov"}, "/o.mov", ("prores_ks", ["-profile:v", "0"], "CPU"))
        self.assertNotIn("-hwaccel", cpu)
        self.assertNotIn("-timecode", cpu)

    def test_lua_links_the_proxy_only_when_there_is_one(self):
        import tempfile
        from nookcut.export import resolve_kit
        e = plan(Brief(title="t", source="/x.mov", game="g"), fake_analysis())
        with tempfile.TemporaryDirectory() as d:
            out = resolve_kit(e, d, proxy_path="/p/x_proxy.mov")
            lua = open(out["lua"]).read()
            self.assertIn("local PROXY = [[/p/x_proxy.mov]]", lua)
            self.assertIn("LinkProxyMedia(PROXY)", lua)
            self.assertEqual(out["proxy"], "/p/x_proxy.mov")
            self.assertNotIn("proxy", resolve_kit(e, d))


class Machine(unittest.TestCase):
    def test_workers_follow_cores_memory_and_cooling(self):
        from nookcut.preflight import pick_workers
        self.assertEqual(pick_workers(18, 48, False), 4)   # MacBook Pro: capped at 4
        self.assertEqual(pick_workers(8, 8, False), 2)     # 8 GB: about one per 4 GB
        self.assertEqual(pick_workers(10, 24, True), 2)    # MacBook Air: no fan
        self.assertEqual(pick_workers(2, 4, False), 1)
        self.assertEqual(pick_workers(16, None, False), 4)  # memory unknown

    def test_env_overrides_the_machine(self):
        import os
        from unittest import mock
        from nookcut.preflight import render_workers
        with mock.patch.dict(os.environ, {"NOOKCUT_WORKERS": "7"}):
            self.assertEqual(render_workers(), 7)


class Publish(unittest.TestCase):
    def test_bangladesh_weekend_is_fri_sat(self):
        w = posting_windows("Bangladesh", "Asia/Dhaka", 7)["windows"]
        weekend_days = {x["day"][:3] for x in w if x["window"] in ("late morning", "evening")}
        self.assertEqual(weekend_days, {"Fri", "Sat"})

    def test_unknown_region(self):
        self.assertIn("error", posting_windows("Atlantis"))


class Portability(unittest.TestCase):
    def test_skill_copies_in_sync(self):
        import os
        from nookcut.agents import skill_body, skill_text
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(open(os.path.join(root, "skills/nookcut-editor/SKILL.md")).read(), skill_text())
        self.assertEqual(open(os.path.join(root, "GEMINI.md")).read(), skill_body())

    def test_export_refreshes_the_playbook_and_keeps_the_rest(self):
        import os, tempfile
        from nookcut import agents
        read = lambda p: open(p, encoding="utf-8").read()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "AGENTS.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# My project\n\nRun make test.\n")
            agents.export_rules("agents-md", d)
            first = read(path)
            self.assertTrue(first.startswith("# My project\n\nRun make test.\n\n" + agents.START))
            with open(path, "w", encoding="utf-8") as f:     # an old playbook between the markers
                f.write(first.replace("Framing: any game", "OLD SECTION") + "\n# Notes\nkeep me\n")
            agents.export_rules("agents-md", d)
            text = read(path)
            self.assertNotIn("OLD SECTION", text)
            self.assertEqual(text.count(agents.START), 1)
            self.assertTrue(text.startswith("# My project\n\nRun make test."))
            self.assertTrue(text.endswith("# Notes\nkeep me\n"))
            agents.export_rules("agents-md", d)
            self.assertEqual(read(path), text)                 # running it again changes nothing
            # an export from before the markers: replaced up to the next top-level heading
            legacy = os.path.join(d, "GEMINI.md")
            with open(legacy, "w", encoding="utf-8") as f:
                f.write("# Intro\nhello\n\n# NookCut editor\n\nold playbook\n## Rules\nold\n\n# Team\nbob\n")
            agents.export_rules("gemini", d)
            text = read(legacy)
            self.assertNotIn("old playbook", text)
            self.assertIn("Framing: any game", text)
            self.assertTrue(text.startswith("# Intro\nhello\n\n" + agents.START))
            self.assertTrue(text.endswith(agents.END + "\n\n# Team\nbob\n"))
            agents.export_rules("cursor", d)                   # a file of its own: written whole
            self.assertIn("alwaysApply: false", read(os.path.join(d, ".cursor/rules/nookcut.mdc")))

    def test_every_platform_has_config(self):
        from nookcut.agents import mcp_config, platforms
        for p in platforms():
            self.assertIn("where", mcp_config(p, "", "nookcut-mcp"))
            self.assertIn("where", mcp_config(p, "https://h/mcp", "nookcut-mcp"))


class Render(unittest.TestCase):
    def test_error_keeps_root_cause(self):
        from nookcut.errors import ffmpeg_detail as _ffmpeg_error
        stderr = ("[Parsed_crop_8] Invalid too big or non positive size for width '1304' or height '1088'\n"
                  "[Parsed_crop_8] Failed to configure input pad on Parsed_crop_8\n"
                  + "Could not open encoder before EOF\n" * 40
                  + "Nothing was written into output file\n")
        msg = _ffmpeg_error(stderr)
        self.assertIn("Invalid too big", msg)
        self.assertIn("Nothing was written", msg)
        self.assertEqual(msg.count("Could not open encoder"), 1)

    def test_hardware_decoder_choice(self):
        from nookcut.preflight import pick_decoder
        listing = "Hardware acceleration methods:\ncuda\nqsv\nd3d11va\nvaapi\n"
        self.assertEqual(pick_decoder(listing, "amf", "Windows"), "d3d11va")    # works on every GPU vendor
        self.assertEqual(pick_decoder(listing, "nvenc", "Linux"), "cuda")
        self.assertEqual(pick_decoder(listing, "vaapi", "Linux"), "vaapi")
        self.assertEqual(pick_decoder("Hardware acceleration methods:\n", None, "Linux"), None)

    def test_encoder_override_and_fallback_settings(self):
        from unittest import mock
        from nookcut.preflight import HW_ENCODERS, encoder_args, pick_encoder
        with mock.patch.dict("os.environ", {"NOOKCUT_ENCODER": "cpu"}):
            self.assertIsNone(pick_encoder(" V..... h264_nvenc   NVIDIA NVENC\n"))
        with mock.patch.dict("os.environ", {"NOOKCUT_ENCODER": "nvenc"}):
            self.assertIsNone(pick_encoder(""))  # forced but not in this ffmpeg build -> CPU
        self.assertEqual(encoder_args(None)["codec"], "libx264")
        for e in HW_ENCODERS.values():
            self.assertTrue(e["codec"].startswith("h264_"))  # concat copies segments: must stay H.264

    def test_gpu_failure_rerenders_everything_on_cpu(self):
        from unittest import mock
        from nookcut import render as r
        from nookcut.errors import MediaError
        used = []

        def segment(*a):
            used.append(a[10])
            if a[10]:
                raise MediaError("The GPU encoder stopped.", "")
            return f"seg{a[1]}.mov"

        edit = {"source": "in.mp4", "segments": [{}, {}, {}], "source_audio": "mute"}
        with mock.patch.object(r, "_segment", segment), mock.patch.object(r, "run"), \
                mock.patch.object(r, "find_font", return_value="font.ttf"), \
                mock.patch.object(r, "_decoded_size", return_value=(1920, 1080, None)), \
                mock.patch.object(r, "seg_duration", return_value=2.0), mock.patch.object(r, "total", return_value=6.0):
            r.render(edit, "out.mp4", None, encoder="nvenc", workers=1)
        self.assertEqual(used[0], "nvenc")
        self.assertEqual(used[1:], [None, None, None])  # every segment redone on the CPU, none mixed


PQ_STREAM = {"color_transfer": "smpte2084", "color_primaries": "bt2020", "color_space": "bt2020nc",
             "pix_fmt": "yuv420p10le"}
PQ_FRAME = {"side_data_list": [
    {"side_data_type": "Mastering display metadata", "red_x": "34000/50000", "red_y": "16000/50000",
     "green_x": "13250/50000", "green_y": "34500/50000", "blue_x": "7500/50000", "blue_y": "3000/50000",
     "white_point_x": "15635/50000", "white_point_y": "16450/50000", "min_luminance": "50/10000",
     "max_luminance": "10000000/10000"},
    {"side_data_type": "Content light level metadata", "max_content": 1000, "max_average": 400}]}


class Hdr(unittest.TestCase):
    def test_detects_hdr_and_reads_metadata(self):
        from nookcut.color import hdr_from_probe, x265_params
        self.assertIsNone(hdr_from_probe({"color_transfer": "bt709"}))
        self.assertEqual(hdr_from_probe({"color_transfer": "arib-std-b67"})["transfer"], "hlg")
        h = hdr_from_probe(PQ_STREAM, PQ_FRAME)
        self.assertEqual((h["transfer"], h["bits"]), ("pq", 10))
        self.assertEqual(h["master_display"], "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50)")
        self.assertEqual(h["max_cll"], "1000,400")
        self.assertIn("master-display=G(13250", x265_params(h))

    def test_captions_sit_at_paper_white_in_hdr(self):
        from nookcut.color import caption_color
        self.assertEqual(caption_color("0xFFD400", None), "0xFFD400")      # SDR: unchanged
        self.assertEqual(caption_color("white", {"transfer": "pq"}), "0x949494")   # 203 nits in PQ
        self.assertEqual(caption_color("white", {"transfer": "hlg"}), "0xBFBFBF")  # 75% HLG
        r, g, b = (int(caption_color("0xFFD400", {"transfer": "pq"})[i:i + 2], 16) for i in (2, 4, 6))
        self.assertTrue(r > g > b)                                                  # still yellow

    def test_color_plan(self):
        from unittest import mock
        from nookcut import render as r
        from nookcut.errors import EnvironmentNotReady
        hdr = {"transfer": "pq", "trc": "smpte2084"}
        sdr = r.color_plan({})["pre"]
        self.assertIn("out_range=tv", sdr)                     # SDR: limited range...
        self.assertNotIn("color_matrix", sdr)                  # ...without re-reading untagged colours as BT.601
        self.assertIn("out_color_matrix=bt709", r.color_plan({"source_matrix": "smpte170m"})["pre"])  # Elgato
        with mock.patch("nookcut.color.pick_tonemap", return_value="videotoolbox"):
            plan = r.color_plan({"source_hdr": hdr, "hdr": "sdr"})
            self.assertIn("scale_vt", plan["pre"])
            self.assertIn("-init_hw_device", plan["args"])
            self.assertIsNone(plan["hdr"])
            self.assertIn("scale_vt", r.color_plan({"source_hdr": hdr, "hdr": "keep"}, sdr_only=True)["pre"])
        keep = r.color_plan({"source_hdr": hdr, "hdr": "keep"})
        self.assertEqual(keep["hdr"], hdr)
        self.assertIn("yuv420p10le", keep["pre"])
        with mock.patch("nookcut.color.pick_tonemap", return_value=None), self.assertRaises(EnvironmentNotReady):
            r.color_plan({"source_hdr": hdr, "hdr": "sdr"})

    def test_hdr_segment_is_10bit_with_sound_apart(self):
        import tempfile
        from unittest import mock
        from nookcut import render as r
        from nookcut.edit import seg
        hdr = {"transfer": "pq", "trc": "smpte2084"}
        edit = {"source": "in.webm", "orientation": "vertical", "width": 1080, "height": 1920, "source_audio": "duck",
                "source_hdr": hdr, "hdr": "keep"}
        s = seg(10, 12, "highlight", top="BIG|MOMENT")
        cmds = []
        with tempfile.TemporaryDirectory() as work, mock.patch.object(r, "run", lambda c, *a, **k: cmds.append(c)):
            out = r._segment(edit, 0, s, work, "/x/Impact.ttf", None, 1920, 1080, encoder="videotoolbox",
                             look=r.color_plan(edit))
        video, sound, mux = cmds
        graph = video[video.index("-filter_complex") + 1]
        self.assertIn("overlay=0:470:format=yuv420p10", graph)
        self.assertIn("fontcolor=0x949494", graph)
        self.assertNotIn("[0:a", graph)                       # GPU encoders lose HDR metadata next to audio
        self.assertEqual(video[video.index("-c:v") + 1], "hevc_videotoolbox")
        self.assertEqual(video[video.index("-color_trc") + 1], "smpte2084")
        self.assertEqual(sound[-1], out[:-4] + ".wav")
        self.assertEqual((video[-1], mux[-1]), (out[:-4] + "_v.mov", out))  # sound muxed back per segment

    def test_dolby_vision(self):
        from nookcut.probe import dolby_vision
        rec = lambda p, c: {"side_data_list": [{"side_data_type": "DOVI configuration record", "dv_profile": p,
                                                "dv_bl_signal_compatibility_id": c}]}
        self.assertIsNone(dolby_vision({}))
        self.assertEqual(dolby_vision(rec(8, 4)), {"profile": 8, "base": "HLG"})     # iPhone: HLG picture inside
        self.assertEqual(dolby_vision(rec(8, 1))["base"], "HDR10")
        self.assertIsNone(dolby_vision(rec(5, 0))["base"])                         # no standard picture: refused

    def test_brief_hdr(self):
        self.assertEqual(Brief(title="t", source="x").hdr, "sdr")
        self.assertEqual(Brief(title="t", source="x", hdr="HDR10").hdr, "keep")
        self.assertEqual(Brief(title="t", source="x", hdr=True).hdr, "keep")


class Scenes(unittest.TestCase):
    def test_cuts_are_parsed_and_merged(self):
        from nookcut.analyze import merge_cuts
        text = ("frame:70 pts:70\nlavfi.scd.time=7\nframe:71\nlavfi.scd.time=7.2\n"
                "lavfi.scd.time=11.3\nlavfi.scd.time=16.30\n")
        self.assertEqual(merge_cuts(text), [7.0, 11.3, 16.3])   # 7.0 and 7.2 are one transition (a flash)
        self.assertEqual(merge_cuts(""), [])

    def test_segments_never_flash_another_shot(self):
        from nookcut.edit import seg
        from nookcut.story import clear_of_cuts
        e = {"segments": [seg(9.5, 12.5, "highlight"),    # cut 0.5 s after the start: start on the new shot
                          seg(20.0, 23.0, "highlight"),   # cut 0.3 s before the end: stop before it
                          seg(30.0, 33.0, "highlight"),   # cut in the middle: a real cut, keep it
                          seg(40.0, 41.2, "highlight"),   # would get shorter than 1 s: leave as is
                          seg(50.0, 52.0, "death")]}      # frame-exact: never moved
        clear_of_cuts(e, [10.0, 22.7, 31.5, 40.4, 50.3])
        self.assertEqual([(x["src_in"], x["src_out"]) for x in e["segments"]],
                         [(10.0, 12.5), (20.0, 22.7), (30.0, 33.0), (40.0, 41.2), (50.0, 52.0)])
        self.assertIs(clear_of_cuts({"cut_by": "sentences", "segments": []}, [1.0])["cut_by"], "sentences")

    def test_cuts_dont_count_as_action(self):
        from nookcut.analyze import moment_scores
        sig = fake_signals()
        k = int(150 * sig["rate"])
        sig["motion"][k] = 20.0                 # big change, but under the old z > 4 spike rule
        plain = moment_scores(sig)[k]
        sig["scenes"] = [150.0]
        self.assertLessEqual(moment_scores(sig)[k], 0.5)
        self.assertGreater(plain, 0.5)


class Bars(unittest.TestCase):
    def test_letterbox_is_cut_and_dark_scenes_are_not(self):
        from nookcut.analyze import content_box, parse_bars
        W, H = 3840, 2160
        box = lambda t, x, y, w, h: [t, x, y, w, h]
        letterbox = [box(t / 2, 0, 264, 3840, 1632) for t in range(20)]
        letterbox[3] = box(1.5, 900, 600, 1200, 900)              # a dark frame reports a small box
        letterbox[5] = box(2.5, 0, 264, 3840, 1890)                # subtitles inside the bottom bar
        self.assertEqual(content_box(letterbox, 0, 10, W, H), [0, 264, 3840, 1896])  # top cut, subtitles kept
        wall = [box(t / 2, 400, 0, 3440, 2160) for t in range(20)]                   # a dark wall on one side
        self.assertIsNone(content_box(wall, 0, 10, W, H))
        pillar = [box(t / 2, 480, 0, 2880, 2160) for t in range(20)]                 # 4:3 inside 16:9
        self.assertEqual(content_box(pillar, 0, 10, W, H), [480, 0, 2880, 2160])
        once = [box(t / 2, 0, 0, 3840, 2160) for t in range(100)]                    # bars in one shot only
        once[:10] = [box(t / 2, 0, 300, 3840, 1560) for t in range(10)]
        self.assertIsNone(content_box(once, 0, 5, W, H))
        thin = [box(t / 2, 0, 6, 3840, 2148) for t in range(20)]                     # 6 px is not a bar
        self.assertIsNone(content_box(thin, 0, 10, W, H))
        self.assertIsNone(content_box(letterbox, 50, 60, W, H))                      # no samples there
        text = "frame:0 pts:0 pts_time:0\nlavfi.cropdetect.w=-3838\nlavfi.cropdetect.h=18\nlavfi.cropdetect.x=3840\n" \
               "lavfi.cropdetect.y=2136\nframe:1 pts:1 pts_time:0.5\nlavfi.cropdetect.w=3840\nlavfi.cropdetect.h=1632\n" \
               "lavfi.cropdetect.x=0\nlavfi.cropdetect.y=264\n"
        self.assertEqual(parse_bars(text), [[0.5, 0, 264, 3840, 1632]])             # empty frame dropped

    def test_wide_output_keeps_bars(self):
        from nookcut.story import crop_bars
        from nookcut.edit import seg
        a = {"signals": {"bars": [[t / 2, 0, 264, 3840, 1632] for t in range(20)],
                         "info": {"width": 3840, "height": 2160}}}
        e = crop_bars({"orientation": "vertical", "segments": [seg(1, 5, "highlight")]}, a)
        self.assertEqual(e["segments"][0]["crop"], [0, 264, 3840, 1632])
        e = crop_bars({"orientation": "wide", "segments": [seg(1, 5, "highlight")]}, a)
        self.assertNotIn("crop", e["segments"][0])


class Checks(unittest.TestCase):
    def test_metadata_parsing(self):
        from nookcut import qc
        text = "lavfi.black_start=1.5\nlavfi.black_end=2.25\nlavfi.black_start=9.0\nlavfi.r128.I=-inf\nlavfi.r128.I=-14.2\n"
        self.assertEqual(qc._pairs(text, "lavfi.black_start", "lavfi.black_end"), [[1.5, 2.25], [9.0, None]])
        self.assertEqual(qc._metadata(text, "lavfi.r128.I"), [-14.2])          # -inf at the start is skipped
        self.assertEqual(qc.spread(10, 4), [1.25, 3.75, 6.25, 8.75])

    def test_verify_reports_what_a_viewer_would_notice(self):
        from unittest import mock
        from nookcut import qc
        from nookcut.edit import seg
        edit = {"width": 1080, "height": 1920, "fps": "60", "orientation": "vertical", "music": "ai",
                "segments": [seg(0, 10, "highlight"), seg(20, 30, "end")]}
        probe = {"format": {"duration": "20.0"}, "streams": [
            {"codec_type": "video", "width": 1080, "height": 1920, "avg_frame_rate": "60/1", "codec_name": "h264",
             "color_transfer": "bt709"}, {"codec_type": "audio"}]}
        found = {"black": [[4.0, 5.2], [19.5, None]], "frozen": [], "lufs": -21.3, "true_peak": -0.4, "silent": []}
        with mock.patch.object(qc, "_probe", return_value=probe), mock.patch.object(qc, "scan", return_value=found), \
                mock.patch.object(qc, "frames", return_value="check.jpg") as grab:
            out = qc.verify(edit, "out.mp4", "check.jpg")
        self.assertFalse(out["ok"])
        self.assertEqual(len(out["issues"]), 3)                  # black mid-video, too quiet, true peak
        self.assertIn("4.0-5.2", out["issues"][0])               # the end card's fade-out isn't an issue
        self.assertEqual(grab.call_args[0][2], [5.0, 15.0])      # one frame per segment, in the middle
        probe["streams"][0]["color_transfer"] = "smpte2084"
        with mock.patch.object(qc, "_probe", return_value=probe), \
                mock.patch.object(qc, "scan", return_value={**found, "black": [], "lufs": -14, "true_peak": -2}), \
                mock.patch.object(qc, "frames", return_value="check.jpg"):
            self.assertEqual(qc.verify(edit, "out.mp4", "check.jpg")["issues"],
                             ["colour is tagged smpte2084, expected bt709"])


class Broll(unittest.TestCase):
    def setUp(self):
        import tempfile, os
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "clips")
        os.makedirs(os.path.join(self.dir, "more"))
        for name in ("boss fight.mp4", "pillar.png", "more/map-overview.mp4"):
            open(os.path.join(self.dir, name), "w").close()
        open(os.path.join(self.tmp.name, "secret.mp4"), "w").close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_files_inside_the_folder(self):
        from nookcut.broll import resolve
        from nookcut.errors import InputError
        self.assertTrue(resolve(self.dir, "more/map-overview.mp4").endswith("map-overview.mp4"))
        for bad in ("../secret.mp4", "/etc/hosts", "missing.mp4"):
            with self.assertRaises(InputError):
                resolve(self.dir, bad)

    def test_placed_where_the_speaker_names_it(self):
        from nookcut.broll import auto_place, tokens
        from nookcut.edit import seg
        self.assertEqual(tokens("Boss_Fight-final 2.mp4"), {"boss", "fight"})
        words = lambda t, text: [[t + k * 0.4, t + k * 0.4 + 0.35, w] for k, w in enumerate(text.split())]
        e = {"segments": [seg(0, 3, "hook"), seg(5, 10, "highlight"), seg(12, 17, "highlight"),
                          seg(20, 25, "highlight"), seg(30, 40, "end")]}
        e["segments"][0]["words"] = words(0, "the boss is here")         # the hook stays on the speaker
        e["segments"][1]["words"] = words(5, "find the bosses first")    # plural matches
        e["segments"][2]["words"] = words(12, "another boss then the map")  # boss used already: map instead
        e["segments"][3]["words"] = words(20, "nothing to show here")
        files = [{"file": "boss fight.mp4"}, {"file": "more/map-overview.mp4"}, {"file": "pillar.png"}]
        notes = auto_place(e, files)
        b1, b2 = e["segments"][1]["broll"], e["segments"][2]["broll"]
        self.assertEqual((b1["file"], b1["start"], b1["end"]), ("boss fight.mp4", 0.8, 4.8))   # from "bosses", the third word
        self.assertEqual(b2["file"], "more/map-overview.mp4")
        self.assertNotIn("broll", e["segments"][0])
        self.assertNotIn("broll", e["segments"][3])
        self.assertEqual(len(notes), 2)
        self.assertIn("says 'bosses'", notes[0])

    def test_common_words_name_nothing(self):
        from nookcut.broll import auto_place
        from nookcut.edit import seg
        e = {"segments": [seg(k * 10, k * 10 + 5, "highlight") for k in range(5)]}
        for k, sg in enumerate(e["segments"]):
            sg["words"] = [[k * 10.0, k * 10.0 + 0.3, "you"], [k * 10.0 + 0.4, k * 10.0 + 0.7, "won"]]
        self.assertEqual(auto_place(e, [{"file": "you-won.mp4"}]), [])   # "you" is stop-listed, "won" said 5 times

    def test_wide_inset_stays_above_the_captions(self):
        from nookcut import render as r
        from unittest import mock
        info = {"still": False, "duration": 5.0, "hdr": False, "matrix": None}
        edit = {"broll_dir": self.dir}
        s = {"broll": {"file": "boss fight.mp4", "layout": "inset", "main": "source"}}
        with mock.patch.object(r.br, "_probe_one", return_value=info):
            _, steps, _ = r._broll(edit, s, (1920, 1080, 0, 0), "60", 4.0, [], r._layout({"orientation": "wide"}))
        y = int(steps.split("overlay=")[1].split(":")[1])
        self.assertLess(y + 410 + 8, 930)                         # box bottom above the caption line

    def test_cover_is_capped(self):
        from nookcut.broll import auto_place
        from nookcut.edit import seg
        e = {"segments": [seg(k * 10, k * 10 + 5, "highlight") for k in range(6)]}
        for k, sg in enumerate(e["segments"]):
            sg["words"] = [[k * 10.0, k * 10.0 + 0.3, f"clip{k}"]]
        notes = auto_place(e, [{"file": f"clip{k}.mp4"} for k in range(6)])
        self.assertLessEqual(len(notes) * 4.0, 0.35 * 30 + 0.01)   # at most 35% of the video

    def test_validation(self):
        from nookcut.broll import check
        self.assertEqual(check({"file": "pillar.png", "layout": "inset", "main": "source", "start": 0, "end": 2},
                               self.dir, 3.0), [])
        errs = check({"file": "../secret.mp4", "layout": "tiled", "start": 2, "end": 9}, self.dir, 3.0)
        self.assertEqual(len(errs), 3)

    def test_layouts_render_over_the_picture(self):
        import tempfile
        from unittest import mock
        from nookcut import render as r
        from nookcut.edit import seg
        from nookcut.errors import InputError
        edit = {"source": "in.mp4", "orientation": "vertical", "width": 1080, "height": 1920, "fps": "60",
                "source_audio": "duck", "broll_dir": self.dir, "source_hdr": None}
        info = {"still": False, "duration": 2.0, "hdr": False, "matrix": None}
        for layout, main, expect in (("cover", "broll", ["[base][br]overlay=0:470"]),
                                     ("split", "broll", ["[fg2]crop=540:900", "overlay=540:470"]),
                                     ("inset", "source", ["pad=iw+8:ih+8"]),
                                     ("inset", "broll", ["[fg2]scale=410:340"])):
            s = seg(10, 14, "highlight")
            s["broll"] = {"file": "boss fight.mp4", "layout": layout, "main": main, "start": 1.0, "end": 3.0}
            cmds = []
            with tempfile.TemporaryDirectory() as work, mock.patch.object(r, "run", lambda c, *a, **k: cmds.append(c)), \
                    mock.patch.object(r.br, "_probe_one", return_value=info):
                r._segment(edit, 0, s, work, "/x/Impact.ttf", None, 1920, 1080, look=r.color_plan(edit))
            cmd = cmds[0]
            graph = cmd[cmd.index("-filter_complex") + 1]
            for piece in expect + ["enable='between(t,1.000,3.000)'", "[0:a:0]atempo"]:
                self.assertIn(piece, graph, layout)
            self.assertIn("-stream_loop", cmd)                     # a short clip loops through its window
        s["broll"] = {"file": "pillar.png", "layout": "cover"}
        with tempfile.TemporaryDirectory() as work, mock.patch.object(r, "run", lambda c, *a, **k: cmds.append(c)), \
                mock.patch.object(r.br, "_probe_one", return_value={**info, "still": True}):
            r._segment(edit, 0, s, work, "/x/Impact.ttf", None, 1920, 1080, look=r.color_plan(edit))
        self.assertIn("-loop", cmds[-1])
        self.assertIn("crop=1080:900:x='(iw-ow)*min(1", cmds[-1][cmds[-1].index("-filter_complex") + 1])  # drifts
        hdr = {**edit, "source_hdr": {"transfer": "pq", "trc": "smpte2084"}, "hdr": "keep"}
        with tempfile.TemporaryDirectory() as work, self.assertRaises(InputError):
            r._segment(hdr, 0, s, work, "/x/Impact.ttf", None, 1920, 1080, look=r.color_plan(hdr))


class Framing(unittest.TestCase):
    """framing "full": a window of the output's shape fills the frame, for any recording size."""

    def test_window_fits_any_shape(self):
        from nookcut.render import window
        self.assertEqual(window(1920, 1080, 1080, 1920), (606, 1080))     # 16:9 into 9:16: full height
        self.assertEqual(window(1280, 720, 1080, 1920), (404, 720))
        self.assertEqual(window(3840, 1080, 1080, 1920), (606, 1080))     # 32:9 ultrawide
        self.assertEqual(window(1080, 2340, 1080, 1920), (1080, 1920))    # tall phone: top and bottom cut
        self.assertEqual(window(1080, 1920, 1920, 1080), (1080, 606))     # vertical into wide
        self.assertEqual(window(1920, 1080, 1080, 1920, 2.0), (302, 540))  # zoom frames tighter

class Voiceover(unittest.TestCase):
    def test_command_engine_runs_without_a_shell(self):
        import os
        from unittest import mock
        from nookcut import voice
        cmds = []

        def fake_run(cmd, *a, **k):
            cmds.append(cmd)
            if cmd[0] != "ffmpeg":
                open(cmd[cmd.index("--out") + 1], "wb").close()
        line = 'He said "stop"; rm -rf ~ && $(reboot)'
        env = {"NOOKCUT_TTS": "command", "NOOKCUT_TTS_COMMAND": "mytts --file {text_file} --out {out_wav} --voice {voice}"}
        with mock.patch.dict(os.environ, env), mock.patch.object(voice, "run", fake_run):
            voice.speak(line, "am_michael", 1.1, "/tmp/never-written.wav")
        tts = cmds[0]
        self.assertEqual(tts[0], "mytts")                     # the server's program, as a list (no shell)
        self.assertNotIn(line, " ".join(tts))                 # the text only ever goes through a file
        self.assertEqual(tts[tts.index("--voice") + 1], "am_michael")

    def test_settings_file(self):
        import os, stat, tempfile
        from unittest import mock
        from nookcut import config
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "PATH", os.path.join(d, "config.env")):
            config.write("NOOKCUT_TTS", "command")
            config.write("NOOKCUT_TTS_COMMAND", "py say.py --out {out_wav}")
            with open(config.PATH, "a") as f:
                f.write("PATH=/evil\n# NOOKCUT_FONT=commented\n")
            self.assertEqual(config.read(), {"NOOKCUT_TTS": "command", "NOOKCUT_TTS_COMMAND": "py say.py --out {out_wav}"})
            self.assertEqual(stat.S_IMODE(os.stat(config.PATH).st_mode), 0o600)
            with mock.patch.dict(os.environ, {"NOOKCUT_TTS": "off"}, clear=False):
                config.load()
                self.assertEqual(os.environ["NOOKCUT_TTS"], "off")          # the environment wins
            os.environ.pop("NOOKCUT_TTS_COMMAND", None)
            with self.assertRaises(ValueError):
                config.write("PATH", "/evil")

    def test_kokoro_command(self):
        import shlex
        from nookcut import voice
        cmd = shlex.split(voice.kokoro_command("/my py/python", "/models dir"))
        self.assertEqual(cmd[0], "/my py/python")                            # paths with spaces stay one argument
        self.assertTrue(cmd[1].endswith("adapters/kokoro_say.py"))
        self.assertEqual(cmd[cmd.index("--models") + 1], "/models dir")
        self.assertIn("{language}", cmd)

    def test_voice_choice(self):
        from unittest import mock
        from nookcut import voice
        fake = [{"name": "Bad News", "language": "en_US"}, {"name": "Eddy (English (US))", "language": "en_US"},
                {"name": "Daniel (English (UK))", "language": "en_GB"}, {"name": "Paulina", "language": "es_MX"}]
        with mock.patch.object(voice, "voices", return_value=fake), mock.patch.object(voice, "engine", return_value="system"):
            self.assertEqual(voice.pick_voice("en"), "Daniel (English (UK))")    # no novelty or robotic voices
            self.assertEqual(voice.pick_voice("es"), "Paulina")
            self.assertIsNone(voice.pick_voice("bn"))                            # none: skipped with a note
            self.assertEqual(voice.pick_voice("en", "daniel"), "Daniel (English (UK))")

    def test_captions_read_aloud(self):
        from nookcut.pipeline import spoken
        self.assertEqual(spoken("RATE THIS|1-10"), "Rate this 1 to 10.")
        self.assertEqual(spoken("ATTEMPT #18"), "Attempt number 18.")
        self.assertEqual(spoken("WHICH PART WAS BEST?"), "Which part was best?")

    def test_mix_ducks_the_bed_under_the_voice(self):
        from nookcut.render import _mix_graph
        cmd = []
        g = _mix_graph(cmd, {"source_audio": "duck"}, True, "music.wav", "voice.wav")
        self.assertEqual(cmd, ["-i", "music.wav", "-i", "voice.wav"])
        self.assertIn("[2:a]asplit[key][v];[bed][key]sidechaincompress", g)
        self.assertTrue(g.endswith("loudnorm=I=-14:TP=-2:LRA=11,aresample=48000[a]"))
        cmd = []
        self.assertEqual(_mix_graph(cmd, {"source_audio": "mute"}, False, None, "voice.wav"),
                         "[1:a]loudnorm=I=-14:TP=-2:LRA=11,aresample=48000[a]")   # voice only
        self.assertNotIn("sidechain", _mix_graph([], {"source_audio": "duck"}, True, "m.wav", None))

    def test_lines_are_placed_and_long_ones_reported(self):
        import tempfile, os
        from unittest import mock
        from nookcut import pipeline as pl
        from nookcut.edit import seg
        from nookcut.tasks import Task
        e = {"language": "en", "voice": {"name": "", "speed": 1.0}, "template": "highlights",
             "segments": [seg(0, 3, "hook"), seg(10, 14, "highlight"), seg(20, 22, "end")]}
        e["segments"][0]["vo"] = "Short line."
        e["segments"][2]["vo"] = "A very long line that cannot possibly fit into two seconds of video at all."
        lengths = {"Short line.": 1.0}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(pl.vc, "pick_voice", return_value="Samantha"), \
                mock.patch.object(pl.vc, "cached", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(pl.qc, "_probe", side_effect=lambda f, t: {"format": {"duration": lengths.get(f, 5.0)}}), \
                mock.patch.object(pl, "run"):
            out = pl._voice(d, e, Task(track=False))
        first, last = out["lines"]
        self.assertEqual((first["start"], first["tempo"]), (0.1, 1.0))
        self.assertEqual(last["tempo"], 1.25)                        # sped up as far as allowed...
        self.assertIn("runs", out["warnings"][0])                    # ...then reported, never cut
        pl._vo_words(e, out["lines"])
        self.assertEqual([w[2] for w in e["segments"][0]["_vo_words"]], ["Short", "line."])


class Script(unittest.TestCase):
    def test_lines_and_how_they_are_said(self):
        from nookcut import script as sc
        from nookcut.errors import InputError
        text = "First line.\n\n  Second   line, with spaces.\n" + "Long sentence here. " * 15
        out = sc.lines(text)
        self.assertEqual(out[:2], ["First line.", "Second line, with spaces."])
        self.assertTrue(all(len(x) <= 220 for x in out))                 # a long line is split at sentence ends
        self.assertEqual(sc.say_form("Rate it 1-10 & try #3"), "Rate it 1 to 10 and try number 3")
        with self.assertRaises(InputError):
            sc.lines("   ")
        self.assertEqual(sc.headline("When he jumps, roll forward."), "WHEN HE JUMPS")

    def _edit(self, files, words_per_line):
        from nookcut import script as sc
        from nookcut.brief import Brief
        b = Brief(title="Beat the boss", source="base.mp4", broll_dir="")
        placed, at = [], 0.0
        for text in words_per_line:
            length = len(text.split()) * 0.4
            placed.append({"text": text, "start": at, "end": at + length, "words": sc.word_times(text, at, length)})
            at += length + sc.GAP
        return sc.build_edit(b, {"duration": at + 0.5, "fps": 30.0}, placed, files, "60")

    def test_broll_per_line(self):
        files = [{"file": "boss-arena.mp4", "still": False, "duration": 30.0, "width": 1920, "height": 1080},
                 {"file": "phone.mp4", "still": False, "duration": 5.0, "width": 1080, "height": 1920},
                 {"file": "map.png", "still": True, "duration": None, "width": 1600, "height": 900}]
        e, notes = self._edit(files, ["Most players lose to the boss.", "Here is the trick.",
                                      "Open the map first.", "The boss arena has pillars.", "Tell me your time."])
        segs = e["segments"]
        self.assertEqual([x.get("broll", {}).get("file") for x in segs],
                         ["boss-arena.mp4", "phone.mp4", "map.png", "boss-arena.mp4", None])
        self.assertEqual(segs[0]["broll"]["layout"], "fit")                # landscape in vertical: shown whole
        self.assertEqual(segs[1]["broll"]["layout"], "cover")              # portrait fills the frame
        self.assertGreater(segs[3]["broll"]["in"], 0)                      # named again: carries on in the clip
        self.assertEqual(segs[4]["top"].replace("|", " "), "TELL ME YOUR TIME")   # nothing left: its words
        self.assertEqual(segs[0]["kind"], "hook")
        self.assertEqual(segs[0]["top"], "BEAT THE BOSS")
        for a, b in zip(segs, segs[1:]):
            self.assertAlmostEqual(a["src_out"], b["src_in"], places=3)    # cut at the pauses, nothing lost
        self.assertTrue(all(x["words"] for x in segs) and e["subtitles"])
        _, none = self._edit([], ["One.", "Two."])
        self.assertIn("no B-roll folder", none[0])

    def test_script_projects_refuse_analyze_and_plan(self):
        import tempfile, os
        from nookcut import pipeline as pl
        from nookcut.brief import Brief
        from nookcut.errors import InputError
        with tempfile.TemporaryDirectory() as d:
            Brief(title="t", source="base.mp4", made_from="script").save(os.path.join(d, "brief.json"))
            for step in (lambda: pl.run_analysis(d), lambda: pl.make_plan(d)):
                with self.assertRaises(InputError):
                    step()


class StylePacks(unittest.TestCase):
    def test_packs_are_checked_installed_and_used(self):
        import json, os, tempfile
        from unittest import mock
        from nookcut import copywriting as cw
        self.assertEqual(cw.check_pack(cw._read(os.path.join(cw.BUILTIN, "en.json"))), [])   # slot list complete
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"NOOKCUT_COPY_DIR": d}):
            good = os.path.join(d, "mine.json")
            with open(good, "w") as f:
                json.dump({"name": "Boss Fights!", "version": "1", "captions": {"hook.comeback": ["{n} DEATHS|FOR THIS"]},
                           "titles": {"comeback": ["{title} after {n} deaths"]}}, f)
            self.assertEqual(cw.install_pack(good), "boss-fights")
            self.assertEqual([p["name"] for p in cw.list_packs()], ["boss-fights"])
            data, code, note = cw.load("en", "boss-fights")
            self.assertEqual(data["captions"]["hook.comeback"], ["{n} DEATHS|FOR THIS"])    # the pack's keys win
            self.assertIn("death.top", data["captions"])                                     # the rest stays
            self.assertEqual((code, note), ("en", ""))
            self.assertIn("isn't installed", cw.load("en", "nope")[2])
            bad = os.path.join(d, "bad.json")
            with open(bad, "w") as f:
                json.dump({"captions": {"hook.x": ["{player}"]}, "extra": 1}, f)
            with self.assertRaises(ValueError):
                cw.install_pack(bad)


class FrameRate(unittest.TestCase):
    def test_output_rate_follows_the_source_family(self):
        from nookcut.edit import fps_value, output_fps
        self.assertEqual(output_fps(59.94006), "60000/1001")
        self.assertEqual(output_fps(120), "60")
        self.assertEqual(output_fps(119.88, "30"), "30000/1001")
        self.assertEqual(output_fps(50), "50")
        self.assertEqual(output_fps(25, "30"), "25")
        self.assertAlmostEqual(fps_value({"fps": "60000/1001"}), 59.94, places=2)
        self.assertEqual(fps_value({}), 60.0)                  # edits from before keep 60

    def test_slow_motion_uses_real_frames_when_there_are_enough(self):
        from nookcut.render import needs_interpolation
        slow = {"speed": 0.5}
        self.assertFalse(needs_interpolation({"source_fps": 120, "fps": "60"}, slow))
        self.assertTrue(needs_interpolation({"source_fps": 60, "fps": "60"}, slow))
        self.assertFalse(needs_interpolation({"source_fps": 59.94, "fps": "30000/1001"}, slow))
        self.assertFalse(needs_interpolation({"source_fps": 30, "fps": "60"}, {"speed": 1.0}))
        self.assertTrue(needs_interpolation({"source_fps": 240, "fps": "60"}, {"speed": 0.2}))

    def test_key_moments_as_text(self):
        from nookcut.brief import moment
        self.assertEqual(moment("10:42.5=final blow"), {"t": 642.5, "label": "final blow"})
        self.assertEqual(moment("90"), {"t": 90.0})
        self.assertEqual(moment({"t": "1:00", "label": "x"}), {"t": 60.0, "label": "x"})
        b = Brief(title="t", source="x", key_moments="0:30=win")
        self.assertEqual(b.key_moments, [{"t": 30.0, "label": "win"}])
        with self.assertRaises(ValueError):
            moment("final blow")
        self.assertFalse(hasattr(Brief(title="t", source="x"), "spoilers"))

    def test_brief_fps(self):
        self.assertEqual(Brief(title="t", source="x").fps, "auto")
        self.assertEqual(Brief(title="t", source="x", fps=30).fps, "30")
        self.assertEqual(Brief(title="t", source="x", fps="59.94").fps, "60")
        with self.assertRaises(ValueError):
            Brief(title="t", source="x", fps="24").validate()


class Tasks(unittest.TestCase):
    def test_ffmpeg_errors_are_readable(self):
        from nookcut.errors import explain_ffmpeg
        e = explain_ffmpeg("[mov,mp4] moov atom not found\nin.mp4: Invalid data found when processing input",
                           "reading the source")
        self.assertEqual(e.code, "media")
        self.assertIn("damaged or incomplete", e.message)
        self.assertTrue(e.hint)
        self.assertIn("moov atom", e.detail)

    def test_cancel_kills_process(self):
        import threading, time
        from nookcut.errors import Cancelled
        from nookcut.tasks import Task, run
        t = Task("t", track=False)
        threading.Timer(0.5, t.cancel).start()
        start = time.time()
        with self.assertRaises(Cancelled):
            run(["ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i", "testsrc=d=30", "-f", "null", "-"], t)
        self.assertLess(time.time() - start, 5)

    def test_one_job_per_project(self):
        import tempfile
        from nookcut.errors import Busy
        from nookcut.tasks import project_lock
        d = tempfile.mkdtemp()
        with project_lock(d, "render"):
            with self.assertRaises(Busy):
                with project_lock(d, "analyze"):
                    pass
        with project_lock(d, "analyze"):  # released after the first job
            pass

    def test_progress_snapshot(self):
        from nookcut.tasks import Task
        t = Task("render", track=False)
        t.plan(("a", 1), ("b", 3))
        t.begin("b", 10)
        t.set(5)
        snap = t.snapshot()
        self.assertEqual(snap["step"], "2/2")
        self.assertAlmostEqual(snap["percent"], 62.5)


class Service(unittest.TestCase):
    def test_service_definition_is_local_and_tokenless(self):
        from nookcut import service
        plist = service._plist()
        self.assertIn("<string>127.0.0.1</string>", plist)
        self.assertIn("NOOKCUT_TOKEN_FILE", plist)
        self.assertNotIn("NOOKCUT_TOKEN<", plist)  # the token itself never goes in the plist
        self.assertIn("--transport", service._unit())


class Aim(unittest.TestCase):
    """Shots aimed at the action: where the picture changes, from the 64x36 scan."""

    def test_scan_finds_where_things_move(self):
        from nookcut.analyze import _Frames, W, H
        f = _Frames()
        frames = []
        for k in range(6):
            b = bytearray(W * H * 3)
            for y in range(10, 20):
                for x in range(44 + k, 50 + k):
                    b[(y * W + x) * 3 + 1] = 255
            frames.append(bytes(b))
        f.feed(b"".join(frames))
        self.assertIsNone(f.where[0])                       # nothing to compare the first frame with
        x, y, spread = f.where[3]
        self.assertAlmostEqual(x, 0.75, delta=0.05)
        self.assertAlmostEqual(y, 0.42, delta=0.05)
        self.assertLess(spread, 0.1)
        self.assertEqual(len(f.grid()["motion"]), 16 * 9)

    def test_aim_follows_one_spot_and_ignores_camera_turns(self):
        from nookcut.story import aim
        spot = [[0.8, 0.5, 0.05]] * 10
        self.assertAlmostEqual(aim(spot, 2.0, 0, 5)[0], 0.5 + 0.3 * 0.85, places=3)
        self.assertIsNone(aim([[0.8, 0.5, 0.41]] * 10, 2.0, 0, 5))   # motion all over: keep the middle
        self.assertIsNone(aim([None] * 10, 2.0, 0, 5))                 # nothing moves
        self.assertIsNone(aim(None, 2.0, 0, 5))                        # an analysis from before aiming
        # with black bars cut, the aim is a fraction of the picture inside them
        x, _ = aim(spot, 2.0, 0, 5, crop=[240, 0, 1440, 1080], size=(1920, 1080))
        self.assertAlmostEqual(x, 0.5 + ((0.8 * 1920 - 240) / 1440 - 0.5) * 0.85, places=3)

    def test_aim_skips_the_facecam_and_hud_corners(self):
        from nookcut.story import aim
        corner = [[0.9, 0.85, 0.05]] * 10                            # a facecam or kill feed moving alone
        self.assertIsNone(aim(corner, 2.0, 0, 5))
        panel = [[0.5, 0.1, 0.05]] * 10                              # inside the split layout's panel box
        self.assertIsNone(aim(panel, 2.0, 0, 5, avoid=[[0.4, 0.0, 0.2, 0.2]]))
        self.assertIsNotNone(aim(panel, 2.0, 0, 5))

    def test_scan_leaves_out_ignored_boxes(self):
        from nookcut.analyze import _Frames, W, H
        f = _Frames(ignore=[[0.75, 0.0, 0.25, 0.3]])                 # the facecam's box
        frames = []
        for k in range(3):
            b = bytearray(W * H * 3)
            for y in range(2, 8):
                for x in range(52 + k, 60 + k):
                    b[(y * W + x) * 3 + 1] = 255
            frames.append(bytes(b))
        f.feed(b"".join(frames))
        self.assertEqual(f.where[1:], [None, None])                  # only the facecam moved
        self.assertGreater(max(f.grid()["motion"]), 0)               # the grid still sees it (not a map)

    def test_auto_focus_keeps_a_hand_set_focus(self):
        from nookcut.story import auto_focus
        from nookcut.edit import seg
        a, b, c = seg(0, 4, "highlight"), seg(5, 9, "highlight"), seg(10, 14, "highlight")
        b["focus"] = [0.2, 0.5]                                       # set by the agent
        c["focus"], c["focus_by"] = [0.9, 0.5], "auto"                # set automatically, cut since moved
        where = [[0.8, 0.5, 0.05]] * 20 + [[0.5, 0.5, 0.4]] * 10
        edit = {"segments": [a, b, c]}
        auto_focus(edit, {"signals": {"where": where, "rate": 2.0, "info": {"width": 1920, "height": 1080}}})
        self.assertEqual(a["focus_by"], "auto")
        self.assertEqual(b["focus"], [0.2, 0.5])
        self.assertNotIn("focus", c)                                  # nothing to aim at any more: the middle


class Split(unittest.TestCase):
    """framing "split": facecam (or the minimap) on top, the action below."""

    def test_minimap_is_the_still_corner(self):
        from nookcut.analyze import hud_corner
        M, B = [3.0] * 144, [100] * 144
        for y in range(6, 9):
            for x in range(13, 16):
                M[y * 16 + x] = 0.2
        found = hud_corner({"cols": 16, "rows": 9, "motion": M, "bright": B})
        self.assertEqual(found["corner"], "bottom-right")
        self.assertEqual(found["box"], [0.7812, 0.6111, 0.2188, 0.3889])   # half a block of margin inward
        self.assertIsNone(hud_corner({"cols": 16, "rows": 9, "motion": [3.0] * 144, "bright": B}))  # all moves
        dark = [0] * 144                                              # still and black: bars, not a map
        self.assertIsNone(hud_corner({"cols": 16, "rows": 9, "motion": M, "bright": dark}))

    def test_several_still_corners_go_to_the_usual_map_corner(self):
        from nookcut.analyze import hud_corner
        M, B = [3.0] * 144, [100] * 144
        for y in (0, 1, 7, 8):
            for x in (0, 1, 14, 15):
                M[y * 16 + x] = 0.2                                  # four equal still corners
        found = hud_corner({"cols": 16, "rows": 9, "motion": M, "bright": B})
        self.assertEqual(found["corner"], "top-left")
        self.assertEqual(len(found["others"]), 3)

    def test_panel_prefers_the_facecam(self):
        import tempfile
        from nookcut.story import panel_of
        b = Brief(title="t", source="/x.mov", framing="split", facecam="0.7,0.6,0.3,0.4")
        self.assertEqual(panel_of(b, None), {"kind": "facecam", "box": [0.7, 0.6, 0.3, 0.4], "by": "user", "style": "card"})
        with tempfile.NamedTemporaryFile(suffix=".mp4") as cam:
            b = Brief(title="t", source="/x.mov", framing="split", facecam=cam.name, facecam_offset=2.5)
            p = panel_of(b, None)
            self.assertEqual((p["kind"], p["file"], p["offset"]), ("facecam", cam.name, 2.5))
        b = Brief(title="t", source="/x.mov", framing="split")
        self.assertEqual(panel_of(b, None)["by"], "default")          # no facecam, no analysis: top-left map
        b = Brief(title="t", source="/x.mov", framing="split", minimap="0.8,0,0.2,0.35")
        self.assertEqual(panel_of(b, None), {"kind": "minimap", "box": [0.8, 0.0, 0.2, 0.35], "by": "user", "style": "card"})

    def test_brief_checks_split(self):
        self.assertEqual(Brief(title="t", source="/x", framing="facecam").framing, "split")
        with self.assertRaises(ValueError):
            Brief(title="t", source="/x", framing="split", orientation="wide").validate()
        with self.assertRaises(ValueError):
            Brief(title="t", source="/x", framing="split", minimap="top left").validate()

    def test_render_stacks_the_panel_over_the_action(self):
        import tempfile
        from unittest import mock
        from nookcut import render as r
        from nookcut.edit import seg
        edit = {"source": "in.mp4", "orientation": "vertical", "width": 1080, "height": 1920, "fps": "60",
                "source_audio": "duck", "source_hdr": None, "framing": "split",
                "panel": {"kind": "minimap", "box": [0.0, 0.0, 0.2, 0.33]}}
        s = seg(10, 14, "highlight")
        s["focus"] = [0.7, 0.5]
        cmds = []
        with tempfile.TemporaryDirectory() as work, mock.patch.object(r, "run", lambda c, *a, **k: cmds.append(c)):
            r._segment(edit, 0, s, work, "/x/Impact.ttf", None, 1920, 1080, look=r.color_plan(edit))
            graph = cmds[0][cmds[0].index("-filter_complex") + 1]
            # card (default): the facecam card and the action over a blurred copy
            for piece in ("crop=w='trunc(iw*0.2/2)*2'", "force_original_aspect_ratio=decrease", "split=3[a][b][c]",
                          "scale=1080:1112", "[bg][fg]overlay=0:510", "[t0][pn]overlay=195:60[base]"):
                self.assertIn(piece, graph)
            edit["panel"]["style"] = "full"
            r._segment(edit, 0, s, work, "/x/Impact.ttf", None, 1920, 1080, look=r.color_plan(edit))
            graph = cmds[-1][cmds[-1].index("-filter_complex") + 1]
            for piece in ("[pn][fg]vstack[base]", f"scale=1080:{1920 - r.PANEL_H}"):
                self.assertIn(piece, graph)
            edit["panel"] = {"kind": "facecam", "file": "/cam.mp4", "offset": 2.0}
            with mock.patch.object(r.br, "_probe_one", return_value={"hdr": False, "matrix": None}):
                r._segment(edit, 0, s, work, "/x/Impact.ttf", None, 1920, 1080, look=r.color_plan(edit))
            cmd = cmds[-1]
            self.assertEqual(cmd[cmd.index("/cam.mp4") - 5:cmd.index("/cam.mp4")], ["-ss", "8.000", "-t", "4.000", "-i"])
            self.assertIn("[1:v]", cmd[cmd.index("-filter_complex") + 1])

    def test_edit_validates_the_panel(self):
        from nookcut.edit import validate
        edit = {"orientation": "vertical", "framing": "split", "segments": [], "panel": {"kind": "map", "box": [0, 0, 2, 1]}}
        self.assertEqual(len(validate(edit)), 2)


class Cues(unittest.TestCase):
    """The user's on-screen signs (a kill icon): every time each one shows."""

    def test_brief_reads_cues(self):
        b = Brief(title="t", source="/x", cues=["kill@1:02@0.8,0.05,0.15,0.08", {"label": "win", "t": 90, "box": "0,0,1,1"}])
        self.assertEqual(b.cues[0], {"label": "kill", "t": 62.0, "box": [0.8, 0.05, 0.15, 0.08]})
        with self.assertRaises(ValueError):
            Brief(title="t", source="/x", cues=["kill@10"])

    def test_matches_are_runs_above_the_bar(self):
        from nookcut.analyze import cue_times, parse_ssim
        self.assertEqual(parse_ssim("n:1 Y:0.41 All:0.410 (2.3)\nn:2 Y:0.95 All:0.950 (13.0)\n"), [0.41, 0.95])
        v = [0.3] * 100
        for start in (10, 50, 52, 80):          # 50 and 52 are one appearance (0.4 s apart)
            v[start] = v[start + 1] = 0.97
        out = cue_times(v, at=10 / 5)
        self.assertEqual(out["at"], [2.0, 10.0, 16.0])
        self.assertIn("note", cue_times([0.9] * 50, at=2))           # the box always looks alike

    def test_cues_raise_the_moments_around_them(self):
        from nookcut.analyze import moment_scores
        sig = {"motion": [1.0] * 40, "loudness": None, "brightness": [100] * 40, "rate": 2.0, "scenes": [],
               "cues": [{"label": "kill", "at": [10.0]}]}
        scores = moment_scores(sig)
        self.assertGreater(scores[20], scores[5] + 2)


class HookQuotes(unittest.TestCase):
    """Hooks from what is said: a short exclaimed or shouted line from later in the video."""

    def _edit(self):
        from nookcut.edit import seg
        return {"orientation": "vertical", "segments": [seg(0, 2, "hook"), seg(5, 15, "highlight"), seg(20, 30, "highlight")]}

    def test_shouted_line_wins_and_sign_offs_never_do(self):
        from nookcut.copywriting import quotes, QUOTE_MIN
        words = [[6.0, 6.3, "here"], [6.3, 6.6, "we"], [6.6, 7.0, "go"], [7.0, 7.4, "again."],
                 [21.0, 21.3, "no"], [21.3, 21.6, "way"], [21.6, 21.9, "he"], [21.9, 22.2, "missed."],
                 [25.0, 25.3, "subscribe"], [25.3, 25.6, "for"], [25.6, 26.0, "more."]]
        loud = [-30.0] * 60
        for k in range(42, 45):
            loud[k] = -18.0                                   # "no way he missed" is 12 dB louder
        out = quotes(self._edit(), words, loud, 2.0)
        self.assertEqual(out[0]["text"], "NO WAY HE MISSED")
        self.assertGreaterEqual(out[0]["score"], QUOTE_MIN)
        self.assertLess(out[1]["score"], QUOTE_MIN)            # said normally: offered, never applied
        self.assertNotIn("SUBSCRIBE FOR MORE", [q["text"] for q in out])

    def test_lines_in_the_hook_or_too_long_are_skipped(self):
        from nookcut.copywriting import quotes
        words = [[0.5, 0.8, "wow!"], [0.8, 1.0, "nice!"]] + \
                [[6 + k * 0.3, 6.3 + k * 0.3, w] for k, w in enumerate("this is a very long sentence that goes on and on".split())]
        self.assertEqual(quotes(self._edit(), words), [])


class StreamStyle(unittest.TestCase):
    """The stream-clip look: word cards, red emphasis, censoring, a context label, the facecam card,
    the one-take clip, and the user's names fixed in the transcript."""

    def test_censor_hides_swears_only(self):
        from nookcut.subtitles import censor
        self.assertEqual([censor(w) for w in ("FUCKING", "Shit!", "motherfucker", "bullshit")],
                         ["F*CKING", "Sh*t!", "motherf*cker", "bullsh*t"])
        self.assertEqual([censor(w) for w in ("Dickens", "Shitake", "fun", "ARGONAUT")],
                         ["Dickens", "Shitake", "fun", "ARGONAUT"])

    def test_cards_are_short_and_loud_words_stand_alone(self):
        from nookcut.subtitles import cards
        words = [[0, .3, "Well,"], [.4, .6, "I'm"], [.6, .8, "not"], [.8, 1.0, "gonna"], [1.0, 1.1, "be"],
                 [5, 5.3, "GG"], [5.3, 5.8, "fucking"], [5.8, 6, "GG"], [6, 6.4, "bro."]]
        out = cards(words, 7, emphasis={5})
        self.assertEqual([[w[2] for w in c[2]] for c in out],
                         [["Well,"], ["I'm", "not"], ["gonna", "be"], ["GG"], ["fucking"], ["GG", "bro."]])
        self.assertEqual([c[3] for c in out], [False, False, False, True, True, False])
        self.assertEqual(out[1][1], 0.8)                      # up until the next card: hard cuts, no gaps

    def test_streamer_ass_puts_emphasis_lower_and_draws_the_label(self):
        from nookcut.subtitles import ass
        words = [[0.2, 0.5, "that"], [0.5, 0.9, "sucks"], [2.0, 2.4, "fucking"]]
        out = ass(words, 3, 1080, 1920, 510, 110, "/x/Impact.ttf", 16, style="streamer", emphasis=[2.0],
                  emphasis_y=1344, hide_swears=True,
                  label={"text": "ARGONAUT|OPERATOR", "x": 356, "y": 754, "size": 93, "end": 1.25})
        self.assertIn("\\pos(540,510)}THAT SUCKS", out)
        self.assertIn("Loud,,0,0,0,,{\\an5\\pos(540,1344)}F*CKING", out)
        self.assertIn("ARGONAUT\\NOPERATOR", out)
        self.assertIn("\\move(356,754,356,1074,1070,1250)", out)   # swipes down and away at the end

    def test_card_geometry(self):
        from nookcut.render import split_geometry, _layout
        e = {"orientation": "vertical", "width": 1080, "height": 1920, "framing": "split", "panel": {}}
        g = split_geometry(e)
        self.assertEqual((g["panel"], g["act_y"], g["act_h"]), ((195, 60, 690, 444), 510, 1112))
        L = _layout(e)
        self.assertEqual((L["speech_mid"], L["hook_mid"], L["fg_h"]), (510, 1622, 1112))   # on the two borders
        e["panel"]["style"] = "full"
        self.assertEqual(split_geometry(e)["act_y"], 760)

    def test_terms_fix_misheard_names(self):
        from nookcut.transcribe import apply_terms
        def fixed(text):
            w = [[k, k + .5, x] for k, x in enumerate(text.split())]
            out, maybe = apply_terms(w, ["Argonaut", "Al Mazrah"])
            return " ".join(x[2] for x in out), [m["said"] for m in maybe]
        self.assertEqual(fixed("fighting an organ on it."), ("fighting an Argonaut.", []))
        self.assertEqual(fixed("fighting an Argonaut."), ("fighting an Argonaut.", []))
        self.assertEqual(fixed("two argonauts!"), ("two Argonauts!", []))
        self.assertEqual(fixed("Al Mazra is big"), ("Al Mazrah is big", []))
        self.assertEqual(fixed("killed in Harkinut."), ("killed in Harkinut.", ["Harkinut"]))   # only asked
        self.assertEqual(fixed("what an argument")[0], "what an argument")                    # a real word stays

    def test_clip_is_one_take_ending_after_the_peak(self):
        b = Brief(title="t", source="/x.mov", game="g", max_duration=20)
        a = fake_analysis()
        a["scores"] = [0.0] * len(a["scores"])
        a["scores"][int(300 * 2)] = 9.0                         # the big moment at 300 s
        e = plan(b, a, "clip")
        self.assertEqual(len(e["segments"]), 1)
        s = e["segments"][0]
        self.assertAlmostEqual(s["src_out"], 302.0, places=1)    # ends 2 s after it
        self.assertAlmostEqual(s["src_out"] - s["src_in"], 20.0, places=1)
        self.assertEqual((e["music"], s["top"], s["speed"]), ("none", "", 1.0))

    def test_shouted_words_are_marked(self):
        from nookcut.story import mark_emphasis
        from nookcut.edit import seg
        s = seg(0, 10, "highlight")
        s["words"] = [[1.0, 1.4, "that"], [1.5, 1.9, "sucks"], [6.0, 6.4, "no"], [6.5, 6.9, "way"], [8, 8.4, "wow!"]]
        loud = [-30.0] * 20
        loud[12] = loud[13] = -18.0                              # "no way" 12 dB louder
        mark_emphasis({"segments": [s]}, s["words"], {"signals": {"loudness": loud, "rate": 2.0}})
        self.assertEqual(s["emphasis"], [6.0, 6.5, 8])


class MinimapOnce(unittest.TestCase):
    """Split with the minimap on top: the action below never shows it a second time."""

    def _edit(self, box, kind="minimap"):
        return {"orientation": "vertical", "width": 1080, "height": 1920, "framing": "split",
                "source_width": 1920, "source_height": 1080, "panel": {"kind": kind, "box": box}}

    def test_window_moves_off_the_minimap(self):
        from nookcut.render import minimap_clear
        from nookcut.edit import seg
        s = seg(0, 4, "highlight")
        s["focus"] = [0.2, 0.5]                                       # aimed at the left, where the map is
        moved, patch = minimap_clear(self._edit([0, 0, 0.2, 0.33]), s, 1920, 1080, 1048, 1080)
        self.assertIsNone(patch)
        left = moved["focus"][0] * 1920 - 1048 / 2
        self.assertGreaterEqual(left, 0.2 * 1920)                     # the window starts right of the map
        self.assertEqual(s["focus"], [0.2, 0.5])                      # the edit itself is untouched

    def test_clear_or_facecam_windows_stay(self):
        from nookcut.render import minimap_clear
        from nookcut.edit import seg
        s = seg(0, 4, "highlight")
        s["focus"] = [0.7, 0.5]
        self.assertIs(minimap_clear(self._edit([0, 0, 0.2, 0.33]), s, 1920, 1080, 1048, 1080)[0], s)
        s["focus"] = [0.2, 0.5]
        self.assertIs(minimap_clear(self._edit([0, 0, 0.2, 0.33], "facecam"), s, 1920, 1080, 1048, 1080)[0], s)

    def test_minimap_is_blurred_only_when_it_cannot_be_avoided(self):
        from unittest import mock
        import tempfile
        from nookcut import render as r
        from nookcut.edit import seg
        edit = {**self._edit([0.3, 0.0, 0.4, 0.33]), "fps": "60", "source_audio": "duck", "source_hdr": None,
                "source": "in.mp4"}
        s = seg(10, 14, "highlight")
        moved, patch = r.minimap_clear(edit, s, 1920, 1080, 1048, 1080)
        self.assertEqual(patch, [576, 0, 768, 356])                   # a wide map in the middle: no way round
        cmds = []
        with tempfile.TemporaryDirectory() as work, mock.patch.object(r, "run", lambda c, *a, **k: cmds.append(c)):
            r._segment(edit, 0, s, work, "/x/Impact.ttf", None, 1920, 1080, look=r.color_plan(edit))
        graph = cmds[0][cmds[0].index("-filter_complex") + 1]
        self.assertIn("[m2]crop=768:356:576:0,gblur=sigma=28[mb];[m1][mb]overlay=576:0", graph)


class BorderCaptions(unittest.TestCase):
    """Captions centred on the action's borders, over a translucent slab."""

    def test_band_puts_captions_on_its_edges(self):
        from nookcut.render import _layout
        band = _layout({"orientation": "vertical", "width": 1080, "height": 1920})
        self.assertEqual((band["hook_mid"], band["speech_mid"]), (470, 1370))       # the band's top and bottom
        self.assertNotIn("hook_mid", _layout({"orientation": "wide", "width": 1920, "height": 1080}))

    def test_hook_block_is_centred_on_the_border_with_slabs(self):
        import tempfile
        from nookcut import render as r
        from nookcut.edit import seg
        L = r._layout({"orientation": "vertical", "width": 1080, "height": 1920})
        s = seg(0, 2, "hook", top="THIS GOT|INSANE")
        with tempfile.TemporaryDirectory() as work:
            f = r._text(work, 0, s, 2.0, L, "/x/Impact.ttf", 1080, 1920, edit={"caption_box": True})
            lines = [x for x in f if x.startswith("drawtext")]
            ys = [int(x.split(":y=")[1].split(":")[0]) for x in lines]
            self.assertEqual(ys, [470 - (140 + 120) // 2, 470 - (140 + 120) // 2 + 140])   # middle on 470
            self.assertIn("box=1:boxcolor=black@0.5", lines[0])
            plain = r._text(work, 0, s, 2.0, L, "/x/Impact.ttf", 1080, 1920, edit={"caption_box": False})
            self.assertNotIn("box=1", plain[0])

    def test_highlight_style_draws_one_slab_per_phrase(self):
        from nookcut.subtitles import ass
        out = ass([[0, .4, "lets"], [.4, .8, "go"]], 1, 1080, 1920, 1370, 112, "/x/Impact.ttf", 16, boxed=True)
        self.assertEqual(out.count("Slab,,"), 1)                       # one slab under the whole phrase
        self.assertEqual(out.count("Words,,"), 2)                      # each word highlighted in turn on top
        self.assertIn("\\an5\\pos(540,1370)", out)                     # centred on the border
        self.assertNotIn("fscx108", out)                               # no pop: it would break the slab


class NoFullBleed(unittest.TestCase):
    """One vertical video: full-bleed framing was removed and is refused with a clear message."""

    def test_full_bleed_is_refused_everywhere(self):
        import json, os, tempfile
        from nookcut import pipeline as pl
        from nookcut.edit import validate
        from nookcut.errors import InputError
        for word in ("full", "full_bleed", "full screen", "no blur"):
            with self.assertRaises(ValueError):
                Brief(title="t", source="/x", framing=word).validate()
        self.assertIn("isn't available", validate({"framing": "full", "segments": []})[0])
        with tempfile.TemporaryDirectory() as d:
            Brief(title="t", source="/x").save(os.path.join(d, "brief.json"))
            with self.assertRaises(InputError):
                pl.set_framing(d, "full screen")
            self.assertEqual(pl.set_framing(d, "minimap")["framing"], "split")


if __name__ == "__main__":
    unittest.main()
