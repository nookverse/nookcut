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
