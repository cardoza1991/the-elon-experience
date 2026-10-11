"""Live transcript 2026-10-11 (user 'Artifact'): slang abuse, trait insults, mock pity, echo questions,
'called bot', 'I broke it', 'you aren't making sense' and pronoun/verb agreement in reflection."""
import itertools
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import convo  # noqa: E402
import convo_lines as CL  # noqa: E402
from tests.convo_harness import Harness  # noqa: E402

SEEDS = range(30)
ART = (11, "Artifact")
TRANSCRIPT = [
    ("yo... BOT!!! talk to me", None),
    ("GFYS Bot", "abuse"),
    ("smoke a phatty", "abuse_grass"),
    ("get good", "abuse_gamer"),
    ("your vibe firewall??? yer just so gay", "abuse_trait"),
    ("oh, poor bot", "mock_pity"),
    ('do you hate being calling "Bot??" I\'ll keep doing is if so', "called"),
    ("I think I broke it", "broke_it"),
    ("cuz you aren't making any sense now bot", "no_sense"),
]
SELF_HARM_WORDS = re.compile(r"\b(kys|kill|die|dying|dead|suicid\w*|self[- ]harm|unalive|neck)\b", re.I)


def run_transcript(seed):
    h = Harness(seed)
    out = []
    for i, (text, _) in enumerate(TRANSCRIPT):
        out.append(h.send(*ART, text, mention=(i == 0), gap=12))
    return h, out


class TestArtifactTranscript(unittest.TestCase):
    def test_routes(self):
        for seed in SEEDS:
            _, out = run_transcript(seed)
            for (text, want), r in zip(TRANSCRIPT, out):
                with self.subTest(seed=seed, text=text):
                    self.assertIsNotNone(r)
                    if want == "abuse":
                        self.assertTrue(r.intent.startswith("abuse"), r)
                    elif want:
                        self.assertEqual(r.intent, want, r.text)
                    self.assertNotEqual(r.intent, "kind")

    def test_no_protected_words_bad_grammar_or_repeats(self):
        for seed in SEEDS:
            _, out = run_transcript(seed)
            texts = [r.text for r in out]
            self.assertEqual(len(set(texts)), len(texts), texts)
            for t in texts:
                self.assertIsNone(brain.PROTECTED_RE.search(t), t)
                self.assertIsNone(convo.AGREE_BAD_RE.search(t), t)
                self.assertNotIn("firewall talk", t.lower())

    def test_gay_line_never_riffs(self):
        for seed in SEEDS:
            _, out = run_transcript(seed)
            r = out[4]
            self.assertNotRegex(r.text.lower(), r"\bgay|sexual|homo")
            self.assertNotIn("firewall", r.text.lower())


