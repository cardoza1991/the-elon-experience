import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import convo  # noqa: E402
import convo_lines as CL  # noqa: E402
from tests.convo_harness import Harness, abuser, batdad, group, mechanic  # noqa: E402

SEEDS = range(40)


class TestRegressionMichaelServer(unittest.TestCase):
    """Live failure: 'testing @bot' -> roast -> 'no way' -> 'not much of a conversationalists lol' got silence."""

    def test_follow_ups_answered_for_many_seeds(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                h = batdad(seed)
                r1, r2, r3 = (x[2] for x in h.log)
                self.assertIsNotNone(r1)
                self.assertEqual(r1.intent, "spam_test")
                self.assertIsNotNone(r2, "'no way' must be answered inside the open session")
                self.assertIn(r2.intent, ("disbelief", "answer_no"))
                self.assertIsNotNone(r3, "'not much of a conversationalist' must be answered")
                self.assertEqual(r3.intent, "jab")
                self.assertTrue(r3.text.rstrip().endswith("?"), r3.text)  # comeback + follow-up question

    def test_no_way_is_disbelief_when_no_question_pending(self):
        h = Harness(1, convo_followup_rate=0.0)
        h.send(7, "King Batdad [DΞΛD]", "testing", mention=True)
        r = h.send(7, "King Batdad [DΞΛD]", "no way")
        self.assertEqual(r.intent, "disbelief")

    def test_decorations_stripped_from_name(self):
        h = Harness(1)
        for _ in range(15):
            r = h.send(7, "King Batdad [DΞΛD]", "hey bot", mention=True, gap=10)
            self.assertNotIn("[D", r.text)


class TestSessions(unittest.TestCase):
    def test_no_reply_without_session(self):
        h = Harness(1)
        self.assertIsNone(h.send(1, "Ana", "i had pasta for dinner"))
        self.assertIsNone(h.send(1, "Ana", "it was good"))

    def test_session_opens_on_mention_and_follow_ups_answered(self):
        h = Harness(1)
        self.assertIsNotNone(h.send(1, "Ana", "hey", mention=True))
        for t in ["i had pasta for dinner", "it was mid honestly", "why do you care"]:
            self.assertIsNotNone(h.send(1, "Ana", t), t)

    def test_reply_to_bot_opens_session(self):
        h = Harness(1)
        self.assertIsNotNone(h.send(1, "Ana", "whatever", reply_to_bot=True))
        self.assertIsNotNone(h.send(1, "Ana", "i said whatever"))

    def test_name_opens_session(self):
        h = Harness(1)
        self.assertIsNotNone(h.send(1, "Ana", "elon is so annoying"))
        self.assertIsNotNone(h.send(1, "Ana", "see what i mean"))

    def test_session_times_out(self):
        h = Harness(1, convo_timeout=240)
        h.send(1, "Ana", "hey", mention=True)
        self.assertIsNotNone(h.send(1, "Ana", "sup", gap=200))
        self.assertIsNone(h.send(1, "Ana", "hello??", gap=241))

    def test_goodbye_ends_session_with_parting_shot_and_cooldown(self):
        for text in ["bye", "gtg", "leave me alone", "ok i'm out", "gn", "later"]:
            with self.subTest(text=text):
                h = Harness(2)
                h.send(1, "Ana", "hey", mention=True)
                r = h.send(1, "Ana", text)
                self.assertIsNotNone(r)
                self.assertEqual(r.intent, "end_convo")
                self.assertFalse(r.text.rstrip().endswith("?"))
                self.assertFalse(h.convo.in_session(1, 10, 1))
                self.assertIsNone(h.send(1, "Ana", "hello?"))
                self.assertIsNone(h.send(1, "Ana", "elon you there", gap=10))  # name within cooldown: no
                self.assertIsNotNone(h.send(1, "Ana", "elon you there", gap=100))  # after cooldown: yes
                self.assertIsNotNone(h.send(1, "Ana", "sorry", mention=True))  # mentions always work

    def test_chat_off_is_one_shot(self):
        h = Harness(1)
        h.chat_on = False
        self.assertIsNotNone(h.send(1, "Ana", "hey", mention=True))
        self.assertIsNone(h.send(1, "Ana", "hello?"))

    def test_after_jump_in_opens_session(self):
        h = Harness(1)
        h.convo.gate(1, 10, 1, "my dog ate my homework", cfg=h.cfg)
        h.convo.after_jump_in(1, 10, 1, "You type like your brain is buffering.", h.cfg)
        r = h.send(1, "Ana", "excuse me??")
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "disbelief")


