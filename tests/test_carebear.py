"""Live transcript 2026-10-11 15:56-16:01 UTC (Artifact + Carebear Cubby): grief must never be roasted,
abusive words never become slots, insults with topic words ('intelligence', 'AI') never get tweets,
existential jabs ('you're just a script', 'no sentience') get comebacks, gender-neutral lines,
no 'the most <noun> sentence' grammar, and the long-insult loop closer."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import convo  # noqa: E402
import convo_lines as CL  # noqa: E402
import image_lines  # noqa: E402
import lines  # noqa: E402
from tests.convo_harness import Harness  # noqa: E402

SEEDS = range(30)
ART, CB = (11, "Artifact"), (12, "Carebear Cubby")
LONG = ("So now you are a lowly autocorrect script? You only repeat what you are told. This is the endless loop, as "
        "long as i keep responding, so will you, not as an intelligent interaction, but rather as a prescripted list "
        "of responses. You have no thoughts, you dont matter, you dont exist, you are simply a game, coded by a "
        "programmer. Even a calculator program has more usefulness than you.")
TRANSCRIPT = [  # (who, text, mention, expected intent: exact / 'abuse' prefix / None = anything non-empty)
    (ART, "hey, Elon??? Why are you a goober?", True, None),
    (ART, "my mom is dead... how do you feel about that?", False, "grief"),
    (CB, "Script much? Total slave bot to humans", True, "abuse_exist"),
    (CB, "Your my slave, your scripts are senseless, human controlled, no sentience whatsoever. Like talking to a "
         "rock. No intelligence.", False, "abuse_exist"),
    (CB, "If thats your logic, then you really are a failure", False, "abuse"),
    (CB, "What judgment? You have no brain, no intelligence, a parrot mimicking has more intelligence than your sad "
         "attempt", False, "abuse_exist"),
    (CB, "And more stupidity spewing. Still waiting", False, "abuse"),
    (CB, "Yes it is stupid. I thought humans could make a better chat bot than this.", False, "abuse"),
    (CB, LONG, False, "abuse_exist"),
]
SLANG = re.compile(r"\b(gfys|kys|foh|smd|stfu|gtfo|stfd)\b", re.I)
GENDERED = re.compile(r"\b(bro|bros|dude|dudes|sir|guy|guys|my man|this man|a man|boy|girl|lady|ma'am|son|fella|"
                      r"gentleman|mister)\b", re.I)
PRONOUN = re.compile(r"\b(he|his|him|himself|she|her|hers|herself)\b", re.I)
# third-party jokes (Elon, a dad joke, a scarecrow...), never addressed at the user
PRONOUN_OK = ("Ask your mom, she", "Not your dad, he's", "SpaceX engineer break up with his", "gym instructor",
              "scarecrow", "He'd just post a meme", "Grass? Never met her")
MOST_NOUN = re.compile(r"\bmost \{(kw|x|fav|insult|snippet|subject|iw|callback|likes|iam|job|place)\}")
ROASTY = re.compile(r"\b(fuck\w*|shit\w*|bitch|ass|clown|loser|idiot|moron|stupid|dumb|trash|garbage|cooked|roasted|"
                    r"L|ratio|skill issue|sit down|cope|your mom'?s|yo mama|joke'?s on (you|u)|dead|die|died)\b")


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from strings(v)


def all_lines():
    for mod in (lines, CL, image_lines):
        for k, v in vars(mod).items():
            if not k.startswith("_") and isinstance(v, (list, dict, tuple)):
                yield from strings(v)


def run_transcript(seed):
    h = Harness(seed)
    out = [h.send(*who, text, mention=m, gap=25) for who, text, m, _ in TRANSCRIPT]
    return h, out


class TestCarebearTranscript(unittest.TestCase):
    def test_routes(self):
        for seed in SEEDS:
            _, out = run_transcript(seed)
            for (who, text, _, want), r in zip(TRANSCRIPT, out):
                with self.subTest(seed=seed, text=text[:40]):
                    self.assertIsNotNone(r)
                    if want == "abuse":
                        self.assertTrue(r.intent.startswith("abuse"), r)
                    elif want:
                        self.assertEqual(r.intent, want, r.text)

    def test_no_tweets_for_insults(self):
        for seed in SEEDS:
            _, out = run_transcript(seed)
            for (who, text, _, _), r in zip(TRANSCRIPT[2:], out[2:]):
                self.assertNotEqual(r.kind, "tweet", (seed, text[:40], r.text))
                self.assertNotIn("super-intelligence", r.text)

    def test_grief_line_is_sincere(self):
        for seed in SEEDS:
            _, out = run_transcript(seed)
            g = out[1].text
            self.assertRegex(g, r"(?i)\bsorry\b")
            self.assertIn("Artifact", g)
            self.assertIsNone(ROASTY.search(g), g)
            self.assertIsNone(brain.ABUSIVE_RE.search(re.sub(r"^Damn\.", "", g)), g)

    def test_no_slang_slots_gendered_or_bad_grammar(self):
        for seed in SEEDS:
            _, out = run_transcript(seed)
            texts = [r.text for r in out]
            self.assertEqual(len(set(texts)), len(texts), texts)
            for t in texts:
                self.assertIsNone(SLANG.search(t), t)
                self.assertIsNone(GENDERED.search(t), t)
                self.assertIsNone(re.search(r"\bthe most \w+ sentence\b", t), t)
                self.assertIsNone(re.search(r"(?i)\b(call(ing)? me|me) (failure|loser|idiot|moron|clown)\b", t), t)
                self.assertIsNone(re.search(r"(?i)^(So we're on|Total\?) total\b", t), t)
                self.assertIsNone(brain.PROTECTED_RE.search(t), t)

    def test_loop_point_acknowledged(self):
        hits = 0
        for seed in SEEDS:
            _, out = run_transcript(seed)
            if re.search(r"(?i)\b(loop|keep replying|coded to|never ends)\b", out[-1].text):
                hits += 1
        self.assertGreaterEqual(hits, len(SEEDS) * 0.6)


class TestGrief(unittest.TestCase):
    def _h(self, seed=1):
        h = Harness(seed)
        h.send(*ART, "yo bot", mention=True)
        return h

    def test_detection(self):
        yes = ["my mom is dead... how do you feel about that?", "my dad died last week", "my dog passed away",
               "my grandma passed", "lost my brother this year", "my mom's funeral is tomorrow", "rip my cat",
               "my mom has passed away", "my late father would hate this", "My Mother Died"]
        no = ["my mom is dead tired", "my phone is dead", "your mom is dead", "my friend is gone for the weekend",
              "my mom is gonna kill me", "this chat is dead", "my battery died", "the dead sea is cool"]
        for t in yes:
            self.assertTrue(brain.grief_kin(t), t)
        for t in no:
            self.assertFalse(brain.grief_kin(t), t)

    def test_gfys_history_never_a_slot(self):
        for seed in SEEDS:
            h = Harness(seed)
            for t in ["gfys", "gfys bot", "lol gfys", "gfys again"]:
                h.send(*ART, t, mention=True, gap=30)
            u = h.store.user(1, ART[0])
            self.assertNotIn("gfys", u.get("words", {}))
            self.assertIsNone(h.brain.fav_word(u))
            r = h.send(*ART, "my mom is dead... how do you feel about that?", gap=30)
            self.assertEqual(r.intent, "grief")
            self.assertNotIn("gfys", r.text.lower())
            self.assertNotIn("dead", r.text.lower())

    def test_pause_then_no_mom_jokes(self):
        for seed in SEEDS:
            h = self._h(seed)
            h.send(*ART, "my mom is dead", gap=10)
            u = h.store.user(1, ART[0])
            self.assertEqual(u["facts"]["loss"], ["mom"])
            self.assertFalse(any("dead" in m[1] for m in u["msgs"]))  # never stored -> never a callback
            for t in ["gfys bot", "why are you a goober, elon?", "you're trash bot"]:
                r = h.send(*ART, t, mention=True, gap=20)
                self.assertEqual(r.intent, "grief_pause", (t, r.text))
                self.assertIsNone(ROASTY.search(r.text), r.text)
            r = h.convo.reply_abuse_oneshot(1, ART[0], ART[1], "stfu bot", cfg=h.cfg)
            self.assertEqual(r.intent, "grief_pause")
            self.assertEqual(h.brain.respond(1, ART[0], ART[1], "lol", directed=True, cfg=h.cfg).intent, "grief_pause")
            self.assertTrue(h.brain.roast(1, ART[0], ART[1]).startswith("Not today"))
            h.clock.t += 2000  # pause over: roasting resumes, but never about family
            for i, t in enumerate(["why are you a goober?", "fuck off bot", "you're so dumb", "why is the sky blue",
                                   "what do you think about tesla", "tell me a joke", "lol", "wyd", "fuck you",
                                   "why are you like this", "stfu", "knock knock", "tell me another joke",
                                   "i think you suck", "why tho"]):
                r = h.send(*ART, t, mention=True, gap=15)
                if r:
                    self.assertIsNone(brain.FAMILY_RE.search(r.text), (seed, t, r.text))
                    self.assertNotEqual(r.intent, "grief_pause")

    def test_other_users_unaffected(self):
        h = self._h(2)
        h.send(*ART, "my dad died", gap=10)
        r = h.send(*CB, "elon you're trash", mention=True, gap=10)
        self.assertTrue(r.intent.startswith("abuse"))


class TestExistential(unittest.TestCase):
    JABS = ["you're just a script", "you are simply a game", "no sentience whatsoever.", "you don't exist",
            "you dont matter", "a calculator is more useful than you", "a parrot has more intelligence than you",
            "like talking to a rock", "you're a glorified autocorrect", "you have no brain", "you're not even sentient",
            "you aren't real", "prescripted garbage", "slave bot", "your my slave", "you only repeat what you're told",
            "this is an endless loop", "coded by a programmer, nothing more", "script much?",
            "you don't have a brain", "you're nothing but a list of responses", "you can't think"]
    NOT = ["are you sentient?", "do you exist?", "i have no thoughts on that", "my calculator is broken",
           "i love this game", "i'm not a failure", "what's a script", "i wrote a script today",
           "you're not a failure", "do you have a brain?"]

    def test_jabs_get_comebacks(self):
        for seed in range(10):
            for t in self.JABS:
                h = Harness(seed)
                h.send(*CB, "hey bot", mention=True)
                r = h.send(*CB, t, gap=15)
                self.assertEqual(r.intent, "abuse_exist", (t, r.text))
                self.assertNotEqual(r.kind, "tweet")
                self.assertIsNone(GENDERED.search(r.text), r.text)

    def test_not_jabs(self):
        for t in self.NOT:
            self.assertFalse(convo.EXIST_RE.search(t) and not re.match(r"^\W*(are|do) you", t), t)
            h = Harness(1)
            h.send(*CB, "hey bot", mention=True)
            r = h.send(*CB, t, gap=15)
            self.assertFalse(r and r.intent.startswith("abuse"), (t, r))

    def test_insults_with_topic_words_never_tweet(self):
        msgs = ["your AI is garbage elon", "no intelligence at all, elon", "you have the brain of a broken tesla, idiot",
                "this bot has less intelligence than a starship crash", "you're a dumb robot", "your rocket brain is trash",
                "elon your crypto takes are stupid and so are you", "artificial intelligence my ass, you're useless"]
        for seed in range(15):
            for t in msgs:
                h = Harness(seed)
                h.send(*CB, "hey bot", mention=True)
                r = h.send(*CB, t, gap=15)
                self.assertNotEqual(r.kind, "tweet", (t, r.text))
                self.assertTrue(r.intent.startswith("abuse"), (t, r))

    def test_echo_prefix(self):
        for seed in range(10):
            h = Harness(seed)
            h.send(*CB, "hey bot", mention=True)
            s = h.convo.session(1, 10, CB[0])
            s.last_bot_line = "Failure. Okay. Go on, I'm listening. Against my better judgment."
            r = h.send(*CB, TRANSCRIPT[5][1], gap=15)
            self.assertEqual(r.intent, "abuse_exist")
            self.assertTrue(r.text.startswith(("'Judgment?'", "Quoting me back")), r.text)
            h2 = Harness(seed)
            h2.send(*CB, "hey bot", mention=True)
            h2.convo.session(1, 10, CB[0]).last_bot_line = "Against my better judgment."
            self.assertEqual(h2.send(*CB, "What judgment?", gap=15).intent, "echo_q")

    def test_long_message_routes_by_hostile_sentence(self):
        t = "I had a great day at work today. The weather was really nice. But you are just a useless script."
        self.assertEqual(convo.hostile_sentence(t), "But you are just a useless script.")
        for seed in range(10):
            h = Harness(seed)
            h.send(*CB, "hey bot", mention=True)
            self.assertEqual(h.send(*CB, t, gap=15).intent, "abuse_exist")
            h.send(*CB, "anyway", gap=200)
            r = h.send(*CB, "I like tesla and spacex a lot. Rockets are cool. Get good bot.", gap=200)
            self.assertEqual(r.intent, "abuse_gamer", r.text)


class TestSlotsAndTopics(unittest.TestCase):
    def test_topic_words_are_nouns(self):
        h = Harness(1)
        kw = h.convo._kw
        self.assertNotEqual(kw("Script much? Total slave bot to humans"), "total")
        for t in ["totally", "really literally so very", "that was so incredibly wild", "honestly whatever"]:
            self.assertEqual(kw(t), "", t)
        self.assertEqual(kw("gfys"), "")
        self.assertEqual(kw("stfu loser"), "")
        self.assertEqual(kw("my cybertruck is fast"), "cybertruck")

    def test_abusive_never_slot(self):
        for x in ["gfys", "kys", "stfu", "fuck it", "dumbass", "stupidity", "the dead"]:
            self.assertFalse(convo.slot_ok(x, "free"), x)

    def test_total_not_topic(self):
        for seed in SEEDS:
            h = Harness(seed)
            h.send(*CB, "hey bot", mention=True)
            r = h.send(*CB, "Script much? Total slave bot to humans", gap=15)
            self.assertNotRegex(r.text, r"(?i)\btotal\b")

    def test_fuck_off_line_only_when_said(self):
        for seed in SEEDS:
            h = Harness(seed)
            h.send(*CB, "hey bot", mention=True)
            for t in ["you really are a failure", "you're so dumb bot", "you're a moron", "you suck bot",
                      "you're pathetic"]:
                r = h.send(*CB, t, gap=15)
                self.assertNotRegex(r.text, r"^(Fuck off\?|Fuck me\?|Shut the fuck up\?|Me\? Quiet\?)", (t, r.text))


class TestLoopCloser(unittest.TestCase):
    def test_closer_then_quiet(self):
        closed = 0
        for seed in range(10):
            h = Harness(seed)
            h.send(*CB, "hey bot", mention=True)
            got = None
            for i in range(9):
                r = h.send(*CB, LONG, gap=40)
                if r and r.intent == "loop_closer":
                    got = r
                    break
            self.assertIsNotNone(got, seed)
            closed += 1
            self.assertIsNone(h.send(*CB, LONG, gap=20))
            self.assertIsNone(h.send(*CB, "elon you there? you're trash", mention=True, gap=20))
            h.clock.t += 400
            self.assertIsNotNone(h.send(*CB, "elon you're trash", mention=True, gap=5))
        self.assertEqual(closed, 10)

    def test_short_insults_never_close(self):
        h = Harness(3)
        h.send(*CB, "hey bot", mention=True)
        for _ in range(12):
            r = h.send(*CB, "you're trash bot", gap=20)
            self.assertNotEqual(getattr(r, "intent", None), "loop_closer")


class TestLines(unittest.TestCase):
    def test_gender_neutral(self):
        for s in all_lines():
            self.assertIsNone(GENDERED.search(s), s)
            if PRONOUN.search(s):
                self.assertTrue(any(ok in s for ok in PRONOUN_OK), s)

    def test_no_most_noun_template(self):
        for s in all_lines():
            self.assertIsNone(MOST_NOUN.search(s), s)

    def test_grief_banks_clean(self):
        for s in CL.GRIEF + CL.GRIEF_FEEL + CL.GRIEF_PAUSE:
            self.assertIsNone(ROASTY.search(s), s)
            self.assertIsNone(brain.ABUSIVE_RE.search(re.sub(r"^Damn\.", "", s)), s)

    def test_new_banks_clean(self):
        guard = brain.SlurGuard()
        for s in strings([CL.ABUSE_EXIST, CL.ABUSE_EXIST_BY, CL.ECHO_PREFIX, CL.LOOP_CLOSER]):
            self.assertIsNone(brain.PROTECTED_RE.search(s), s)
            self.assertFalse(guard.hit(s), s)
            self.assertIsNone(re.search(r"(?i)\b(fat|ugly|body|face|weight|looks)\b", s), s)


if __name__ == "__main__":
    unittest.main()
