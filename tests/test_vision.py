"""Image judging: pixel-stat traits on synthetic images, emoji choice, trigger rules, pipeline guarantee."""
import asyncio
import os
import random
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import convo  # noqa: E402
import image_lines as IL  # noqa: E402
import vision  # noqa: E402
from store import DEFAULT_CONFIG, Store  # noqa: E402
from tests import synth_images as S  # noqa: E402
from tests.convo_harness import Clock, shared_index, tweets  # noqa: E402

_CACHE = {}
_MIDS = iter(range(5_000_000, 6_000_000))


def feats(kind, **kw):
    key = (kind, tuple(sorted(kw.items())))
    if key not in _CACHE:
        data = getattr(S, kind)(**kw)
        _CACHE[key] = vision.analyze(data, kind + (".png" if kind == "screenshot" else ".jpg"))
    return _CACHE[key]


def judge(seed=1, clock=None):
    clock = clock or Clock()
    b = brain.Brain(Store(None), tweets(), rng=random.Random(seed), clock=clock, index=shared_index())
    convo.Conversations(b, clock)
    return vision.ImageJudge(b, clock), clock


BODY_RE = re.compile(r"\b(face|body|bodies|hair|skin|weight|fat|thin|ugly|pretty|teeth|nose|eyes|outfit|"
                     r"chin|forehead|cheeks|lips|looks like you|you look)\b", re.I)


