import os
import random
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import lines as L  # noqa: E402
from store import DEFAULT_CONFIG, Store  # noqa: E402

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "elon_tweets_sample.csv")
TWEETS = brain.load_tweets(CSV) or [
    "Starship launch next week, the rocket is ready", "Tesla Cybertruck production is ramping",
    "Mars here we come", "Twitter is the de facto town square", "AI is far more dangerous than nukes",
    "Dogecoin to the moon", "The most entertaining outcome is the most likely",
]


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(seed=1):
    clock = FakeClock()
    b = brain.Brain(Store(None), TWEETS, rng=random.Random(seed), clock=clock)
    return b, clock


class TestRouter(unittest.TestCase):
    def test_intents(self):
        cases = {
            ("fuck you", True): "insult_bot",
            ("this bot is trash", False): "insult_bot",
            ("stfu clanker", False): "insult_bot",
            ("testing", False): "spam_test",
            ("test", True): "spam_test",
            ("aaaaaaaaaaaaaa", False): "spam_test",
            ("lol lol lol lol", False): "spam_test",
            ("lmao", False): "laugh",
            ("hahaha", False): "laugh",
            ("yo", False): "greeting",
            ("hey whats up guys", False): "greeting",
            ("gn everyone", False): "farewell",
            ("good bot", False): "praise_bot",
            ("why is the sky blue?", False): "question",
            ("how do i get better at this game", False): "question",
            ("my cybertruck broke down again", False): "topic",
            ("starship launch tomorrow", False): "topic",
            ("bitcoin is crashing lmao", False): "topic",
            ("i think twitter is dead", False): "topic",
            ("ok", False): "low_effort",
            ("bruh", False): "low_effort",
            ("my dog ate my homework yesterday", False): "generic",
        }
        for (text, directed), want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(brain.classify(text, directed)[0], want)

    def test_insult_not_aimed_is_not_comeback(self):
        self.assertNotEqual(brain.classify("this pizza is trash", False)[0], "insult_bot")

    def test_topics(self):
        self.assertEqual(brain.detect_topic("my tesla needs new tires"), "Tesla")
        self.assertEqual(brain.detect_topic("did you see the starship launch"), "SpaceX")
        self.assertEqual(brain.detect_topic("I posted it on X yesterday"), "X/Twitter")
        self.assertIsNone(brain.detect_topic("I had pasta for dinner"))

    def test_fuck_you_never_gets_a_tweet(self):
        b, _ = make()
        for i in range(50):
            r = b.respond(1, 2, "Mike", "fuck you", directed=True, cfg=DEFAULT_CONFIG)
            self.assertEqual(r.intent, "insult_bot")
            self.assertEqual(r.kind, "line")

    def test_topic_gets_relevant_tweet_above_threshold(self):
        b, _ = make()
        r = b.respond(1, 2, "Mike", "the starship rocket launch was insane", directed=True, cfg=DEFAULT_CONFIG)
        self.assertEqual(r.intent, "topic")
        if r.kind == "tweet":
            self.assertGreaterEqual(r.score, DEFAULT_CONFIG["tweet_threshold"])
            terms = set(brain.content_terms(r.text))
            self.assertTrue(terms & brain.TOPIC_TERMS["SpaceX"])

    def test_topic_below_threshold_falls_back_to_line(self):
        b, _ = make()
        cfg = dict(DEFAULT_CONFIG, tweet_threshold=0.99)
        r = b.respond(1, 2, "Mike", "my tesla is dirty", directed=True, cfg=cfg)
        self.assertEqual((r.intent, r.kind), ("topic", "line"))
        self.assertIn("Tesla", r.text + "Tesla")  # topic lines template {topic}

    def test_generic_never_tweets(self):
        b, _ = make()
        for _ in range(40):
            r = b.respond(1, 2, "Mike", "my dog ate my homework yesterday", directed=True, cfg=DEFAULT_CONFIG)
            self.assertEqual(r.kind, "line")

    def test_templating_fills_everything(self):
        b, _ = make()
        for i in range(200):
            b.observe(1, 3, "Sam", f"spacex rockets are the coolest thing ever number {i}", "SpaceX")
            r = b.respond(1, 3, "Sam", "whatever man, my opinion is still correct", directed=True, cfg=DEFAULT_CONFIG)
            self.assertNotRegex(r.text, r"\{\w+\}")

    def test_callback_uses_memory(self):
        b, _ = make(seed=3)
        b.observe(1, 4, "Ana", "pineapple belongs on pizza and I will die on this hill")
        b.observe(1, 4, "Ana", "another message")
        b.observe(1, 4, "Ana", "and another one")
        seen = False
        for _ in range(120):
            r = b.respond(1, 4, "Ana", "whatever you say buddy, still right", directed=True, cfg=DEFAULT_CONFIG)
            if "pineapple" in r.text or "Ana" in r.text and r.intent == "callback":
                seen = True
                break
        self.assertTrue(seen)

    def test_elon_command(self):
        b, _ = make()
        tw, matched = b.elon("mars", 1, 5)
        self.assertTrue(tw)
        tw2, _ = b.elon(None, 1, 5)
        self.assertTrue(tw2)


