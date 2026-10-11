"""Regression tests for the 2026-10-11 live bug report (tone requests, hijacking, echo, caps)."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import convo_lines as CL  # noqa: E402
from tests.convo_harness import Harness, live_bug  # noqa: E402

SEEDS = range(30)
BAT = (8, "[DEAD] Batdaddy [DΞΛD]")


class TestToneRequests(unittest.TestCase):
    def test_meta_requests_get_tone_answer(self):
        for phrase in ("like can you be less offensive", "tone it down bot", "can you be nice",
                       "chill out elon", "be nicer", "stop being so mean"):
            for seed in range(8):
                with self.subTest(phrase=phrase, seed=seed):
                    h = Harness(seed)
                    h.send(*BAT, "hey", mention=True)
                    r = h.send(*BAT, phrase)
                    self.assertIsNotNone(r)
                    self.assertEqual(r.intent, "tone_req", r.text)

    def test_summon_by_name(self):
        for seed in range(10):
            h = Harness(seed)
            r = h.send(*BAT, "Elon")
            self.assertIsNotNone(r)
            self.assertEqual(r.intent, "summon")

    def test_mild_tone_skips_vulgar_and_caps_abuse(self):
        for seed in SEEDS:
            h = Harness(seed, tone="mild")
            h.send(*BAT, "hey", mention=True)
            for i in range(6):
                r = h.send(*BAT, "stfu bot you're fucking trash", gap=8)
                if r:
                    self.assertEqual(r.intent, "abuse_1")
                    self.assertIsNone(brain.VULGAR_RE.search(r.text), r.text)

    def test_savage_tone_starts_at_tier_2(self):
        h = Harness(1, tone="savage")
        h.send(*BAT, "hey", mention=True)
        r = h.send(*BAT, "stfu bot")
        self.assertEqual(r.intent, "abuse_2")


class TestNoHijack(unittest.TestCase):
    def test_message_to_other_user_inside_session_is_ignored(self):
        for seed in SEEDS:
            h = Harness(seed)
            h.send(*BAT, "hey", mention=True)
            h.send(*BAT, "bruh we are testing you")
            r = h.send(*BAT, "Edwin your best friend is back", other_target=True)
            self.assertIsNone(r, r and r.text)
            r = h.send(*BAT, "you are so right lmao", other_target=True)
            self.assertIsNone(r, r and r.text)

    def test_naming_the_bot_overrides_other_target(self):
        h = Harness(2)
        h.send(*BAT, "hey", mention=True)
        r = h.send(*BAT, "elon roast Edwin for me", other_target=True)
        self.assertIsNotNone(r)

    def test_live_bug_replay_is_single_and_quiet_for_edwin(self):
        for seed in SEEDS:
            h = live_bug(seed)
            edwin = [r for (_, t, r) in h.log if "Edwin" in t]
            self.assertEqual(edwin, [None])
            self.assertEqual(h.log[2][2].intent, "tone_req")


class TestEchoAndCaps(unittest.TestCase):
    CHAT = ["i like pizza", "my car broke down today", "what do you think of mars", "lol whatever",
            "i'm a mechanic", "do you even sleep", "that's wild", "nah", "why though", "tell me something",
            "i went fishing", "bro you're slow"]

    def _snippet_hits(self, h, said, reply):
        s = brain.clean_text(said).lower().strip(" .!?")
        return len(s) > 6 and s in reply.lower()

    def test_whole_message_echo_capped(self):
        for seed in SEEDS:
            h = Harness(seed)
            flags = []
            h.send(*BAT, "hey", mention=True)
            for i in range(40):
                said = self.CHAT[i % len(self.CHAT)]
                r = h.send(*BAT, said, mention=True, gap=30)
                if r:
                    flags.append(self._snippet_hits(h, said, r.text))
            for a, b in zip(flags, flags[1:]):
                self.assertFalse(a and b, f"two echo lines in a row (seed={seed})")
            self.assertLessEqual(sum(flags), len(flags) / 4 + 1, f"seed={seed}: {sum(flags)}/{len(flags)}")

    def test_fix_caps(self):
        self.assertEqual(brain.fix_caps("ok. that's it! why? no"), "Ok. That's it! Why? No")
        self.assertEqual(brain.fix_caps('you said "lol. ok" fine'), 'You said "lol. ok" fine')
        self.assertEqual(brain.fix_caps("wait... and then"), "Wait... and then")

    def test_no_lowercase_sentence_starts_in_replies(self):
        bad = re.compile(r"(?:^|(?<!\.\.)[.!?]\s+)([a-z])")
        for seed in range(15):
            h = live_bug(seed)
            for _, _, r in h.log:
                if r:
                    outside = re.sub(r'"[^"]*"', '"Q"', r.text)
                    self.assertIsNone(bad.search(outside), r.text)

    def test_new_banks_clean(self):
        for bank in ("tone_req", "summon"):
            self.assertGreaterEqual(len(CL.BANKS[bank]), 10)
            for line in CL.BANKS[bank]:
                self.assertFalse(brain.SlurGuard().hit(line), line)


if __name__ == "__main__":
    unittest.main()
