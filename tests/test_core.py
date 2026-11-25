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
        w = Brief(title="t", source="s", max_duration=200, platforms=["youtube_shorts"]).validate()
        self.assertTrue(any("180" in x for x in w))


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

    def test_full_frame_has_no_blurred_fill_and_follows_focus(self):
        import tempfile
        from unittest import mock
        from nookcut import render as r
        from nookcut.edit import seg
        edit = {"source": "in.mp4", "orientation": "vertical", "width": 1080, "height": 1920, "fps": "60",
                "source_audio": "duck", "source_hdr": None, "framing": "full"}
        s = seg(10, 14, "highlight")
        s.update(focus=[0.2, 0.5], focus_end=[0.8, 0.5])
        cmds = []
        with tempfile.TemporaryDirectory() as work, mock.patch.object(r, "run", lambda c, *a, **k: cmds.append(c)):
            r._segment(edit, 0, s, work, "/x/Impact.ttf", None, 1280, 720, look=r.color_plan(edit))
        graph = cmds[0][cmds[0].index("-filter_complex") + 1]
        self.assertNotIn("gblur", graph)
        self.assertIn("(0.2000+(0.6000)*", graph)                    # eased pan from 0.2 to 0.8
        self.assertIn("scale=3420:1924:flags=lanczos,crop=w='min(1080,iw)'", graph)   # enlarged, then panned
        s["focus_end"] = None
        with tempfile.TemporaryDirectory() as work, mock.patch.object(r, "run", lambda c, *a, **k: cmds.append(c)):
            r._segment(edit, 0, s, work, "/x/Impact.ttf", None, 1280, 720, look=r.color_plan(edit))
        self.assertIn("crop=w='min(404,iw)':h='min(720,ih)':x='max(0,min(iw-ow,0.2000*iw-ow/2))'",
                      cmds[-1][cmds[-1].index("-filter_complex") + 1])

    def test_focus_is_checked(self):
        edit = {"framing": "full", "segments": [{"src_in": 0, "src_out": 2, "speed": 1, "kind": "hook",
                                                 "focus": [1.4, 0.5], "focus_end": "left"}]}
        errs = validate(edit)
        self.assertEqual(len(errs), 2)
        self.assertIn("focus must be [x, y]", errs[0])
        self.assertIn("framing", validate({"framing": "zoomed", "segments": []})[0])
        self.assertEqual(Brief(title="t", source="s", framing="full_bleed").framing, "full")
        self.assertEqual(Brief(title="t", source="s").framing, "band")

    def test_user_can_switch_framing_after_planning(self):
        import json, os, tempfile
        from nookcut import pipeline as pl
        from nookcut.errors import InputError
        with tempfile.TemporaryDirectory() as d:
            Brief(title="t", source="s").save(os.path.join(d, "brief.json"))
            self.assertEqual(pl.set_framing(d, "full screen")["framing"], "full")   # no edit yet: brief only
            with open(os.path.join(d, "edit.json"), "w") as f:
                json.dump({"template": "highlights", "framing": "full", "segments": []}, f)
            out = pl.set_framing(d, "blur")
            self.assertEqual(out["framing"], "band")
            self.assertEqual(json.load(open(os.path.join(d, "edit.json")))["framing"], "band")
            self.assertEqual(Brief.load(os.path.join(d, "brief.json")).framing, "band")
            with self.assertRaises(InputError):
                pl.set_framing(d, "sideways")

    def test_resolve_starts_each_shot_at_its_focus(self):
        from nookcut.export import resolve_frames
        edit = {"framing": "full", "width": 1080, "height": 1920, "source_width": 1920, "source_height": 1080,
                "segments": [{"zoom": 1.0}, {"zoom": 1.0, "focus": [0.25, 0.5]}, {"zoom": 1.0, "focus": [0.0, 0.5]}]}
        mid, left, edge = resolve_frames(edit, 1.48)
        self.assertEqual(mid[1:], (0.0, 0.0))
        self.assertAlmostEqual(mid[0], 3.1683, places=3)              # fit to width, then fill the height
        self.assertGreater(left[1], 0)                                 # aim left: the picture moves right
        self.assertAlmostEqual(edge[1], (960 - 303) * 1080 / 606, places=0)   # held inside the picture
        self.assertEqual(resolve_frames({**edit, "framing": "band"}, 1.48)[0], (1.48, 0.0, 0.0))


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


if __name__ == "__main__":
    unittest.main()