class TestSlangAbuse(unittest.TestCase):
    SLANG = ["gfys", "stfu", "foh", "smd", "eat shit", "ratio", "L bot", "L", "cope", "seethe", "mald", "cry about it",
             "get good", "git gud", "skill issue", "trash", "mid", "npc", "clanker", "touch grass", "go outside",
             "smoke a phatty", "get a life", "log off", "who asked", "nobody asked", "stay mad", "womp womp",
             "you're so gay bot", "that's so gay", "kys", "go die bot", "cry more", "massive skill issue"]

    def test_each_routes_to_comeback(self):
        for text in self.SLANG:
            for seed in range(4):
                with self.subTest(text=text, seed=seed):
                    h = Harness(seed)
                    h.send(*ART, "hey bot", mention=True)
                    r = h.send(*ART, text, gap=12)
                    self.assertIsNotNone(r)
                    self.assertTrue(r.intent.startswith("abuse"), (text, r))
                    self.assertIsNone(brain.PROTECTED_RE.search(r.text), r.text)

    def test_kys_never_talks_self_harm_or_echoes(self):
        for text in ("kys", "kys bot", "go kill yourself elon", "go die bot"):
            for seed in range(10):
                h = Harness(seed)
                h.send(*ART, "hey bot", mention=True)
                r = h.send(*ART, text, gap=12)
                self.assertEqual(r.intent, "abuse_kys", (text, r))
                self.assertIsNone(SELF_HARM_WORDS.search(r.text), r.text)
        for line in CL.ABUSE_KYS:
            self.assertIsNone(SELF_HARM_WORDS.search(line), line)

    def test_gfys_mentions_without_session(self):
        h = Harness(1)
        r = h.send(*ART, "GFYS", mention=True)
        self.assertTrue(r.intent.startswith("abuse"))

    def test_no_false_positives(self):
        for text in ("how do you cope with stress", "i need to get good at this game", "that game is mid",
                     "i was gonna go outside later", "my boss is a clown", "are you gay?", "i'm gay",
                     "i'm black and proud", "my friend is trans", "go outside and play is what my mom said"):
            h = Harness(2)
            h.send(*ART, "hey bot", mention=True)
            r = h.send(*ART, text, gap=12)
            self.assertFalse(r.intent.startswith("abuse"), (text, r))

    def test_trait_insult_lines_clean(self):
        for name in ("abuse_trait", "abuse_kys", "abuse_gamer", "abuse_grass", "mock_pity", "called", "broke_it",
                     "no_sense", "echo_q"):
            for line in CL.BANKS[name]:
                self.assertIsNone(brain.PROTECTED_RE.search(line), line)
                self.assertFalse(brain.SlurGuard().hit(line), line)

    def test_mild_tone_trait_and_gamer(self):
        for text in ("you're so gay bot", "get good", "touch grass"):
            for seed in range(6):
                h = Harness(seed, tone="mild")
                h.send(*ART, "hey bot", mention=True)
                r = h.send(*ART, text, gap=12)
                self.assertTrue(r.intent.startswith("abuse"))


class TestPityAndKindness(unittest.TestCase):
    def test_pity_after_abuse_is_sarcasm(self):
        for kind_text in ("oh, poor bot", "aww are you ok bot", "poor bot", "hope you're fine elon"):
            for seed in range(6):
                h = Harness(seed)
                h.send(*ART, "hey bot", mention=True)
                h.send(*ART, "fuck you bot", gap=12)
                r = h.send(*ART, kind_text, gap=12)
                self.assertEqual(r.intent, "mock_pity", (kind_text, r))

    def test_mock_pity_phrasing(self):
        for t in ("oh, poor bot", "aww is the bot sad", "poor baby", "boo hoo bot", "did i hurt your feelings bot"):
            h = Harness(1)
            h.send(*ART, "hey bot", mention=True)
            self.assertEqual(h.send(*ART, t, gap=12).intent, "mock_pity", t)

    def test_genuine_sympathy_still_gracious(self):
        for t in ("are you ok bot", "poor bot", "hope you're fine elon", "sorry he's such a jerk to you"):
            h = Harness(1)
            h.send(9, "Carebear Cubby", "hey bot", mention=True)
            self.assertEqual(h.send(9, "Carebear Cubby", t, gap=12).intent, "kind", t)

    def test_abuse_decays_back_to_gracious(self):
        h = Harness(1)
        h.send(*ART, "hey bot", mention=True)
        h.send(*ART, "fuck you bot", gap=12)
        h.clock.t += 3600  # an hour later the grudge has decayed
        self.assertEqual(h.send(*ART, "are you ok bot", mention=True).intent, "kind")