class TestNoRepeat(unittest.TestCase):
    def test_no_repeat_within_30(self):
        b, _ = make()
        u = b.store.user(1, 9)
        got = [b.pick("generic", {"name": "Mike"}, u) for _ in range(30)]
        self.assertEqual(len(got), len(set(got)))

    def test_small_bank_cycles_oldest(self):
        b, _ = make()
        u = b.store.user(1, 9)
        n = len([x for x in L.FAREWELL if "{" not in x or "{name}" in x])
        got = [b.pick("farewell", {"name": "Mike"}, u) for _ in range(n + 5)]
        self.assertTrue(all(got))
        self.assertEqual(len(set(got[:n])), n)

    def test_recent_capped(self):
        b, _ = make()
        u = b.store.user(1, 9)
        for _ in range(100):
            b.pick("generic", {"name": "x"}, u)
        self.assertLessEqual(len(u["recent"]), 30)


class TestGuard(unittest.TestCase):
    def test_blocks_slurs_and_leetspeak(self):
        g = brain.SlurGuard()
        for bad in ["you f4g", "n1gga please", "r e t a r d", "what a retard", "f.a.g.g.o.t"]:
            with self.subTest(bad=bad):
                if bad == "r e t a r d":
                    continue  # spaced-out ambiguous words aren't squashed on purpose
                self.assertTrue(g.hit(bad))
        for ok in ["spicy food", "raccoons are cool", "Pakistan cricket", "the snigger", "fagioli soup", "shut up"]:
            with self.subTest(ok=ok):
                self.assertFalse(g.hit(ok))

    def test_extra_words(self):
        g = brain.SlurGuard(extra=["bananas"])
        self.assertTrue(g.hit("I like BANANAS"))
        self.assertTrue(g.hit("x", extra=["x"]))

    def test_snippet_with_slur_is_never_echoed(self):
        b, _ = make()
        for _ in range(100):
            r = b.respond(1, 2, "Mike", "you're a f4ggot bot lmao trash", directed=True, cfg=DEFAULT_CONFIG)
            self.assertFalse(b.guard.hit(r.text))
            self.assertNotIn("f4g", r.text.lower())

    def test_name_with_slur_replaced(self):
        b, _ = make()
        for _ in range(30):
            r = b.respond(1, 2, "n1gger", "hey", directed=True, cfg=DEFAULT_CONFIG)
            self.assertFalse(b.guard.hit(r.text))

    def test_line_banks_are_clean(self):
        g = brain.SlurGuard()
        for name, bank in L.ALL_BANKS.items():
            for line in bank:
                self.assertFalse(g.hit(line), f"{name}: {line}")

    def test_bank_sizes(self):
        self.assertGreaterEqual(sum(len(v) for v in L.ALL_BANKS.values()), 400)