class TestTraits(unittest.TestCase):
    def test_dark_blurry(self):
        f = feats("dark_blurry")
        self.assertTrue(f["dark"] and f["blurry"])
        self.assertFalse(f["screenshot"] or f["meme"] or f["vibrant"])
        self.assertEqual(f["primary"], "dark_blurry")
        self.assertIn("💀", vision.emojis(f))
        self.assertLessEqual(f["score"], 4)

    def test_bright_colorful(self):
        f = feats("bright_colorful")
        self.assertTrue(f["vibrant"] and f["sharp"])
        self.assertFalse(f["dark"] or f["blurry"] or f["mono"])
        self.assertEqual(vision.emojis(f)[0], "🔥")
        self.assertGreaterEqual(f["score"], 7)

    def test_monochrome(self):
        f = feats("monochrome")
        self.assertTrue(f["mono"])
        self.assertEqual(f["primary"], "mono")
        self.assertEqual(vision.emojis(f)[0], "🗿")

    def test_screenshot(self):
        f = feats("screenshot")
        self.assertTrue(f["screenshot"])
        self.assertFalse(f["meme"] or f["people"] or f["blurry"])
        self.assertEqual(f["primary"], "screenshot")
        self.assertEqual(vision.emojis(f), ["🤨"])

    def test_wallpaper_at_screen_size_is_not_a_screenshot(self):
        f = vision.analyze(S.wallpaper(), "wallpaper.png")
        self.assertFalse(f["screenshot"])
        f = vision.analyze(S.wallpaper(), "Screenshot_20261011-031500.png")
        self.assertTrue(f["screenshot"])  # the filename is a strong hint

    def test_meme_bands(self):
        f = feats("meme")
        self.assertTrue(f["meme_bands"] and f["meme"])
        self.assertFalse(f["screenshot"])
        self.assertEqual(vision.emojis(f)[0], "🤡")

    def test_glow_room_like_the_halo_helmet(self):
        f = feats("glow_room", blur=12)
        self.assertTrue(f["dark"] and f["glow"])
        self.assertEqual(f["glow_color"], "blue")
        self.assertEqual(f["primary"], "glow_blurry")
        self.assertIn("👀", vision.emojis(f))

    def test_portrait_is_people_safe(self):
        f = feats("skin_portrait")
        self.assertTrue(f["people"])
        for e in vision.emojis(f):
            self.assertIn(e, vision.PEOPLE_EMOJI)

    def test_one_or_two_emojis_always(self):
        for kind in S.ALL:
            em = vision.emojis(feats(kind))
            self.assertTrue(1 <= len(em) <= 2, (kind, em))

    def test_unreadable_and_oversize(self):
        with self.assertRaises(ValueError):
            vision.analyze(b"not an image at all", "x.png")
        with self.assertRaises(ValueError):
            vision.analyze(b"\0" * (vision.MAX_BYTES + 1), "big.png")
        self.assertFalse(vision.is_image_attachment("big.png", "image/png", vision.MAX_BYTES + 1))
        self.assertFalse(vision.is_image_attachment("clip.mp4", "video/mp4", 100))
        self.assertTrue(vision.is_image_attachment("a.webp", None, 100))

    def test_gif_first_frame(self):
        import io
        from PIL import Image
        frames = [Image.new("RGB", (64, 64), c) for c in ((255, 0, 0), (0, 0, 255))]
        b = io.BytesIO()
        frames[0].save(b, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
        f = vision.analyze(b.getvalue(), "x.gif")
        self.assertTrue(f["animated"])
        self.assertEqual(f["color"], "red")

    def test_exif_hints(self):
        import io
        from PIL import Image
        img = Image.new("RGB", (400, 300), (20, 20, 30))
        ex = Image.Exif()
        ex[0x010F] = "Apple"
        ex[0x0110] = "iPhone 15 Pro"
        ex[0x0132] = "2026:10:10 23:41:00"
        b = io.BytesIO()
        img.save(b, "JPEG", exif=ex)
        f = vision.analyze(b.getvalue(), "IMG_0001.jpg")
        self.assertEqual(f["device"], "iPhone 15 Pro")
        self.assertEqual(f["hour"], 23)
        self.assertTrue(f["night_hint"])


class TestVerdicts(unittest.TestCase):
    def test_verdict_per_type(self):
        for kind in S.ALL:
            f = feats(kind)
            for seed in range(6):
                j, _ = judge(seed)
                v = j.verdict(f, gid=1, uid=5, name="GamerQuailYT", cfg=dict(DEFAULT_CONFIG), asked=seed % 2 == 0)
                self.assertTrue(v and len(v) < 300, v)
                self.assertNotIn("{", v)
                self.assertFalse(brain.SlurGuard().hit(v), v)

    def test_people_mode_never_judges_the_person(self):
        f = feats("skin_portrait")
        allowed = set()
        for b in vision.PEOPLE_SAFE | {"people_safe", "fire_hi", "fire_mid", "fire_lo", "device", "night",
                                       "generic_img"}:
            allowed.update(IL.BANKS[b])
        for seed in range(30):
            j, _ = judge(seed)
            v = j.verdict(f, gid=1, uid=5, name="Ana", cfg=dict(DEFAULT_CONFIG), asked=True)
            self.assertIsNone(BODY_RE.search(v), v)
        for b in vision.PEOPLE_SAFE | {"people_safe"}:
            for line in IL.BANKS[b]:
                self.assertIsNone(BODY_RE.search(line), line)

    def test_all_lines_clean(self):
        total = 0
        for name, lines in IL.BANKS.items():
            total += len(lines)
            for line in lines:
                self.assertFalse(brain.SlurGuard().hit(line), line)
                # 'black'/'white' are colours here (black and white photos, white bars), not people
                self.assertIsNone(convo.SENSITIVE_RE.search(re.sub(r"(?i)\b(black|white)\b", "", line)), line)
                self.assertIsNone(BODY_RE.search(line), line)
        self.assertGreaterEqual(total, 250)

    def test_mild_tone_has_no_vulgar_verdicts(self):
        for kind in S.ALL:
            for seed in range(5):
                j, _ = judge(seed)
                v = j.verdict(feats(kind), gid=1, uid=5, name="Ana", cfg=dict(DEFAULT_CONFIG, tone="mild"))
                self.assertIsNone(brain.VULGAR_RE.search(v), v)

    def test_halo_example_mentions_glow_or_light(self):
        f = feats("glow_room", blur=12)
        hits = 0
        for seed in range(12):
            j, _ = judge(seed)
            v = j.verdict(f, gid=1, uid=5, name="GamerQuailYT", cfg=dict(DEFAULT_CONFIG), asked=True)
            hits += bool(re.search(r"blue|glow|light|dark|blur|focus|camera", v, re.I))
        self.assertGreaterEqual(hits, 9)


class TestTriggerRules(unittest.TestCase):
    C = dict(DEFAULT_CONFIG)

    def test_asked_detection(self):
        for t in ("Is this fire or what?", "rate this", "thoughts?", "what do you think", "be honest",
                  "how does it look", "is it mid", "out of 10?", "roast my setup"):
            self.assertTrue(vision.ImageJudge.asked(t), t)
        for t in ("", "my new helmet", "lol", "look at this", "finally done"):
            self.assertFalse(vision.ImageJudge.asked(t), t)

    def test_unprompted_is_react_only_mostly(self):
        j, clock = judge(1)
        verdicts = 0
        for i in range(200):
            clock.t += 5
            react, want = j.plan(1, 10, 100 + i, "", cfg=dict(self.C, image_react_per_min=1000))
            self.assertTrue(react)
            verdicts += want
        # 10% chance but a 300s per-channel cooldown over 1000s -> at most 4
        self.assertLessEqual(verdicts, 4)

    def test_zero_rate_never_talks_unprompted(self):
        j, clock = judge(2)
        for i in range(100):
            clock.t += 400
            self.assertFalse(j.plan(1, 10, 7, "", cfg=dict(self.C, image_reply_rate=0.0))[1])

    def test_asked_or_explicit_gets_verdict(self):
        j, clock = judge(3)
        clock.t += 10
        self.assertEqual(j.plan(1, 10, 7, "Is this fire or what?", cfg=self.C), (True, True))
        clock.t += 10
        self.assertEqual(j.plan(1, 10, 8, "", cfg=self.C, explicit=True)[1], True)

    def test_modes_ignore_other_target_abuse(self):
        j, clock = judge(4)
        clock.t += 10
        self.assertEqual(j.plan(1, 10, 7, "rate this", cfg=dict(self.C, images="off")), (False, False))
        self.assertEqual(j.plan(1, 10, 7, "rate this", cfg=dict(self.C, images="react-only")), (True, False))
        self.assertEqual(j.plan(1, 10, 7, "rate this", cfg=self.C, ignored=True), (False, False))
        self.assertEqual(j.plan(1, 10, 7, "rate this", cfg=self.C, other_target=True), (True, False))
        self.assertEqual(j.plan(1, 10, 7, "rate this", cfg=self.C, abusive=True), (True, False))

    def test_reaction_cap_per_channel(self):
        j, clock = judge(5)
        c = dict(self.C, image_react_per_min=3)
        got = [j.plan(1, 10, i, "", cfg=c)[0] for i in range(6)]
        self.assertEqual(got, [True, True, True, False, False, False])
        self.assertTrue(j.plan(1, 11, 1, "", cfg=c)[0])  # other channel unaffected
        clock.t += 61
        self.assertTrue(j.plan(1, 10, 1, "", cfg=c)[0])

    def test_asked_respects_user_cooldown(self):
        j, clock = judge(6)
        clock.t += 10
        self.assertTrue(j.plan(1, 10, 7, "rate this", cfg=self.C)[1])
        self.assertFalse(j.plan(1, 10, 7, "rate this", cfg=self.C)[1])  # same second: mention cooldown


class TestPipeline(unittest.TestCase):
    """Full bot.on_message with an image attachment: <=1 text send, <=2 reactions."""

    @classmethod
    def setUpClass(cls):
        from tests import test_pipeline as TP
        cls.TP = TP
        import bot as B
        cls.B = B
        B.bot._connection.user = TP.BOT_USER

    def setUp(self):
        B, TP = self.B, self.TP
        self.loop = asyncio.new_event_loop()
        self.ch, self.g = TP.FakeChannel(), TP.FakeGuild()
        self.patches = [
            mock.patch.object(B.bot, "get_context", mock.AsyncMock(return_value=mock.Mock(valid=False))),
            mock.patch.object(B, "automod", mock.AsyncMock(return_value=False)),
            mock.patch.object(B.asyncio, "sleep", mock.AsyncMock()),
            mock.patch.object(B.store, "save", lambda *a, **k: False),
        ]
        for p in self.patches:
            p.start()
        self.clock = [9000.0]
        clk = lambda: self.clock[0]  # noqa: E731
        B.BRAIN.clock = clk
        for cd in (B.BRAIN.chan_cd, B.BRAIN.user_cd, B.BRAIN.mention_cd):
            cd.clock = clk
        B.CONVO.clock = clk
        B.JUDGE.clock = clk
        for cd in (B.BRAIN.chan_cd, B.BRAIN.user_cd, B.BRAIN.mention_cd):
            cd.last.clear()
        B.JUDGE.reacts.clear()
        B.JUDGE.last_verdict.clear()
        B.CONVO.channels.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.loop.close()

    def att(self, kind="glow_room"):
        a = mock.Mock()
        a.filename, a.content_type = kind + ".jpg", "image/jpeg"
        data = getattr(S, kind)()
        a.size = len(data)
        a.read = mock.AsyncMock(return_value=data)
        return a

    def run_msg(self, text, *, kind="glow_room", mention=False, uid=50, mid=None, attach=True):
        TP = self.TP
        self.clock[0] += 20
        mid = mid if mid is not None else next(_MIDS)  # own id range: bot dedupes message ids globally
        msg = TP.make_msg(text, TP.FakeUser(uid, "GamerQuailYT"), self.ch, self.g, mention_bot=mention, mid=mid,
                          attachments=[self.att(kind)] if attach else [])
        before = self.ch.send.await_count
        self.loop.run_until_complete(self.B.on_message(msg))
        return msg, msg.reply.await_count + self.ch.send.await_count - before

    def test_asked_image_gets_exactly_one_verdict_and_reactions(self):
        msg, n = self.run_msg("Is this fire or what?")
        self.assertEqual(n, 1)
        self.assertTrue(1 <= msg.add_reaction.await_count <= 2)
        text = msg.reply.await_args.args[0]
        self.assertRegex(text, r"\w")

    def test_unprompted_image_reacts_silently(self):
        with mock.patch.object(self.B, "cfg", lambda g: dict(DEFAULT_CONFIG, image_reply_rate=0.0)):
            for kind in S.ALL:
                msg, n = self.run_msg("", kind=kind, uid=60)
                self.assertEqual(n, 0, kind)
                self.assertTrue(1 <= msg.add_reaction.await_count <= 2, kind)

    def test_mention_with_image_single_send_any_text(self):
        for i, text in enumerate(("thoughts?", "hey", "stfu bot", "you're trash bot", "lol", "rate my setup")):
            msg, n = self.run_msg(text, mention=True, uid=70 + i, kind="bright_colorful")
            self.assertLessEqual(n, 1, text)
            self.assertLessEqual(msg.add_reaction.await_count, 2)

    def test_duplicate_dispatch_one_action(self):
        msg, n = self.run_msg("rate this", mid=777_001)
        self.loop.run_until_complete(self.B.on_message(msg))
        self.assertEqual(msg.reply.await_count + self.ch.send.await_count, 1)
        self.assertLessEqual(msg.add_reaction.await_count, 2)

    def test_images_off_does_nothing(self):
        with mock.patch.object(self.B, "cfg", lambda g: dict(DEFAULT_CONFIG, images="off")):
            msg, n = self.run_msg("rate this", uid=90)
        self.assertEqual(msg.add_reaction.await_count, 0)
        self.assertLessEqual(n, 1)  # may still chat about the text, never an image verdict
        if n:
            self.assertNotRegex(msg.reply.await_args.args[0] if msg.reply.await_count else "", r"/10")

    def test_react_only_mode(self):
        with mock.patch.object(self.B, "cfg", lambda g: dict(DEFAULT_CONFIG, images="react-only")):
            msg, n = self.run_msg("", uid=91)
        self.assertEqual(n, 0)
        self.assertGreaterEqual(msg.add_reaction.await_count, 1)


if __name__ == "__main__":
    unittest.main()