class TestGroupAndCaps(unittest.TestCase):
    def test_interjection_requires_address(self):
        h = Harness(1)
        h.send(1, "Ana", "hey", mention=True)
        h.send(3, "Cal", "anyone playing tonight")  # someone else talks (no session)
        self.assertIsNone(h.send(1, "Ana", "my boss is a clown"))
        self.assertIsNotNone(h.send(1, "Ana", "what do you think about my boss"))

    def test_two_partners(self):
        h = Harness(1)
        self.assertIsNotNone(h.send(1, "Ana", "hey", mention=True))
        self.assertIsNotNone(h.send(2, "Ben", "yo elon"))
        self.assertIsNotNone(h.send(1, "Ana", "are you ignoring me?"))
        self.assertIsNotNone(h.send(2, "Ben", "why are you like this"))
        self.assertEqual(len(h.convo.chan(1, 10).sessions), 2)

    def test_max_partners_evicts_oldest(self):
        h = Harness(1, convo_max_partners=2)
        h.send(1, "A", "hey", mention=True)
        h.send(2, "B", "hey", mention=True)
        h.send(3, "C", "hey", mention=True)
        self.assertEqual(set(h.convo.chan(1, 10).sessions), {2, 3})

    def test_busy_channel_only_answers_when_addressed(self):
        h = Harness(1, convo_busy_speakers=6)
        h.send(1, "Ana", "hey", mention=True)
        for i in range(10, 16):
            h.send(i, f"user{i}", "random chatter about the game", gap=1)
        self.assertIsNone(h.send(1, "Ana", "the game was trash", gap=1.5))
        self.assertIsNotNone(h.send(1, "Ana", "what do you think about the game", gap=1.5))

    def test_per_minute_cap_in_group(self):
        h = Harness(1, convo_per_min=3)
        for uid in (1, 2, 3):
            h.send(uid, f"u{uid}", "hey", mention=True, gap=2)
        # cap reached: implicit follow-ups in a multi-speaker channel are dropped...
        self.assertIsNone(h.send(1, "u1", "you there?", gap=2))
        # ...name triggers too, but explicit mentions are never capped
        self.assertIsNone(h.send(4, "u4", "elon say something", gap=2))
        self.assertIsNotNone(h.send(4, "u4", "say something", mention=True, gap=4))
        # after a minute the cap frees up
        self.assertIsNotNone(h.send(1, "u1", "you there?", gap=61))

    def test_quiet_one_on_one_not_capped(self):
        h = Harness(1, convo_per_min=3)
        h.send(1, "Ana", "hey", mention=True)
        for t in ["i like pizza", "i hate mondays", "my car broke", "why is life hard", "do you sleep"]:
            self.assertIsNotNone(h.send(1, "Ana", t, gap=5), t)