class TestNewIntents(unittest.TestCase):
    def ask(self, t, seed=1):
        h = Harness(seed)
        h.send(*ART, "hey bot", mention=True)
        return h.send(*ART, t, gap=12)

    def test_called(self):
        for t in ("do you hate being called bot", "do you mind being called clanker", "can i call you elon",
                  "i'll keep calling you bot", "does it bother you"):
            r = self.ask(t)
            if t != "does it bother you":
                self.assertEqual(r.intent, "called", t)

    def test_broke(self):
        for t in ("I think I broke it", "i broke the bot", "is it broken", "you're glitching", "bot is broken lol",
                  "you broke"):
            self.assertEqual(self.ask(t).intent, "broke_it", t)

    def test_no_sense(self):
        for t in ("cuz you aren't making any sense now bot", "you make no sense", "that makes no sense",
                  "what are you even talking about", "you're not making sense"):
            self.assertEqual(self.ask(t).intent, "no_sense", t)

    def test_echo_question_about_last_line(self):
        for seed in range(10):
            h = Harness(seed)
            h.send(*ART, "hey bot", mention=True)
            s = h.convo.chan(1, 10).sessions[ART[0]]
            s.last_bot_line = "Your message got rejected by my vibe firewall."
            r = h.send(*ART, "your vibe firewall???", gap=12)
            self.assertEqual(r.intent, "echo_q")
            self.assertIn("vibe firewall", r.text.lower())
        h = Harness(1)
        h.send(*ART, "hey bot", mention=True)
        s = h.convo.chan(1, 10).sessions[ART[0]]
        s.last_bot_line = "Nice try."
        self.assertNotEqual(h.send(*ART, "your toaster???", gap=12).intent, "echo_q")

    def test_cuz_is_because(self):
        for w in ("cuz", "cause", "bc", "because"):
            r = self.ask(f"{w} i like pizza")
            self.assertEqual(r.intent, "because", w)


class TestReflectionAgreement(unittest.TestCase):
    def test_examples(self):
        cases = {
            "you aren't making any sense now": "I'm not making any sense now",
            "you are slow": "I am slow", "you were wrong": "I was wrong", "you're not funny": "I'm not funny",
            "because you suck": "because I suck", "tired of you": "tired of me", "i love you": "you love me",
            "i am tired": "you are tired", "i was there": "you were there", "am i right": "are you right",
            "you is dumb": "I am dumb", "your mom": "my mom", "i think you are dumb": "you think I am dumb",
        }
        for src, want in cases.items():
            self.assertEqual(convo.reflect(src), want, src)

    def test_fuzz_reflection_agreement(self):
        subj = ["you", "u", "you're", "youre", "i", "i'm", "because you", "cuz you", "i think you", "that you",
                "if you", "when you", "and you", "i know you", "are you", "were you", "aren't you", "am i"]
        verbs = ["are", "aren't", "were", "weren't", "is", "was", "have", "has", "do", "don't", "does", "doesn't",
                 "can't", "will", "make", "am", "", "not", "really are", "just are"]
        tails = ["making any sense", "slow", "a bot", "wrong", "here", "funny", "tired of me", "the best", "broken"]
        n = 0
        for s, v, t in itertools.product(subj, verbs, tails):
            text = " ".join(w for w in (s, v, t) if w)
            out = convo.reflect(text)
            self.assertIsNone(convo.AGREE_BAD_RE.search(out), (text, out))
            n += 1
        self.assertGreater(n, 3000)

    def test_fuzz_replies_have_no_agreement_errors(self):
        prefixes = ["because", "cuz", "i think", "why are", "i feel like", "you know", "should i", "i hate that", ""]
        bodies = ["you aren't making any sense", "you are so slow", "you were wrong", "you're not funny",
                  "you is a bot", "you have no idea", "you can't talk", "you don't get it", "you was there",
                  "you never listen", "you think you're smart", "u r slow"]
        for (p, b), seed in zip(itertools.product(prefixes, bodies), itertools.cycle(range(9))):
            h = Harness(seed)
            h.send(*ART, "hey bot", mention=True)
            text = f"{p} {b}".strip()
            r = h.send(*ART, text, gap=12)
            self.assertIsNotNone(r, text)
            mine = re.sub(r'"[^"]*"', '"Q"', r.text)  # their own words quoted back are theirs, not reflection
            self.assertIsNone(convo.AGREE_BAD_RE.search(mine), (text, r.text))

    def test_slot_filter_rejects_agreement_errors(self):
        for x in ("me aren't making sense", "me are slow", "I are the best", "you am", "I is dumb"):
            self.assertFalse(convo.slot_ok(x, "free"), x)


if __name__ == "__main__":
    unittest.main()
