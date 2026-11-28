"""Only context-approved, transcript-anchored subtitle pop-outs are rendered."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from render_clip import build_ass  # pyright: ignore[reportMissingImports]


def word(text, start):
    return {"w": text, "s": start, "e": start + .25}


def marker(segment, start, text, emoji):
    return {"segment": segment, "start": start, "word": text, "emoji": emoji}


def render(words, plan=None, *, segments=None, config=None, **kwargs):
    clip = {"id": "clip01", "segments": segments or [{"start": 0, "end": 9}]}
    if plan is not None:
        clip["emoji_popouts"] = plan
    cues = []
    ass, _, _ = build_ass(clip, words, config or {}, None, emoji_cues=cues, **kwargs)
    return cues, ass


class ReviewRenderTest(unittest.TestCase):
    def test_unreviewed_and_explicit_no_never_trigger_keywords(self):
        for plan, cfg in [(None, {}), ([], {}), ([], {"caption_emoji_mode": "legacy"})]:
            cues, ass = render([word("Auto!", .2)], plan, config=cfg)
            self.assertEqual(cues, [])
            self.assertIn("AUTO!", ass)

    def test_exact_anchor_follows_cut_order_captions_remain_intact(self):
        words = [word("Game", .5), word("gekauft.", 1.5), word("Game", 10.3), word("Euro!", 10.8)]
        cfg = [{"start": 10, "end": 12}, {"start": 1, "end": 4}]
        plan = [marker(1, 1.5, "gekauft.", "💳"), marker(0, 10.3, "Game", "🎮")]
        cues, ass = render(words, plan, segments=cfg)
        self.assertEqual([c.glyph for c in cues], ["🎮", "💳"])
        self.assertAlmostEqual(cues[0].start, .3)
        self.assertAlmostEqual(cues[1].start, 2.5)
        self.assertNotEqual(cues[0].corner, cues[1].corner)
        self.assertIn("GEKAUFT.", ass)
        self.assertNotIn("🎮", ass)

    def test_stale_unsafe_duplicate_or_oversized_plans_fail_closed(self):
        base = marker(0, 1., "Game", "🎮")
        invalid = ["no", [base] * 4, [base, base], [{**base, "start": True}],
                   [{**base, "start": 1.001}], [{**base, "word": "game"}],
                   [{**base, "segment": 1}], [{**base, "emoji": "-anything"}],
                   [{**base, "emoji": "🎮🎮"}], [{**base, "emoji": "👍🏽"}]]
        for plan in invalid:
            with self.subTest(plan=plan):
                self.assertEqual(render([word("Game", 1.), word("Auto!", 2.)], plan,
                                        config={"caption_emoji_mode": "legacy"})[0], [])

    def test_no_overlay_for_ducked_word_close_cue_or_unsupported_glyph(self):
        words = [word("Game", .5), word("Karte", 2.), word("Handy!", 2.5)]
        plan = [marker(0, .5, "Game", "🎮"), marker(0, 2., "Karte", "💳"), marker(0, 2.5, "Handy!", "📱")]
        candidates = set()
        self.assertEqual([c.glyph for c in render(words, plan, duck_until=1., candidate_glyphs=candidates)[0]], ["💳"])
        self.assertEqual(candidates, {"💳", "📱"})
        self.assertEqual([c.glyph for c in render(words, plan, duck_until=1., unavailable_glyphs={"💳"})[0]], ["📱"])

    def test_number_merge_does_not_move_cue_earlier(self):
        words = [word("hundert", 1.), word("zwanzig", 1.35), word("Euro!", 1.7)]
        cues, ass = render(words, [marker(0, 1.35, "zwanzig", "💵")])
        self.assertEqual([(c.glyph, c.start) for c in cues], [("💵", 1.35)])
        self.assertIn("120", ass)


class VoiceoverEmojiReceiptTest(unittest.TestCase):
    def test_staging_failure_leaves_final_decision_to_render_fingerprint(self):
        path = SCRIPTS.parents[3] / "live-ops/live/vo_mix.py"
        spec = importlib.util.spec_from_file_location("vo_mix", path)
        assert spec is not None and spec.loader is not None
        mix = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mix)
        mkdir, write_bytes = Path.mkdir, Path.write_bytes
        for operation in ("mkdir", "write"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                work, out = home / "work/demo", home / "out/demo"
                (work / "emoji-reviews").mkdir(parents=True)
                out.mkdir(parents=True)
                (work / "clips.json").write_text(json.dumps({"clips": [
                    {"id": "clip01", "vo": "Intro.", "segments": [{"start": 0, "end": 4}]},
                ]}))
                (work / "sections.json").write_text("{}")
                original = out / "clip01.mp4"
                original.write_bytes(b"original")
                (out / "clip01.json").write_text(json.dumps({
                    "segments": [{"start": 0, "end": 4}], "render_input_sha256": "original-inputs",
                    "original_render_sha256": mix.media_revision(original),
                }))
                (work / "emoji-reviews/clip01.json").write_bytes(b"review")
                audio = home / "intro.mp3"
                audio.write_bytes(b"audio")

                def blocked_mkdir(target, *args, **kwargs):
                    if operation == "mkdir" and target.name == "emoji-reviews":
                        raise PermissionError("review directory unavailable")
                    return mkdir(target, *args, **kwargs)

                def blocked_write(target, data):
                    if operation == "write" and target.parent.name == "emoji-reviews":
                        write_bytes(target, data[:1])
                        raise OSError("interrupted review write")
                    return write_bytes(target, data)

                def rerender(_command, *, env, **_kwargs):
                    stage = Path(env["CLIPS_HOME"])
                    self.assertFalse((stage / "work/demo/emoji-reviews/clip01.json").exists())
                    rendered = stage / "out/demo/clip01.json"
                    rendered.parent.mkdir(parents=True)
                    rendered.write_text(json.dumps({"render_input_sha256": "inputs-without-review"}))

                with (patch.object(Path, "mkdir", blocked_mkdir), patch.object(Path, "write_bytes", blocked_write),
                      patch.object(mix, "video_timeline", return_value={"duration": "4", "avg_frame_rate": "30/1"}),
                      patch.object(mix, "audio_duration", return_value=4.),
                      patch.object(mix.subprocess, "check_output", return_value=b"1.0"),
                      patch.object(mix.subprocess, "run", side_effect=rerender) as run):
                    with self.assertRaisesRegex(ValueError, "Render-Eingaben haben sich geändert"):
                        mix.process(home, "demo", "clip01", None, audio)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(original.read_bytes(), b"original")


class ReviewAgentTest(unittest.TestCase):
    def test_cleanup_removes_only_exact_staged_review_image_and_session(self):
        from review_caption_emoji import cleanup_review  # pyright: ignore[reportMissingImports]
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache/images"
            cache.mkdir(parents=True)
            image = cache / "emoji_review_abcdefgh.jpg"
            other = cache / "upload_ijklmnop.jpg"
            for p in (image, other):
                p.write_bytes(b"review JPEG")
            sid = "local-" + "a" * 64
            busy = subprocess.CompletedProcess([], 1, "Cannot delete active session", "")
            deleted = subprocess.CompletedProcess([], 0, "", "")
            with (patch("review_caption_emoji.subprocess.run", side_effect=[busy, deleted]) as invoke,
                  patch("review_caption_emoji.time.sleep")):
                cleanup_review("hermes", sid, image, b"review JPEG")
            self.assertFalse(image.exists())
            self.assertTrue(other.is_file())
            self.assertEqual(invoke.call_args.args[0], ["hermes", "sessions", "delete", "--yes", sid])
            self.assertEqual(invoke.call_count, 2)

    def test_cancel_after_missing_ack_matches_only_our_input(self):
        from contextlib import nullcontext
        from review_caption_emoji import cancel_review  # pyright: ignore[reportMissingImports]

        pending = {"pending": [{"input_id": "other", "admission_id": "other-job"},
                               {"input_id": "own", "admission_id": "own-job"}]}
        with (patch("review_caption_emoji.gateway_socket", return_value=nullcontext("socket")),
              patch("review_caption_emoji.rpc", side_effect=[pending, {}]) as invoke):
            cancel_review("hermes", "session", "own", None)
        self.assertEqual(invoke.call_args.args[3], "prompt.cancel")
        self.assertEqual(invoke.call_args.kwargs, {"session_id": "session", "admission_id": "own-job"})

    def test_described_ids_cannot_be_applied_after_transcript_insertion(self):
        from review_caption_emoji import review  # pyright: ignore[reportMissingImports]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            plan = work / "clips.json"
            plan.write_text(json.dumps({"clips": [{"id": "clip01", "segments": [{"start": 0, "end": 6}]}]}))
            with patch("review_caption_emoji._lib.load_words", return_value=[word("Karte", 3.)]):
                original = review(work, "clip01", describe=True)
            revision = original.get("revision") if isinstance(original, dict) else "old-context"
            decision = json.dumps({"revision": revision, "choices": [{"id": 0, "emoji": "💳"}]})
            with patch("review_caption_emoji._lib.load_words", return_value=[word("Auto", 1.), word("Karte", 3.)]):
                with self.assertRaisesRegex(ValueError, "geändert"):
                    review(work, "clip01", decision=decision)

    def test_review_never_replaces_clip_plan(self):
        from review_caption_emoji import review  # pyright: ignore[reportMissingImports]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            plan = work / "clips.json"
            before = '{"clips":[{"id":"clip01","segments":[{"start":0,"end":6}]}]}\n'
            plan.write_text(before)
            with (patch("review_caption_emoji._lib.load_words", return_value=[word("Auto", 1.)]),
                  patch("review_caption_emoji.ask_agent", return_value="[]")):
                review(work, "clip01")
            self.assertEqual(plan.read_text(), before)

    def test_corrupt_catalog_does_not_break_render(self):
        import caption_emoji_plan  # pyright: ignore[reportMissingImports]
        caption_emoji_plan.approved_glyphs.cache_clear()
        try:
            for effect in (None, TypeError("Katalogwurzel ist kein Objekt")):
                with patch("caption_emoji_plan.emoji_catalog.entries", return_value=None, side_effect=effect):
                    cues, ass = render([word("Game", 1.)], [marker(0, 1., "Game", "🎮")])
                self.assertEqual(cues, [])
                self.assertIn("GAME", ass)
        finally:
            caption_emoji_plan.approved_glyphs.cache_clear()

    def test_agent_output_can_only_choose_provided_ids_and_known_glyphs(self):
        from review_caption_emoji import anchor_choices, parse_choices, word_context  # pyright: ignore[reportMissingImports]
        clip = {"segments": [{"start": 1, "end": 5}, {"start": 0, "end": .8}]}
        context = word_context(clip, [word("Game", .2), word("Game", 1.), word("Karte", 3.), word("Auto", 8.)])
        self.assertEqual([row["word"] for row in context], ["Game", "Karte", "Game"])
        self.assertEqual(anchor_choices(parse_choices('[{"id":1,"emoji":"💳"}]'), context),
                         [marker(0, 3., "Karte", "💳")])
        self.assertEqual(anchor_choices(parse_choices("[]"), context), [])
        for text in ['[{"id":99,"emoji":"💳"}]', '[{"id":0,"emoji":"x"}]',
                     '[{"id":true,"emoji":"💳"}]', '[{"id":0,"emoji":"💳","start":0}]',
                     '[{"id":0,"id":1,"emoji":"💳"}]', 'Here is []']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                anchor_choices(parse_choices(text), context)

    def test_failed_agent_keeps_plan_unreviewed_but_empty_answer_is_saved(self):
        from review_caption_emoji import review  # pyright: ignore[reportMissingImports]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            plan = work / "clips.json"
            plan.write_text(json.dumps({"clips": [{"id": "clip01", "segments": [{"start": 0, "end": 4}]}]}))
            with (patch("review_caption_emoji._lib.load_words", return_value=[word("Auto", 1.)]),
                  patch("review_caption_emoji.ask_agent", side_effect=subprocess.TimeoutExpired("hermes", 60))):
                with self.assertRaises(subprocess.TimeoutExpired):
                    review(work, "clip01")
            self.assertNotIn("emoji_popouts", json.loads(plan.read_text())["clips"][0])
            with (patch("review_caption_emoji._lib.load_words", return_value=[word("Auto", 1.)]),
                  patch("review_caption_emoji.ask_agent", return_value="[]")):
                self.assertEqual(review(work, "clip01")["emoji_popouts"], [])
            self.assertEqual(json.loads((work / "emoji-reviews/clip01.json").read_text())["emoji_popouts"], [])

    def test_gta_calls_context_review_before_render_and_can_continue_without_it(self):
        cycle_path = SCRIPTS.parents[3] / "live-ops/live/game_clip_cycle.py"
        spec = importlib.util.spec_from_file_location("game_clip_cycle", cycle_path)
        assert spec is not None and spec.loader is not None
        cycle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cycle)
        called = []

        def invoke(name, *_args, **_kwargs):
            called.append(name)
            return (2 if name == "review_caption_emoji.py" else 0), {}, ""

        with patch.object(cycle, "uv_script", side_effect=invoke):
            self.assertEqual(cycle.cut("vod", Path("source.mp4"), "streamer"), (True, ""))
        self.assertLess(called.index("validate_clips.py"), called.index("review_caption_emoji.py"))
        self.assertLess(called.index("review_caption_emoji.py"), called.index("render_clip.py"))