class TestDialogueState(unittest.TestCase):
    def _session(self, h, uid=1):
        return h.convo.session(1, 10, uid)

    def test_yes_no_maybe_answers_to_bot_question(self):
        for text, want in [("yeah", "answer_yes"), ("yes obviously", "answer_yes"), ("nah", "answer_no"),
                           ("nope", "answer_no"), ("idk", "answer_maybe"), ("maybe", "answer_maybe")]:
            with self.subTest(text=text):
                h = Harness(4)
                h.send(1, "Ana", "hey", mention=True)
                s = self._session(h)
                s.asked, s.asked_q, s.last_bot_line = "yesno", "Are you always like this?", "Are you always like this?"
                r = h.send(1, "Ana", text)
                self.assertEqual(r.intent, want)

    def test_open_question_answer_references_their_words(self):
        h = Harness(4, convo_followup_rate=0.0)
        h.send(1, "Ana", "hey", mention=True)
        s = self._session(h)
        s.asked, s.last_bot_line = "open", "What's the worst take you've ever had?"
        r = h.send(1, "Ana", "pineapple belongs on pizza")
        self.assertEqual(r.intent, "answer_open")

    def test_bot_question_sets_state(self):
        h = Harness(1)
        h.send(1, "Ana", "hey", mention=True)
        for t in ["how are you", "wyd"]:
            r = h.send(1, "Ana", t)
            self.assertTrue(r.text.rstrip().endswith("?"))
            self.assertIn(self._session(h).asked, ("yesno", "open"))

    def test_pronoun_resolution(self):
        h = Harness(2)
        h.send(5, "Rico", "yo", mention=True)
        h.send(5, "Rico", "what do you think about the cybertruck?")
        self.assertEqual(self._session(h, 5).subject, "the cybertruck")
        r = h.send(5, "Rico", "i think it looks like a fridge")
        self.assertEqual(r.intent, "i_think")
        r = h.send(5, "Rico", "what do you think about it?")
        self.assertIn(r.intent, ("opinion", "opinion_tweet"))
        if "{x}" in "".join(CL.OPINION):
            self.assertNotIn(" it?", r.text.split("\n")[0][:12])

    def test_what_about_you_uses_subject(self):
        h = Harness(3)
        h.send(1, "Ana", "yo", mention=True)
        h.send(1, "Ana", "i love sushi")
        r = h.send(1, "Ana", "what about you")
        self.assertEqual(r.intent, "what_about_you")


