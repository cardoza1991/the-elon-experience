import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moderation as mod  # noqa: E402
from store import DEFAULT_CONFIG, Store  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class TestDuration(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(mod.parse_duration("10m"), 600)
        self.assertEqual(mod.parse_duration("1h30m"), 5400)
        self.assertEqual(mod.parse_duration("2d"), 172800)
        self.assertEqual(mod.parse_duration("90"), 5400)  # bare number = minutes
        self.assertEqual(mod.parse_duration("45s"), 45)
        self.assertEqual(mod.parse_duration("99d"), mod.MAX_TIMEOUT)
        for bad in ["", "abc", "10x", "0m", None, "-5m"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    mod.parse_duration(bad)

    def test_fmt(self):
        self.assertEqual(mod.fmt_duration(5400), "1h30m")
        self.assertEqual(mod.fmt_duration(0), "0s")


class TestWarns(unittest.TestCase):
    def test_auto_timeout_every_n(self):
        wb = mod.WarnBook(Store(None), clock=lambda: 123)
        triggers = [wb.add(1, 2, "r", "mod", 3) for _ in range(7)]
        self.assertEqual([t[0] for t in triggers], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual([t[1] for t in triggers], [False, False, True, False, False, True, False])
        self.assertEqual(len(wb.list(1, 2)), 7)
        self.assertEqual(wb.remove_last(1, 2), 6)
        self.assertEqual(wb.clear(1, 2), 6)
        self.assertEqual(wb.list(1, 2), [])

    def test_limit_zero_disables(self):
        wb = mod.WarnBook(Store(None))
        self.assertFalse(any(wb.add(1, 2, "r", "m", 0)[1] for _ in range(10)))

    def test_top(self):
        wb = mod.WarnBook(Store(None))
        for _ in range(3):
            wb.add(1, "a", "r", "m", 0)
        wb.add(1, "b", "r", "m", 0)
        self.assertEqual(wb.top(1), [(3, "a"), (1, "b")])

    def test_separate_guilds(self):
        wb = mod.WarnBook(Store(None))
        wb.add(1, 2, "r", "m", 3)
        self.assertEqual(wb.list(9, 2), [])


class TestSpam(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.sd = mod.SpamDetector(self.clock)
        self.cfg = dict(DEFAULT_CONFIG)

    def test_flood(self):
        res = []
        for i in range(6):
            self.clock.t += 1
            res.append(self.sd.check(1, 2, f"msg {i}", 0, self.cfg))
        self.assertEqual(res[:5], [None] * 5)
        self.assertIn("flooding", res[5])

    def test_no_flood_when_slow(self):
        for i in range(30):
            self.clock.t += 2
            self.assertIsNone(self.sd.check(1, 2, f"msg {i}", 0, self.cfg))

    def test_duplicates(self):
        res = []
        for i in range(4):
            self.clock.t += 5
            res.append(self.sd.check(1, 2, "BUY MY MIXTAPE", 0, self.cfg))
        self.assertEqual(res[:3], [None] * 3)
        self.assertIn("duplicate", res[3])

    def test_duplicates_expire(self):
        for i in range(10):
            self.clock.t += 31
            self.assertIsNone(self.sd.check(1, 2, "same", 0, self.cfg))

    def test_mentions(self):
        self.assertIsNone(self.sd.check(1, 2, "hi", 5, self.cfg))
        self.assertIn("mention", self.sd.check(1, 2, "hi", 6, self.cfg))

    def test_disabled(self):
        cfg = dict(self.cfg, spam_enabled=False)
        self.assertIsNone(self.sd.check(1, 2, "x", 50, cfg))

    def test_users_independent(self):
        for i in range(5):
            self.assertIsNone(self.sd.check(1, i, "same", 0, self.cfg))


class TestRaid(unittest.TestCase):
    def test_raid_fires_once_per_window(self):
        clock = Clock()
        rd = mod.RaidDetector(clock)
        cfg = dict(DEFAULT_CONFIG)  # 10 joins / 60s
        fired = []
        for i in range(15):
            clock.t += 1
            fired.append(rd.record_join(1, cfg))
        self.assertEqual(fired.index(True), 9)
        self.assertEqual(sum(fired), 1)
        clock.t += 100
        fired = [rd.record_join(1, cfg) for _ in range(10)]
        self.assertTrue(fired[-1])

    def test_slow_joins_ok(self):
        clock = Clock()
        rd = mod.RaidDetector(clock)
        for i in range(50):
            clock.t += 10
            self.assertFalse(rd.record_join(1, DEFAULT_CONFIG))


class TestFilters(unittest.TestCase):
    def test_links(self):
        self.assertIsNone(mod.link_violation("https://x.com", "off"))
        self.assertEqual(mod.link_violation("join discord.gg/abc", "invites"), "invite")
        self.assertIsNone(mod.link_violation("https://youtube.com/watch", "invites"))
        self.assertEqual(mod.link_violation("https://youtube.com/watch", "all"), "link")
        self.assertEqual(mod.link_violation("go to example.com now", "all"), "link")
        self.assertIsNone(mod.link_violation("no links here.", "all"))

    def test_badwords(self):
        self.assertEqual(mod.badword_hit("You are a Muppet", ["muppet"]), "muppet")
        self.assertIsNone(mod.badword_hit("muppets", ["muppet"]))
        self.assertEqual(mod.badword_hit("big dumb idea", ["dumb idea"]), "dumb idea")
        self.assertIsNone(mod.badword_hit("anything", []))


class TestBotWiring(unittest.TestCase):
    """Import bot.py (no network) and validate the slash command payloads."""

    def test_commands_build(self):
        import bot
        tree = bot.bot.tree
        names = {c.name for c in tree.get_commands()}
        for n in ["help", "elon", "roast", "timeout", "untimeout", "warn", "warnings", "clearwarns", "kick",
                  "ban", "unban", "purge", "slowmode", "lock", "unlock", "lockdown", "config"]:
            self.assertIn(n, names)
        for c in tree.get_commands():
            d = c.to_dict(tree)
            self.assertLessEqual(len(d["description"]), 100)
        prefix = {c.name for c in bot.bot.commands}
        self.assertTrue(names <= prefix)
        self.assertIn("mute", bot.bot.get_command("timeout").aliases)
        self.assertIn("clear", bot.bot.get_command("purge").aliases)


if __name__ == "__main__":
    unittest.main()
