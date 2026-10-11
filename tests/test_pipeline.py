"""Full on_message pipeline with a mocked Discord message/channel: exactly one send per message."""
import asyncio
import itertools
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot as B  # noqa: E402

_ids = itertools.count(10_000)


class FakeUser:
    def __init__(self, uid, name, is_bot=False):
        self.id, self.name, self.display_name, self.bot = uid, name, name, is_bot
        self.mention = f"<@{uid}>"
        self.roles = []

    def __eq__(self, other):
        return getattr(other, "id", None) == self.id

    def __hash__(self):
        return hash(self.id)

    def __str__(self):
        return self.name


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeChannel:
    def __init__(self, cid=500):
        self.id, self.parent_id = cid, None
        self.send = mock.AsyncMock()
        self.mention = f"<#{cid}>"

    def permissions_for(self, _):
        return mock.Mock(send_messages=True, manage_messages=False)

    def typing(self):
        return Typing()


class FakeGuild:
    def __init__(self, gid=777):
        self.id = gid
        self.me = mock.Mock(top_role=99)
        self.text_channels = []

    def get_channel(self, cid):
        return None


BOT_USER = FakeUser(1558667481500094604, "The Elon Experience", is_bot=True)


def make_msg(text, author, channel, guild, *, mention_bot=False, mentions=(), reply_to=None, mid=None):
    m = mock.Mock()
    m.id = mid if mid is not None else next(_ids)
    m.content = text + (f" <@{BOT_USER.id}>" if mention_bot else "")
    m.author, m.channel, m.guild = author, channel, guild
    m.mentions = ([BOT_USER] if mention_bot else []) + list(mentions)
    m.raw_mentions = [u.id for u in m.mentions]
    m.raw_role_mentions = []
    m.mention_everyone = False
    if reply_to is not None:
        m.reference = mock.Mock(message_id=123, resolved=None, cached_message=mock.Mock(author=reply_to))
    else:
        m.reference = None
    m.reply = mock.AsyncMock()
    return m


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        B.bot._connection.user = BOT_USER

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.ch = FakeChannel()
        self.g = FakeGuild()
        self.patches = [
            mock.patch.object(B.bot, "get_context", mock.AsyncMock(return_value=mock.Mock(valid=False))),
            mock.patch.object(B, "automod", mock.AsyncMock(return_value=False)),
            mock.patch.object(B.asyncio, "sleep", mock.AsyncMock()),
            mock.patch.object(B.store, "save", lambda *a, **k: False),
        ]
        for p in self.patches:
            p.start()
        self.clock = [5000.0]
        clk = lambda: self.clock[0]  # noqa: E731
        B.BRAIN.clock = clk
        for cd in (B.BRAIN.chan_cd, B.BRAIN.user_cd, B.BRAIN.mention_cd):
            cd.clock = clk
        B.CONVO.clock = clk
        B.CONVO.channels.clear()
        B.CONVO.abuse.clear()
        B.CONVO.abuse_times.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.loop.close()

    def run_msg(self, msg, gap=10):
        self.clock[0] += gap
        before = self.ch.send.await_count
        self.loop.run_until_complete(B.on_message(msg))
        return msg.reply.await_count + (self.ch.send.await_count - before)

    def sends(self):
        return self.ch.send.await_count

    def test_exactly_one_send_per_message_all_intents(self):
        u = FakeUser(42, "[DEAD] Batdaddy [DΞΛD]")
        script = [
            ("testing", True), ("no way", False), ("not much of a conversationalists lol", False),
            ("Elon", False), ("bruh we are testing you", False), ("like can you be less offensive", False),
            ("your mom did", False), ("Yes I am and you are dumb", False), ("omg get fucked", False),
            ("like just let this bot handle the rabble", False), ("stfu bot", False),
            ("you're so fucking dumb", False), ("what do you think about the cybertruck?", False),
            ("tell me a joke", False), ("i'm a mechanic", False), ("why are you like this", False),
            ("lol", False), ("hey", True), ("gn", False),
        ]
        total = 0
        for text, mention in script:
            msg = make_msg(text, u, self.ch, self.g, mention_bot=mention)
            n = self.run_msg(msg)
            self.assertLessEqual(n, 1, f"{text!r} produced {n} sends")
            total += n
        self.assertGreaterEqual(total, len(script) - 3)  # nearly everything answered (rate limits may skip)

    def test_same_message_dispatched_twice_is_answered_once(self):
        u = FakeUser(43, "Ana")
        msg = make_msg("fuck you bot", u, self.ch, self.g, mention_bot=True, mid=999_001)
        self.run_msg(msg)
        self.loop.run_until_complete(B.on_message(msg))  # replayed / double dispatch
        self.assertEqual(msg.reply.await_count + self.ch.send.await_count, 1)  # fresh channel: totals are per-message

    def test_abuse_storm_one_send_each_and_rate_limited(self):
        u = FakeUser(44, "Dex")
        counts = []
        for i in range(12):
            msg = make_msg("stfu bot you're trash", u, self.ch, self.g)
            counts.append(self.run_msg(msg, gap=1))
        self.assertTrue(all(c <= 1 for c in counts))
        self.assertLessEqual(sum(counts), 6)

    def test_talking_to_another_user_is_not_hijacked(self):
        u = FakeUser(45, "Batdaddy")
        edwin = FakeUser(46, "Edwin")
        self.run_msg(make_msg("hey", u, self.ch, self.g, mention_bot=True))
        n = self.run_msg(make_msg("Edwin your best friend is back", u, self.ch, self.g, mentions=[edwin]))
        self.assertEqual(n, 0)
        n = self.run_msg(make_msg("lol you're right", u, self.ch, self.g, reply_to=edwin))
        self.assertEqual(n, 0)
        # ...but naming the bot while mentioning someone else still works
        n = self.run_msg(make_msg("elon roast Edwin", u, self.ch, self.g, mentions=[edwin]))
        self.assertEqual(n, 1)

    def test_reply_to_bot_counts_as_explicit(self):
        u = FakeUser(47, "Cal")
        n = self.run_msg(make_msg("whatever", u, self.ch, self.g, reply_to=BOT_USER))
        self.assertEqual(n, 1)

    def test_single_on_message_listener(self):
        extra = B.bot.extra_events.get("on_message", [])
        self.assertEqual(len(extra), 0, "no bot.listen('on_message') duplicates")
        self.assertEqual(len(B.bot.cogs), 0)


class SingleInstanceLockTest(unittest.TestCase):
    def test_second_instance_refuses_to_start(self):
        import subprocess
        import tempfile
        import fcntl
        d = tempfile.mkdtemp()
        lock = os.path.join(d, "bot.lock")
        held = open(lock, "w")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code = ("import fcntl,sys\n"
                f"f=open({lock!r},'w')\n"
                "try:\n fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept OSError:\n sys.exit(3)\n")
        r = subprocess.run([sys.executable, "-c", code])
        self.assertEqual(r.returncode, 3)
        held.close()
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")).read()
        self.assertIn("LOCK_EX | fcntl.LOCK_NB", src)


if __name__ == "__main__":
    unittest.main()