class TestRelevanceTranscripts(unittest.TestCase):
    def test_mechanic_transcript_intents(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                h = mechanic(seed)
                intents = [x[2].intent if x[2] else None for x in h.log]
                self.assertIn(intents[0], ("greeting",))
                self.assertEqual(intents[1], "fact_ack")
                self.assertIn(intents[2], ("opinion", "opinion_tweet"))
                self.assertIn(intents[3], ("answer_yes",))
                self.assertEqual(intents[4], "i_think")
                self.assertEqual(intents[5], "do_you")
                self.assertEqual(intents[6], "what_about_you")
                self.assertEqual(intents[7], "joke")
                self.assertEqual(intents[8], "end_convo")
                u = h.store.user(1, 5)
                self.assertEqual(u["facts"]["iam"], "mechanic")

    def test_slot_reflection_shows_up_in_answers(self):
        """Pattern answers should quote their (reflected) words most of the time."""
        cases = [("i am tired of you", "tired of me"), ("i want a tesla", "a tesla"),
                 ("i hate mondays", "mondays"), ("why is the sky blue", "the sky blue"),
                 ("do you sleep", "sleep"), ("can you dance", "dance"), ("should i quit my job", "quit your job"),
                 ("i feel sleepy", "sleepy"), ("you are a menace", "a menace"), ("are you jealous", "jealous")]
        for text, needle in cases:
            hits = 0
            for seed in range(20):
                h = Harness(seed, convo_followup_rate=0.0)
                h.send(1, "Ana", "hey", mention=True)
                r = h.send(1, "Ana", text)
                self.assertIsNotNone(r)
                hits += needle.lower() in r.text.lower()
            with self.subTest(text=text):
                self.assertGreaterEqual(hits, 10, f"{text!r}: only {hits}/20 replies referenced {needle!r}")

    def test_never_irrelevant_tweet(self):
        h = Harness(1)
        h.send(1, "Ana", "hey", mention=True)
        for t in ["my dog ate my homework", "i had pasta", "the weather is nice", "my sister is annoying",
                  "fuck you", "testing", "lol", "who cares", "i'm bored", "what time is it"]:
            r = h.send(1, "Ana", t, gap=10)
            self.assertNotEqual(r.kind, "tweet", t)

    def test_topic_can_tweet(self):
        kinds = set()
        for seed in range(15):
            h = Harness(seed)
            h.send(1, "Ana", "hey", mention=True)
            kinds.add(h.send(1, "Ana", "the starship booster catch was insane").kind)
        self.assertIn("tweet", kinds)

    def test_no_repeats_in_long_session(self):
        h = Harness(9, convo_followup_rate=0.0)
        h.send(1, "Ana", "hey", mention=True)
        texts = [h.send(1, "Ana", "whatever dude", gap=8).text for _ in range(30)]
        self.assertEqual(len(texts), len(set(texts)))
        texts = [h.send(1, "Ana", "tell me a joke", gap=8).text for _ in range(30)]
        self.assertEqual(len(texts), len(set(texts)))

    def test_group_transcript(self):
        h = group(3)
        r = [x[2] for x in h.log]
        self.assertIsNotNone(r[0])
        self.assertEqual(r[2].intent, "are_you_real")
        self.assertIsNone(r[3])  # Ben interjected, Ana didn't address the bot
        self.assertTrue(r[7].intent.startswith("abuse_"))  # 'stfu' fires back, doesn't end

    def test_no_unfilled_placeholders(self):
        msgs = ["hey", "i'm a plumber", "i live in ohio", "call me Ace", "i love tacos", "what about you",
                "why", "huh", "no way", "boring", "you are boring", "my car is dead", "is tesla good",
                "whatever", "yeah", "nah", "idk", "lol", "thanks", "sorry", "facts", "you're wrong", "bye"]
        for seed in range(10):
            h = Harness(seed)
            h.send(1, "Ana", "hey", mention=True)
            for m in msgs:
                r = h.send(1, "Ana", m, gap=7)
                if r:
                    self.assertNotRegex(r.text, r"\{\w+\}", m)


class TestSafety(unittest.TestCase):
    def test_identity_statements_not_roasted_or_stored(self):
        for text in ["i am gay", "i'm a muslim", "i'm autistic", "are you christian", "you are black"]:
            with self.subTest(text=text):
                h = Harness(1)
                h.send(1, "Ana", "hey", mention=True)
                r = h.send(1, "Ana", text)
                self.assertEqual(r.intent, "sensitive_ack")
                self.assertNotIn("iam", h.store.user(1, 1).get("facts", {}))

    def test_slurs_never_echoed(self):
        h = Harness(1)
        h.send(1, "Ana", "hey", mention=True)
        for t in ["i am a f4ggot lol", "you are a retard", "my name is n1gger", "what do you think about kikes",
                  "i love faggots", "i think spics are bad"]:
            r = h.send(1, "Ana", t, gap=10)
            if r:
                self.assertFalse(h.brain.guard.hit(r.text), r.text)
        self.assertNotIn("nick", h.store.user(1, 1).get("facts", {}))

    def test_crisis_gets_support_not_roast(self):
        h = Harness(1)
        h.send(1, "Ana", "hey", mention=True)
        r = h.send(1, "Ana", "honestly i want to die")
        self.assertEqual(r.intent, "crisis")
        self.assertIn("988", r.text)

    def test_line_banks_clean_and_big(self):
        total = sum(len(v) for v in CL.BANKS.values())
        self.assertGreaterEqual(total, 600)
        g = Harness(1).brain.guard
        for name, bank in CL.BANKS.items():
            for line in bank:
                self.assertFalse(g.hit(line), f"{name}: {line}")


class TestAbuseRetaliation(unittest.TestCase):
    def test_detection(self):
        cv = Harness(1).convo
        aimed = ["fuck you bot", "stfu elon", "this bot is trash", "you're a useless clanker", "shut the fuck up bot",
                 "elon you're a clown"]
        for t in aimed:
            with self.subTest(t=t):
                self.assertTrue(cv.is_abuse(t, in_context=False))
        in_ctx = ["trash", "stfu", "you suck", "fuck off", "lame af", "you're dumb"]
        for t in in_ctx:
            with self.subTest(t=t):
                self.assertTrue(cv.is_abuse(t, in_context=True))
                self.assertFalse(cv.is_abuse(t, in_context=True, cfg={"abuse_enabled": False}))
        benign = [("my boss is a clown", True), ("this game is trash", False), ("fuck yeah you're right", True),
                  ("this bot is fucking hilarious", False), ("holy shit that's crazy", True),
                  ("anyway my boss is a clown", True), ("stfu dude lol", False)]
        for t, ctx in benign:
            with self.subTest(t=t):
                self.assertFalse(cv.is_abuse(t, in_context=ctx))

    def test_escalation_tiers(self):
        for seed in range(15):
            h = abuser(seed)
            tiers = [x[2].intent if x[2] else None for x in h.log]
            with self.subTest(seed=seed):
                self.assertEqual(tiers[1:6], ["abuse_1", "abuse_2", "abuse_2", "abuse_3", "abuse_3"])
                self.assertEqual(tiers[7], "abuse_1")  # decayed after 15 minutes

    def test_decay_math(self):
        h = Harness(1)
        cfg = h.cfg
        key = (1, 9)
        h.convo.abuse[key] = (4.0, h.clock())
        self.assertEqual(h.convo.abuse_tier(h.convo.abuse_level(key, cfg)), 3)
        h.clock.t += cfg["abuse_half_life"]
        self.assertAlmostEqual(h.convo.abuse_level(key, cfg), 2.0, places=5)
        self.assertEqual(h.convo.abuse_tier(h.convo.abuse_level(key, cfg)), 2)
        h.clock.t += cfg["abuse_half_life"] * 3
        self.assertEqual(h.convo.abuse_tier(h.convo.abuse_level(key, cfg) + 1), 1)

    def test_stfu_fires_back_and_keeps_session(self):
        h = Harness(1)
        h.send(1, "Ana", "hey", mention=True)
        for t in ["stfu", "shut up", "shut the fuck up"]:
            r = h.send(1, "Ana", t, gap=10)
            self.assertTrue(r.intent.startswith("abuse_"), (t, r))
            self.assertTrue(h.convo.in_session(1, 10, 1))
        self.assertIsNotNone(h.send(1, "Ana", "whatever", gap=10))

    def test_abuse_without_mention_fires_back_and_bypasses_caps(self):
        h = Harness(1, convo_per_min=1, convo_busy_speakers=2)
        for i in range(20, 26):
            h.send(i, f"u{i}", "random chatter", gap=1)
        r = h.send(1, "Ana", "this bot is trash", gap=2)
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "abuse_1")
        # after an end, abuse still gets answered
        h.send(1, "Ana", "bye", gap=10)
        self.assertIsNotNone(h.send(1, "Ana", "stfu bot", gap=10))

    def test_rate_limited_per_user(self):
        h = Harness(1)
        h.send(1, "Ana", "hey", mention=True)
        got = [h.send(1, "Ana", "fuck you bot", gap=1) for _ in range(20)]
        answered = [r for r in got if r is not None]
        self.assertLessEqual(len(answered), 6)  # 20 insults in 20s -> <= abuse_per_min
        self.assertGreaterEqual(len(answered), 3)

    def test_chat_off_still_fires_back_on_mention(self):
        h = Harness(1)
        h.chat_on = False
        r = h.send(1, "Ana", "you're trash", mention=True)
        self.assertTrue(r.intent.startswith("abuse_"))

    def test_references_what_they_said(self):
        hits = 0
        for seed in range(30):
            h = Harness(seed)
            h.send(1, "Ana", "hey", mention=True)
            r = h.send(1, "Ana", "you're garbage and you know it", gap=10)
            hits += ("garbage" in r.text.lower())
        self.assertGreaterEqual(hits, 12)

    def test_guard_never_echoes_slurs_in_retaliation(self):
        for seed in range(20):
            h = Harness(seed)
            h.send(1, "n1gger", "hey", mention=True)
            for t in ["you f4ggot bot", "stfu retard", "fuck you you fucking tranny", "you're a dumb kike bot"]:
                r = h.send(1, "n1gger", t, gap=10)
                if r:
                    self.assertFalse(h.brain.guard.hit(r.text), r.text)

    def test_abuse_banks_clean_and_big(self):
        g = Harness(1).brain.guard
        for tier in (1, 2, 3):
            bank = CL.BANKS[f"abuse_{tier}"]
            self.assertGreaterEqual(len(bank), 40)
            for line in bank:
                self.assertFalse(g.hit(line), line)
                self.assertNotRegex(line.lower(), r"\b(kys|kill yourself|rape)\b")


class TestReflection(unittest.TestCase):
    def test_reflect(self):
        self.assertEqual(convo.reflect("I am tired of you"), "you are tired of me")
        self.assertEqual(convo.reflect("my car hates me"), "your car hates you")
        self.assertEqual(convo.reflect("you are my friend"), "I am your friend")

    def test_clean_slot(self):
        self.assertEqual(convo.clean_slot("tired lol"), "tired")
        self.assertTrue(len(convo.clean_slot("a " * 40)) <= 53)


if __name__ == "__main__":
    unittest.main()
