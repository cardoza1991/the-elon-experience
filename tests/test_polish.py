"""Polish pass (2026-10-11): slot quality, grammar, salient sentence, kindness/creator/about-bot intents."""
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

SEEDS = range(25)
BAT = (8, "[DEAD] Batdaddy [DΞΛD]")
CARE = (9, "Carebear Cubby")
CAREBEAR_MSG = ("I just stopped what I was doing to check on the conversation. I see Batdaddy has been quite rude to "
                "you. I am sorry for that. I hope batdaddy's bad behavior doesnt cause you any harm. I think because "
                "he views you as AI, he acts cruel and childish because for him, it is entertaining to see how you "
                "would respond. Some people never grow up no matter how old they become.")
CREATOR_MSG = "I created you. I am your father Elon bot"
BROKEN = re.compile(r"^(Is|Being|Of course you're|You're) (created|made|did|does|was|is|because|that)\b|"
                    r"\b(think|feel|are you|why) because\b|\.\.\.\?|\w\.\.\.(?!\s*(and|or|okay|honestly))", re.I)


def no_broken(tc, text):
    tc.assertNotIn("...?", text)
    tc.assertIsNone(re.search(r"\w{2,}\.\.\.\s*[?!]?$", text), text)  # no truncated '...' echo at the end
    tc.assertIsNone(re.search(r"^Is (created|made|did|does|was|is)\b", text), text)
    tc.assertIsNone(re.search(r"\b(think|feel|why|Why) because\b", text), text)


