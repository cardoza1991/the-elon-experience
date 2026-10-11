"""Offline harness: drive brain+convo exactly like bot.py does, without Discord.

    .venv/bin/python -m tests.convo_harness      # prints sample transcripts
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import convo  # noqa: E402
from store import DEFAULT_CONFIG, Store  # noqa: E402

BOT_ID = 1558667481500094604
CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "elon_tweets_sample.csv")
_TWEETS = None


def tweets():
    global _TWEETS
    if _TWEETS is None:
        _TWEETS = brain.load_tweets(CSV) or [
            "Been driving latest Cybertruck prototype around Giga Texas. It's awesome!",
            "Starship launch soon", "Mars here we come", "Dogecoin to the moon"]
    return _TWEETS


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


_INDEX = None


def shared_index():
    global _INDEX
    if _INDEX is None:
        _INDEX = brain.TweetIndex(tweets())
    return _INDEX


class Harness:
    def __init__(self, seed=1, **cfg):
        self.clock = Clock()
        self.store = Store(None)
        self.brain = brain.Brain(self.store, tweets(), rng=random.Random(seed), clock=self.clock, index=shared_index())
        self.convo = convo.Conversations(self.brain, self.clock)
        self.cfg = dict(DEFAULT_CONFIG, **cfg)
        self.log = []
        self.chat_on = True

    def send(self, uid, name, text, *, mention=False, reply_to_bot=False, gap=6.0, channel=10, gid=1,
             other_target=False):
        """Mirror of bot.on_message reply logic. Returns the Reply or None."""
        self.clock.t += gap
        explicit = mention or reply_to_bot
        if mention:
            text = f"{text} <@{BOT_ID}>"
        c = self.cfg
        r = None
        mode = self.convo.gate(gid, channel, uid, text, explicit=explicit, cfg=c, chat_on=self.chat_on,
                               ignored=channel in c["ignore_channels"], other_target=other_target and not explicit)
        if mode == "abuse":
            r = self.convo.reply(gid, channel, uid, name, text, cfg=c)
        elif mode == "abuse_oneshot":
            r = self.convo.reply_abuse_oneshot(gid, uid, name, text, cfg=c)
        elif mode == "convo":
            if not explicit or self.brain.allow_directed(gid, uid, c):
                r = self.convo.reply(gid, channel, uid, name, text, cfg=c)
        elif mode == "oneshot":
            if self.brain.allow_directed(gid, uid, c):
                r = self.brain.respond(gid, uid, name, text, directed=True, cfg=c)
        self.brain.observe(gid, uid, name, text, brain.detect_topic(brain.clean_text(text)))
        self.log.append((name, brain.clean_text(text) if not mention else text.replace(f"<@{BOT_ID}>", "@The Elon Experience"), r))
        return r

    def transcript(self):
        out = []
        for name, text, r in self.log:
            out.append(f"{name}: {text}")
            out.append(f"  Elon Experience [{r.intent}]: {r.text}" if r else "  Elon Experience: (stays quiet)")
        return "\n".join(out)


def batdad(seed=1):
    h = Harness(seed)
    n = "King Batdad [DΞΛD]"
    h.send(7, n, "testing", mention=True)
    h.send(7, n, "no way")
    h.send(7, n, "not much of a conversationalists lol")
    return h


def mechanic(seed=2):
    h = Harness(seed)
    n = "Rico"
    for text, kw in [("yo elon", {}), ("i'm a mechanic", {}), ("what do you think about the cybertruck?", {}),
                     ("yeah", {}), ("i think it looks like a fridge", {}), ("do you even like cars", {}),
                     ("what about you", {}), ("tell me a joke", {}), ("lmao ok gtg", {})]:
        h.send(5, n, text, **kw)
    return h


def group(seed=3):
    h = Harness(seed)
    h.send(1, "Ana", "hey bot how are you", mention=True)
    h.send(1, "Ana", "i'm tired of work honestly")
    h.send(2, "Ben", "elon are you real or what")
    h.send(1, "Ana", "anyway my boss is a clown")
    h.send(2, "Ben", "lol")
    h.send(1, "Ana", "why are you like this")
    h.send(2, "Ben", "is bitcoin dead?")
    h.send(1, "Ana", "stfu")
    return h


def live_bug(seed=5):
    """The 03:3x UTC session on Michael's server that came out doubled (now single replies)."""
    h = Harness(seed)
    n = "[DEAD] Batdaddy [DΞΛD]"
    h.send(8, n, "Elon")
    h.send(8, n, "bruh we are testing you")
    h.send(8, n, "like can you be less offensive")
    h.send(8, n, "your mom did")
    h.send(8, n, "Yes I am and you are dumb")
    h.send(8, n, "<@4242> Edwin your best friend is back", other_target=True)
    h.send(8, n, "like just let this bot handle the rabble")
    h.send(8, n, "stfu bot")
    return h


def abuser(seed=4):
    h = Harness(seed)
    n = "Dex"
    h.send(9, n, "yo", mention=True)
    for text in ["you're trash", "stfu bot", "fuck you, you useless clanker", "you're so dumb lmao",
                 "shut the fuck up"]:
        h.send(9, n, text, gap=8)
    h.send(9, n, "ok fine you win, what's up", mention=True, gap=900)  # 15 min later: cooled off
    h.send(9, n, "you still suck", gap=8)  # decayed back to tier 1
    return h


if __name__ == "__main__":
    for title, fn in (("1. Michael's server regression", batdad), ("2. Facts, opinions, pronouns", mechanic),
                      ("3. Group conversation", group), ("4. Abuse escalation", abuser),
                      ("5. Live bug replay (single replies)", live_bug)):
        print(f"=== {title} ===")
        print(fn().transcript())
        print()