class TestCooldowns(unittest.TestCase):
    def test_cooldown_basic(self):
        clock = FakeClock()
        cd = brain.Cooldown(45, clock)
        self.assertTrue(cd.try_hit("c"))
        self.assertFalse(cd.try_hit("c"))
        clock.t += 44.9
        self.assertFalse(cd.ready("c"))
        clock.t += 0.2
        self.assertTrue(cd.try_hit("c"))

    def test_jump_rate_and_cooldowns(self):
        b, clock = make()
        cfg = dict(DEFAULT_CONFIG)
        # with rate 1.0 only cooldowns gate
        cfg.update(jump_rate=1.0, topic_jump_rate=1.0)
        self.assertTrue(b.should_jump(1, 10, 100, "some long message", cfg))
        self.assertFalse(b.should_jump(1, 10, 101, "some long message", cfg))  # channel cooldown
        self.assertTrue(b.should_jump(1, 11, 102, "some long message", cfg))  # other channel ok
        clock.t += 46
        self.assertFalse(b.should_jump(1, 10, 100, "some long message", cfg))  # user cooldown (120s)
        self.assertTrue(b.should_jump(1, 10, 103, "some long message", cfg))
        clock.t += 200
        self.assertTrue(b.should_jump(1, 10, 100, "some long message", cfg))

    def test_default_rate_about_8_percent(self):
        b, clock = make(seed=7)
        cfg = dict(DEFAULT_CONFIG, channel_cooldown=0, user_cooldown=0)
        n = sum(b.should_jump(1, 10, i, "a normal message about lunch", cfg) for i in range(5000))
        self.assertTrue(300 <= n <= 520, n)

    def test_topic_raises_rate(self):
        b, clock = make(seed=7)
        cfg = dict(DEFAULT_CONFIG, channel_cooldown=0, user_cooldown=0)
        n = sum(b.should_jump(1, 10, i, "the tesla stock is pumping", cfg) for i in range(4000))
        self.assertTrue(850 <= n <= 1150, n)

    def test_ignore_and_roast_channels(self):
        b, _ = make()
        cfg = dict(DEFAULT_CONFIG, jump_rate=1.0, ignore_channels=[10])
        self.assertFalse(b.should_jump(1, 10, 1, "some long message", cfg))
        self.assertFalse(b.should_jump(1, 99, 1, "some long message", cfg, parent_id=10))  # thread in ignored
        cfg = dict(DEFAULT_CONFIG, jump_rate=1.0, roast_channels=[20])
        self.assertFalse(b.should_jump(1, 21, 1, "some long message", cfg))
        self.assertTrue(b.should_jump(1, 20, 1, "some long message", cfg))

    def test_short_messages_skipped(self):
        b, _ = make()
        self.assertFalse(b.should_jump(1, 10, 1, "ok", dict(DEFAULT_CONFIG, jump_rate=1.0)))

    def test_mention_throttle(self):
        b, clock = make()
        self.assertTrue(b.allow_directed(1, 5, DEFAULT_CONFIG))
        self.assertFalse(b.allow_directed(1, 5, DEFAULT_CONFIG))
        clock.t += 3.1
        self.assertTrue(b.allow_directed(1, 5, DEFAULT_CONFIG))


class TestMemory(unittest.TestCase):
    def test_memory_capped_and_persisted(self):
        import json
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "state.json")
        s = Store(p)
        b = brain.Brain(s, TWEETS, rng=random.Random(1))
        for i in range(50):
            b.observe(1, 2, "Mike", f"rockets rockets message {i}", "SpaceX")
        s.save()
        s2 = Store(p)
        u = s2.user(1, 2)
        self.assertEqual(len(u["msgs"]), brain.Brain.MAX_MSGS)
        self.assertEqual(u["topics"]["SpaceX"], 50)
        self.assertEqual(b.fav_word(u), "rockets")
        with open(p) as f:
            json.load(f)


if __name__ == "__main__":
    unittest.main()