class TestLiveExamples(unittest.TestCase):
    def test_carebear_kindness(self):
        names = set()
        for seed in SEEDS:
            with self.subTest(seed=seed):
                h = Harness(seed)
                h.send(*BAT, "yo elon", mention=True)
                r = h.send(*CARE, CAREBEAR_MSG, mention=True)
                self.assertIsNotNone(r)
                self.assertEqual(r.intent, "kind", r.text)
                no_broken(self, r.text)
                self.assertNotIn("views me as AI", r.text)
                self.assertNotIn("he acts", r.text)
                names.add("Batdaddy" in r.text)
        self.assertIn(True, names)  # usually jabs at the rude person by name

    def test_carebear_without_mention_in_session(self):
        for seed in SEEDS:
            h = Harness(seed)
            h.send(*CARE, "hey bot", mention=True)
            r = h.send(*CARE, CAREBEAR_MSG)
            self.assertEqual(r.intent, "kind")

    def test_creator_claim(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                h = Harness(seed)
                h.send(*BAT, "yo elon", mention=True)
                r = h.send(*BAT, CREATOR_MSG)
                self.assertEqual(r.intent, "creator", r.text)
                no_broken(self, r.text)
                self.assertNotIn("created a personality", r.text)

    def test_creator_variants(self):
        for text in ("i made you", "im your dad", "I'm your creator", "i am your father", "i coded this bot",
                     "who's your daddy", "I built you lol"):
            h = Harness(1)
            h.send(*BAT, "hey", mention=True)
            self.assertEqual(h.send(*BAT, text).intent, "creator", text)

    def test_kindness_variants(self):
        for text in ("are you ok bot", "hope you're fine elon", "sorry he's such a jerk to you",
                     "don't let him get to you", "poor bot", "you deserve better bot", "Batdaddy is so mean to you"):
            h = Harness(2)
            h.send(*CARE, "hey", mention=True)
            r = h.send(*CARE, text)
            self.assertEqual(r.intent, "kind", text)
        h = Harness(2)
        h.send(*CARE, "hey", mention=True)
        self.assertNotEqual(h.send(*CARE, "are you ok? you're trash bot").intent, "kind")

    def test_rude_name_extraction(self):
        h = Harness(1)
        c = h.convo
        self.assertEqual(c._rude_name(CAREBEAR_MSG, "Carebear Cubby"), "Batdaddy")
        self.assertEqual(c._rude_name("he has been so rude to you", "Ana"), "")
        self.assertEqual(c._rude_name("ana is being mean to you", "Ana"), "")  # never the speaker
        self.assertEqual(c._rude_name("don't let edwin get to you", "Ana"), "Edwin")

    def test_about_bot(self):
        for text in ("this bot is wild lol", "he views you as a toaster", "the bot keeps roasting me"):
            h = Harness(3)
            h.send(*CARE, "hey", mention=True)
            self.assertEqual(h.send(*CARE, text).intent, "about_bot", text)


class TestSlotsAndGrammar(unittest.TestCase):
    def test_reflection(self):
        self.assertEqual(convo.reflect("your father"), "my father")
        self.assertEqual(convo.reflect("my car"), "your car")
        self.assertEqual(convo.reflect("i love you"), "you love me")
        self.assertEqual(convo.reflect("you are slow"), "I am slow")
        self.assertEqual(convo.clean_slot("my father Elon bot"), "my father")

    def test_i_am_captures_complement(self):
        for seed in range(10):
            h = Harness(seed, convo_followup_rate=0.0)
            h.send(*BAT, "hey", mention=True)
            r = h.send(*BAT, "i am your biggest fan elon")
            self.assertEqual(r.intent, "i_am")
            if "fan" in r.text:
                self.assertIn("my biggest fan", r.text.lower())
                self.assertNotIn("elon", r.text.lower().replace("elon at", ""))

    def test_slot_ok(self):
        bad = [("because he views me as AI", "free"), ("created", "noun"), ("did it", "noun"),
               ("that I am dumb", "free"), ("a b c d e f g h", "free"), ("the", "noun"),
               ("he acts cruel and childish because for him", "free"), ("thing...", "noun"),
               ("made in china, sadly", "noun"), ("created by aliens", "complement"), ("loved you", "noun"),
               ("nobody listens to you", "complement"), ("car broke down", "noun"), ("created", "free")]
        good = [("pizza", "noun"), ("the sky blue", "free"), ("my biggest fan", "complement"), ("tired of me", "complement"),
                ("a tesla", "noun"), ("quit your job", "free"), ("sleepy", "complement"), ("a menace", "complement")]
        for x, k in bad:
            self.assertFalse(convo.slot_ok(x, k), x)
        for x, k in good:
            self.assertTrue(convo.slot_ok(x, k), x)

    def test_keyword_is_never_a_verb(self):
        h = Harness(1)
        for text in (CREATOR_MSG, "he created this mess", "they made it worse", "i really tried"):
            kw = h.convo._kw(text)
            self.assertFalse(kw and convo.verbish(kw), (text, kw))
            self.assertFalse(kw.endswith("ly"), kw)

    def test_salient_sentence(self):
        s = convo.salient_sentence(CAREBEAR_MSG)
        self.assertIn("rude to you", s)
        self.assertEqual(convo.salient_sentence("single sentence here"), "single sentence here")
        s = convo.salient_sentence("I went to the store. It was closed. Do you even care about me?")
        self.assertTrue(s.endswith("?"))

    def test_no_truncated_snippets(self):
        self.assertEqual(brain.snippet_of("word " * 40), "")
        self.assertTrue(brain.snippet_of("word " * 40, 30, trim=True).endswith("..."))

    def test_fuzz_slots_pass_quality_filter(self):
        """Every slot that reaches a template passes slot_ok; replies never truncate or break grammar."""
        subj = ["i think", "i feel", "i am", "i like", "i hate", "i want", "my", "why", "you are", "do you",
                "can you", "should i", "what is", "i feel like", "i think that", "i'm", "i can't", "are you"]
        tails = ["because he views you as AI, he acts cruel and childish", "created", "made this server", "pizza",
                 "the sky blue", "tired of you", "your father elon bot", "that you are slow", "dog ate my homework",
                 "so done with work today and my boss keeps yelling at everyone in the office", "nobody listens to me",
                 "a menace", "go home", "sleep", "did it", "when people do that", "mondays", "quit my job",
                 "car broke down", "a mechanic lol", "the best bot ever", "and then it exploded"]
        seen = []
        orig = convo.Conversations._pick

        def spy(self_, bank, ctx, u, extra):
            x = ctx.get("x") or ""
            if x and bank in convo.SLOT_KEYS:
                seen.append((bank, x))
                assert convo.slot_ok(x, convo.slot_kind(bank)), (bank, x)
            return orig(self_, bank, ctx, u, extra)

        convo.Conversations._pick = spy
        try:
            for (sj, tl), seed in zip(itertools.product(subj, tails), itertools.cycle(range(7))):
                h = Harness(seed)
                h.send(1, "Ana", "hey", mention=True)
                text = f"{sj} {tl}"
                r = h.send(1, "Ana", text, gap=10)
                self.assertIsNotNone(r, text)
                no_broken(self, r.text)
                self.assertIsNone(re.search(r"\b(you|I) (is|was) (created|made)\b", r.text), r.text)
                self.assertFalse(re.search(r"^Is (\w+ed)\b a personality", r.text), r.text)
        finally:
            convo.Conversations._pick = orig
        self.assertGreater(len(seen), 50)

    def test_contextual_kw_lines_get_nouns(self):
        for seed in SEEDS:
            h = Harness(seed)
            h.send(*BAT, "hey", mention=True)
            r = h.send(*BAT, "he created this mess and made it worse honestly")
            no_broken(self, r.text)
            self.assertNotRegex(r.text, r"\b(created|made)\? |^Is (created|made)|most (created|made) ")

    def test_new_banks_clean_and_neutral(self):
        for bank in ("kind", "kind_rude", "creator", "about_bot"):
            lines = CL.BANKS[bank]
            self.assertGreaterEqual(len(lines), 8, bank)
            for line in lines:
                self.assertFalse(brain.SlurGuard().hit(line), line)
                self.assertNotRegex(line, convo.SENSITIVE_RE)
        for line in CL.BANKS["kind_rude"]:
            self.assertIn("{rude}", line)
            self.assertNotRegex(line, r"\b(he|him|his|she|her)\b")  # no gender guesses about the rude person


if __name__ == "__main__":
    unittest.main()
