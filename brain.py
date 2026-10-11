"""brain.py - 'AI without AI'.

Pure rules + data: an intent router, a tiny pure-python TF-IDF index over
Elon's tweets, per-user memory for callbacks, no-repeat line picking, a
slur guard, and the jump-in policy with cooldowns. No models, no network.
Everything here is testable without Discord.
"""
import csv
import html
import math
import random
import re
import time
from collections import Counter, defaultdict, deque

import lines as L

WORD_RE = re.compile(r"[a-z0-9']+")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
MENTION_RE = re.compile(r"<[@#][!&]?\d+>|@everyone|@here")
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

STOP = set("""
a an the and or but if then so to of in on at for with from by as is are was were be been being am
i you he she it we they me my your yours his her its our their them this that these those there here
do does did doing done have has had having not no yes yeah yea just like really very can could would
should will wont won't cant can't im i'm it's its dont don't didnt didn't u ur r ya ok okay oh ah
what who when where why how which all any some more most much many also too only even still get got
go going gonna wanna about up out over into than then now one two lol lmao bro bruh dude man fr ngl
tbh idk tho though thats that's whats what's youre you're ive i've id i'd ill i'll lets let's
think know say said says make making made want need see look looking good bad new time people thing things
way day days now today right well back gotta soon yet ever never always every something anything really
guy guys thank thanks great lot little big next last first
""".split())

# ---------------------------------------------------------------- text utils

def tokens(text):
    return [t.strip("'") for t in WORD_RE.findall(text.lower()) if t.strip("'")]


def stem(w):
    for suf in ("ing", "ed", "es"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def content_terms(text):
    return [stem(t) for t in tokens(text) if t not in STOP and len(t) > 1]


def clean_text(text):
    text = MENTION_RE.sub("", text or "")
    text = CUSTOM_EMOJI_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def snippet_of(text, limit=60, trim=False):
    """Short quote of a message. Too long -> '' (no '...' echoes) unless trim=True (UI labels only)."""
    t = URL_RE.sub("", clean_text(text)).replace('"', "'").replace("`", "'").strip()
    if not t:
        return ""
    if len(t) > limit:
        if not trim:
            return ""
        t = t[: limit].rsplit(" ", 1)[0] + "..."
    return t


# protected traits: never echoed, never riffed on, never used as a counter-insult
PROTECTED_RE = re.compile(
    r"\b(gay|gays|lesbian|bi|bisexual|trans|transgender|queer|homo|homosexual|nonbinary|non-binary|asexual|black|white|"
    r"asian|latino|latina|hispanic|mexican|jewish|jew|muslim|christian|catholic|hindu|atheist|buddhist|sikh|"
    r"disabled|autistic|autism|adhd|deaf|blind|immigrant|arab|indian|african|native american|pregnant|religious)\b", re.I)
# self-harm 'insults': never echoed or joked about
SELF_HARM_RE = re.compile(r"\b(kys|kill (your ?self|urself|yaself|yoself)|go die|neck (your ?self|urself)|"
                          r"unalive (your ?self|urself)|end (your ?self|urself))\b", re.I)

VULGAR_RE = re.compile(r"\b(fuck\w*|shit\w*|bitch\w*|ass\b|asshole|dick\w*|piss\w*|cunt|bullshit|damn|hell)\b", re.I)


def fix_caps(text):
    """Capitalise sentence starts (outside quotes) so '{x}' at a sentence start reads right."""
    parts = text.split('"')
    for i in range(0, len(parts), 2):  # even parts are outside quotes
        p = re.sub(r"((?<!\.\.)[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), parts[i])
        if i == 0:
            p = re.sub(r"^(\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), p)
        parts[i] = p
    return '"'.join(parts)


# ---------------------------------------------------------------- slur guard

_LEET = str.maketrans({"1": "i", "!": "i", "|": "i", "3": "e", "0": "o", "4": "a",
                       "@": "a", "$": "s", "5": "s", "7": "t", "+": "t"})


_SPACED_RE = re.compile(r"(?:\b[a-z][^a-z0-9\n]{1,3}){3,}[a-z]\b")


class SlurGuard:
    """Never echo slurs. Configurable: base list in lines.py + extra words."""

    def __init__(self, patterns=None, squashed=None, extra=()):
        self.base = [re.compile(p, re.I) for p in (patterns if patterns is not None else L.BLOCKLIST_PATTERNS)]
        self.squashed = [s.lower() for s in (squashed if squashed is not None else L.BLOCKLIST_SQUASHED)]
        self.extra = set()
        for w in extra:
            self.add(w)

    def add(self, word):
        w = word.strip().lower()
        if w:
            self.extra.add(w)

    def remove(self, word):
        self.extra.discard(word.strip().lower())

    def hit(self, text, extra=()):
        if not text:
            return False
        low = text.lower()
        norm = low.translate(_LEET)
        for rx in self.base:
            if rx.search(low) or rx.search(norm):
                return True
        # spaced/dotted-out spellings ("n.i.g.g.e.r", "f a g g o t"); whole runs only,
        # so innocent words that merely contain a stem ("snigger") don't trip it
        for run in _SPACED_RE.findall(norm):
            squashed = re.sub(r"[^a-z]", "", run)
            if any(squashed.startswith(s) for s in self.squashed):
                return True
        words = set(re.findall(r"[a-z0-9]+", norm)) | set(re.findall(r"[a-z0-9]+", low))
        for w in set(self.extra) | {e.lower() for e in extra}:
            if (" " in w and w in norm) or w in words:
                return True
        return False


# ---------------------------------------------------------------- tweets

def load_tweets(path):
    """Same filter as the original bot: English, 25-250 chars, no leading @/link."""
    out = []
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                text = html.unescape((row.get("Tweet.content") or "").strip())
                if (row.get("Language") == "en" and 25 < len(text) < 250
                        and not text.startswith("http") and not text.startswith("@")
                        and "http" not in text.lower()[:50]):
                    text = URL_RE.sub("", text).strip()
                    if len(text) > 20 and text not in out:
                        out.append(text)
    except FileNotFoundError:
        pass
    return out


FALLBACK_TWEETS = [
    "The future is going to be wild.",
    "Let's make life multiplanetary.",
    "Occupy Mars.",
    "The most entertaining outcome is the most likely.",
]

# topic name -> trigger keywords (raw tokens, stemmed at match time)
TOPICS = {
    "Tesla": "tesla teslas car cars ev evs cybertruck autopilot fsd selfdriving driving battery batteries gigafactory roadster electric vehicle vehicles supercharger plaid",
    "SpaceX": "spacex rocket rockets starship falcon launch launches launched orbit orbital booster raptor space nasa starlink satellite satellites astronaut",
    "Mars": "mars moon multiplanetary planet planets martian colony",
    "X/Twitter": "twitter tweet tweets tweeting retweet ratio timeline algorithm bluesky threads bluecheck verified",
    "AI": "ai agi gpt chatgpt openai grok robot robots optimus neural llm artificial intelligence",
    "money": "money crypto bitcoin btc doge dogecoin stock stocks tsla invest investing investor rich billionaire broke rent cash market eth ethereum dollar dollars economy inflation",
    "Neuralink": "neuralink implant brainchip",
    "tunnels": "tunnel tunnels boring hyperloop traffic",
    "free speech": "censorship censor speech woke media journalist journalists",
    "energy": "solar energy oil nuclear powerwall gas",
    "memes": "meme memes",
}
TOPIC_TERMS = {t: {stem(w) for w in ws.split()} for t, ws in TOPICS.items()}
X_RE = re.compile(r"\b(on|over on|post(?:ed)? on|use|using|quit|left|app called)\s+x\b|\bx\.com\b|\bx app\b", re.I)


def detect_topic(text):
    terms = set(content_terms(text)) | {stem(t) for t in tokens(text)}
    best, hits = None, 0
    for topic, kws in TOPIC_TERMS.items():
        n = len(terms & kws)
        if n > hits:
            best, hits = topic, n
    if best is None and X_RE.search(text):
        best = "X/Twitter"
    return best


class TweetIndex:
    """Tiny TF-IDF + cosine index. Pure python, ~1ms per query for ~700 docs."""

    def __init__(self, docs):
        self.docs = list(docs)
        tfs = [Counter(content_terms(d)) for d in self.docs]
        df = Counter()
        for tf in tfs:
            df.update(tf.keys())
        n = len(self.docs) or 1
        self.idf = {w: math.log((n + 1) / (c + 1)) + 1.0 for w, c in df.items()}
        self.vecs, self.inv = [], defaultdict(list)
        for i, tf in enumerate(tfs):
            v = {w: (1 + math.log(c)) * self.idf[w] for w, c in tf.items()}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            v = {w: x / norm for w, x in v.items()}
            self.vecs.append(v)
            for w in v:
                self.inv[w].append(i)

    def _qvec(self, text, extra_terms=()):
        tf = Counter(content_terms(text))
        for t in extra_terms:
            tf[t] += 0.5
        v = {w: (1 + math.log(c)) * self.idf[w] for w, c in tf.items() if w in self.idf and c >= 1}
        v.update({w: 0.5 * self.idf[w] for w, c in tf.items() if w in self.idf and c < 1})
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    def search(self, text, k=5, extra_terms=(), require=None):
        q = self._qvec(text, extra_terms)
        scores = defaultdict(float)
        for w, qx in q.items():
            for i in self.inv.get(w, ()):
                scores[i] += qx * self.vecs[i][w]
        res = sorted(((s, i) for i, s in scores.items()), reverse=True)
        if require:  # the shared words must include a topic keyword
            res = [(s, i) for s, i in res if require & set(q) & set(self.vecs[i])]
        return [(round(s, 4), self.docs[i]) for s, i in res[:k]]


# ---------------------------------------------------------------- intents

INSULT_RE = re.compile(
    r"\b(fuck (you|u|off|this|that)|f u|fu|stfu|shut (the fuck )?up|you suck|u suck|sucks|trash|garbage|"
    r"dumb(ass)?|stupid|idiot|moron|bitch|loser|clown|cringe|(you'?re|youre|ur|u r|you are|bot is|this bot is|elon is) (so |kinda |lowkey |)mid|^\W*mid\W*$|worst|hate (you|u|this)|ugly|bozo|"
    r"useless|annoying|dogshit|shit bot|bad bot|ass|asshole|dipshit|dickhead|jackass|pathetic|lame|"
    r"nobody asked|who asked|l bot|ratio|^\W*cope|cope( harder)?\W*$|seethe|kys|(go|just|pls|please) die|clanker|go away|piss off)\b", re.I)
PRAISE_RE = re.compile(
    r"\b(good bot|great bot|best bot|love (you|u|this bot)|goat(ed)?|based|w bot|funny|hilarious|"
    r"you'?re? (the best|funny|great|cool)|nice one|well said|thank(s| you)|ty)\b", re.I)
GREETING_RE = re.compile(
    r"^\s*(yo+|hi+|hey+|hello+|sup|wass?up|what'?s up|howdy|gm|good morning|good evening|hola|ayo+|heyo|wsg|morning)\b", re.I)
FAREWELL_RE = re.compile(
    r"\b(gn|good ?night|bye+|cya|see ya|see you|later|peace( out)?|im out|i'?m out|logging off|night)\b", re.I)
LAUGH_RE = re.compile(r"\b(lo+l+|lmf?a+o+|rofl|ha(ha)+h?|hehe+|kek|xd|im dead|i'?m dead|i'?m crying|im crying)\b|💀|😂|🤣|😭", re.I)
TEST_RE = re.compile(r"^\s*((test(ing)?|tst|ping|check|mic|one two|1\s*2\s*3|\d+|asdf\w*)[\s!?.,]*){1,4}$"
                     r"|^\s*is this (thing )?on\W*$", re.I)
QUESTION_START_RE = re.compile(
    r"^\s*(who|what|when|where|why|how|is|are|can|could|do|does|did|should|would|will|which|wanna|am i)\b", re.I)
LOW_EFFORT = {"ok", "k", "kk", "okay", "bruh", "bro", "fr", "ong", "true", "same", "mood", "yup", "yep", "nah",
              "nope", "idk", "ight", "aight", "word", "bet", "cool", "nice", "wow", "huh", "what", "?", "hm", "hmm",
              "sure", "ya", "yes", "no", "lmk", "w", "l", "real", "facts"}
BOT_WORDS_RE = re.compile(r"\b(bot|elon|clanker|robot)\b", re.I)


def is_spammy(text):
    t = text.strip()
    if not t:
        return False
    if re.search(r"(.)\1{7,}", t):  # aaaaaaaa
        return True
    words = tokens(t)
    if len(words) >= 4 and len(set(words)) == 1:  # same word repeated
        return True
    letters = re.sub(r"[^a-z]", "", t.lower())
    if len(letters) >= 8 and not re.search(r"[aeiouy]", letters):  # keyboard smash
        return True
    if len(letters) >= 10 and len(set(letters)) <= 3:
        return True
    return False


def classify(text, directed=False):
    """Return (intent, topic). Pure function, priority ordered."""
    t = clean_text(text)
    low = t.lower()
    topic = detect_topic(t)
    words = tokens(t)
    if not t:
        return "low_effort", None
    if TEST_RE.match(t) or is_spammy(t):
        return "spam_test", topic
    aimed = directed or bool(BOT_WORDS_RE.search(t))
    if aimed and INSULT_RE.search(t):
        return "insult_bot", topic
    if aimed and PRAISE_RE.search(t) and not INSULT_RE.search(t):
        return "praise_bot", topic
    if GREETING_RE.match(t) and len(words) <= 6:
        return "greeting", topic
    if FAREWELL_RE.search(t) and len(words) <= 6:
        return "farewell", topic
    if LAUGH_RE.search(t) and (len(words) <= 2 or (len(words) <= 6 and not topic)):
        return "laugh", topic
    if topic:
        return "topic", topic
    if low.endswith("?") or (QUESTION_START_RE.match(t) and len(words) >= 3):
        return "question", topic
    if len(words) <= 2 and (not words or all(w in LOW_EFFORT for w in words) or len(low) <= 3):
        return "low_effort", topic
    return "generic", topic


# ---------------------------------------------------------------- cooldowns

class Cooldown:
    def __init__(self, seconds, clock=time.monotonic):
        self.seconds = seconds
        self.clock = clock
        self.last = {}

    def ready(self, key, seconds=None):
        s = self.seconds if seconds is None else seconds
        t = self.last.get(key)
        return t is None or self.clock() - t >= s

    def hit(self, key):
        self.last[key] = self.clock()

    def try_hit(self, key, seconds=None):
        if self.ready(key, seconds):
            self.hit(key)
            return True
        return False


# ---------------------------------------------------------------- the brain

class Reply:
    __slots__ = ("intent", "text", "kind", "topic", "score", "explicit")

    def __init__(self, intent, text, kind="line", topic=None, score=None):
        self.intent, self.text, self.kind, self.topic, self.score = intent, text, kind, topic, score
        self.explicit = False

    def __repr__(self):
        return f"Reply({self.intent!r}, {self.kind!r}, {self.text!r})"


class Brain:
    MAX_MSGS = 20
    NO_REPEAT = 30

    def __init__(self, store, tweets=None, rng=None, clock=time.monotonic, guard=None, index=None):
        self.store = store
        self.rng = rng or random.Random()
        self.clock = clock
        self.tweets = list(tweets) if tweets else list(FALLBACK_TWEETS)
        self.index = index or TweetIndex(self.tweets)
        self.guard = guard or SlurGuard()
        self.banks = dict(L.ALL_BANKS)
        self.chan_cd = Cooldown(45, clock)
        self.user_cd = Cooldown(120, clock)
        self.mention_cd = Cooldown(3, clock)

    # ---- memory ----
    def observe(self, gid, uid, name, text, topic=None):
        u = self.store.user(gid, uid)
        u["name"] = name
        t = clean_text(text)
        if not t:
            return
        u["msgs"].append([int(time.time()), t[:300]])
        del u["msgs"][: -self.MAX_MSGS]
        w = u["words"]
        for term in tokens(t):
            if term not in STOP and len(term) > 2 and not self.guard.hit(term) and not term.isdigit():
                w[term] = w.get(term, 0) + 1
        if len(w) > 80:  # keep the top 60
            for k, _ in sorted(w.items(), key=lambda kv: kv[1])[: len(w) - 60]:
                del w[k]
        if topic:
            u["topics"][topic] = u["topics"].get(topic, 0) + 1
        self.store.mark_dirty()

    def fav_word(self, u):
        w = [(c, k) for k, c in u.get("words", {}).items() if c >= 3]
        return max(w)[1] if w else None

    def callback_quote(self, u, extra=()):
        old = [m[1] for m in u.get("msgs", [])[:-2] if 12 <= len(m[1]) <= 140]
        old = [m for m in old if not self.guard.hit(m, extra) and not URL_RE.search(m)]
        if not old:
            return None
        return snippet_of(self.rng.choice(old[-10:]), 70)

    # ---- line picking ----
    def _fill(self, line, ctx):
        keys = set(PLACEHOLDER_RE.findall(line))
        if any(not ctx.get(k) for k in keys):
            return None
        try:
            return line.format(**ctx)
        except (KeyError, IndexError, ValueError):
            return None

    NO_ECHO_BANKS = {"followup", "followup_q", "abuse_tag"}

    def echo_allowed(self, u):
        """Lines that quote their whole message back: max ~1 in 4 replies, never two in a row."""
        return not any(u.get("echo", [])[-3:])

    def pick(self, bank_name, ctx, u, extra=()):
        bank = self.banks[bank_name]
        recent = u.setdefault("recent", [])
        record = bank_name not in self.NO_ECHO_BANKS
        mild = ctx.get("_tone") == "mild"
        no_echo = record and not self.echo_allowed(u)
        if ctx.get("snippet") and (no_echo or (mild and VULGAR_RE.search(ctx["snippet"]))):
            ctx = dict(ctx, snippet="")
        said = ctx.get("_said") or ""
        options = []
        for i, line in enumerate(bank):
            if mild and VULGAR_RE.search(line):
                continue
            lid = f"{bank_name}:{i}"
            text = self._fill(line, ctx)
            if text:
                # an "echo" is any line that parrots their whole message back (snippet or a slot that grabbed it all)
                echo = "{snippet}" in line or (len(said) > 6 and said in text.lower())
                if mild and VULGAR_RE.search(text):
                    continue
                options.append((lid, fix_caps(text), echo, "{x}" in line and bool(ctx.get("x"))))
        if no_echo:
            options = [o for o in options if not o[2]] or options
        if not options:
            return None
        fresh = [o for o in options if o[0] not in recent and o[1] not in recent]
        if fresh:
            # prefer answers that respond to meaning over ones that just quote them back
            plain = [o for o in fresh if not o[2]]
            pool = plain if plain and self.rng.random() < 0.7 else fresh
            # lines that reflect the captured part of what they said ({x}) are the most on-point
            slot = [o for o in pool if o[3]]
            if slot and self.rng.random() < 0.65:
                pool = slot
            lid, text, echo, _ = self.rng.choice(pool)
        else:  # everything was used recently: take the oldest one
            order = {k: n for n, k in enumerate(recent)}
            lid, text, echo, _ = min(options, key=lambda o: order.get(o[0], -1))
        if self.guard.hit(text, extra):
            return None
        recent.append(lid)
        del recent[: -self.NO_REPEAT]
        if record:
            h = u.setdefault("echo", [])
            h.append(bool(echo))
            del h[:-8]
        return text

    def remember_tweet(self, u, tweet):
        recent = u.setdefault("recent", [])
        recent.append("tw:" + str(hash(tweet) & 0xffffffff))
        del recent[: -self.NO_REPEAT]

    def tweet_used(self, u, tweet):
        return ("tw:" + str(hash(tweet) & 0xffffffff)) in u.get("recent", [])

    def match_tweet(self, text, topic=None, threshold=0.15, u=None):
        # only the user's own words drive the score; the topic just filters
        require = TOPIC_TERMS[topic] if topic else None
        res = self.index.search(text, k=8, require=require)
        for score, tweet in res:
            if score < threshold:
                break
            if u is not None and self.tweet_used(u, tweet):
                continue
            return score, tweet
        return None

    def elon(self, query=None, gid=0, uid=0):
        """!elon [topic]: best match for the query, else a random tweet."""
        u = self.store.user(gid, uid)
        if query:
            topic = detect_topic(query)
            terms = set(content_terms(query))
            hit = self.match_tweet(query, None, 0.05, u)
            if not hit and topic:
                hit = self.match_tweet(" ".join(TOPICS[topic].split()[:6]), topic, 0.05, u)
            if hit and (terms & set(self.index._qvec(query)) or topic):
                self.remember_tweet(u, hit[1])
                return hit[1], True
        pool = [t for t in self.tweets if not self.tweet_used(u, t)] or self.tweets
        tw = self.rng.choice(pool)
        self.remember_tweet(u, tw)
        return tw, False

    def context(self, u, name, text, topic=None, extra=()):
        if name:  # 'King Batdad [DEAD]' -> 'King Batdad'
            short = re.sub(r"\s*[\[\(\{<|].*?([\]\)\}>|]|$)\s*", " ", name).strip()
            name = short if len(short) >= 2 else name
        name = name if name and not self.guard.hit(name, extra) else "bro"
        snip = snippet_of(text)
        if len(re.findall(r"[.!?]\s+\S", snip)) >= 1:
            snip = ""  # multi-sentence messages never get parroted back whole
        if snip and (PROTECTED_RE.search(snip) or SELF_HARM_RE.search(snip)):
            snip = ""  # never quote protected-trait words or self-harm talk back
        if snip and (self.guard.hit(snip, extra) or len(snip) < 2):
            snip = ""
        last_topic = topic or (max(u["topics"], key=u["topics"].get) if u.get("topics") else None)
        return {
            "name": name,
            "topic": topic or "",
            "snippet": snip,
            "callback": self.callback_quote(u, extra) or "",
            "fav": self.fav_word(u) or "",
            "count": str(u.get("roasts", 0)) if u.get("roasts", 0) >= 2 else "",
            "_last_topic": last_topic,
            "_said": clean_text(text).lower().strip(" .!?,"),
        }

    def respond(self, gid, uid, name, text, *, directed=False, cfg=None, extra_block=()):
        """Decide what to say. Does NOT apply cooldowns (see should_jump)."""
        cfg = cfg or {}
        u = self.store.user(gid, uid)
        intent, topic = classify(text, directed)
        ctx = self.context(u, name, text, topic, extra_block)
        ctx["_tone"] = cfg.get("tone", "normal")
        reply = None

        if intent == "topic":
            hit = self.match_tweet(text, topic, cfg.get("tweet_threshold", 0.15), u)
            if hit and not self.guard.hit(hit[1], extra_block):
                self.remember_tweet(u, hit[1])
                reply = Reply("topic", hit[1], "tweet", topic, hit[0])
            else:
                text_line = self.pick("topic", ctx, u, extra_block)
                reply = Reply("topic", text_line, "line", topic) if text_line else None
        elif intent in ("generic", "question", "low_effort") and self.rng.random() < 0.22:
            # the 'AI' moment: remember something they said before
            cb_ctx = dict(ctx, topic=ctx["topic"] or (ctx["_last_topic"] or ""))
            line = self.pick("callback", cb_ctx, u, extra_block)
            if line:
                reply = Reply("callback", line, "line", topic)

        if reply is None and intent != "topic":
            line = self.pick(intent, ctx, u, extra_block)
            if line:
                reply = Reply(intent, line, "line", topic)
        if reply is None:
            line = self.pick("generic", dict(ctx, snippet=""), u, extra_block) or "Okay but counterpoint: no."
            reply = Reply("generic", line, "line", topic)

        if self.guard.hit(reply.text, extra_block):  # final safety net
            reply = Reply("generic", "Okay but counterpoint: no.", "line", topic)
        u["roasts"] = u.get("roasts", 0) + 1
        self.store.mark_dirty()
        return reply

    def roast(self, gid, uid, name, extra_block=()):
        """!roast @user - targeted roast using what we remember about them."""
        u = self.store.user(gid, uid)
        last = u["msgs"][-1][1] if u.get("msgs") else ""
        ctx = self.context(u, name, last, None, extra_block)
        bank = "targeted" if self.rng.random() < 0.6 else "generic"
        line = self.pick(bank, dict(ctx, snippet=""), u, extra_block) or self.pick("generic", dict(ctx, snippet=""), u, extra_block)
        if not line or self.guard.hit(line, extra_block):
            line = f"{ctx['name']}, you type like your brain is buffering."
        u["roasts"] = u.get("roasts", 0) + 1
        self.store.mark_dirty()
        return line

    # ---- reply policy ----
    def should_jump(self, gid, channel_id, uid, text, cfg, parent_id=None):
        """Unsolicited jump-in? Applies ignore list, roast channels, rate and cooldowns."""
        chans = {channel_id, parent_id} - {None}
        if chans & set(cfg.get("ignore_channels", [])):
            return False
        rc = cfg.get("roast_channels") or []
        if rc and not (chans & set(rc)):
            return False
        if len(clean_text(text)) < 6:
            return False
        if not self.chan_cd.ready(("c", gid, channel_id), cfg.get("channel_cooldown", 45)):
            return False
        if not self.user_cd.ready(("u", gid, uid), cfg.get("user_cooldown", 120)):
            return False
        rate = cfg.get("jump_rate", 0.08)
        if detect_topic(clean_text(text)):
            rate = max(rate, cfg.get("topic_jump_rate", 0.25))
        if self.rng.random() >= rate:
            return False
        self.chan_cd.hit(("c", gid, channel_id))
        self.user_cd.hit(("u", gid, uid))
        return True

    def allow_directed(self, gid, uid, cfg):
        """Mentions/replies always answered, but at most once per mention_cooldown seconds per user."""
        return self.mention_cd.try_hit(("m", gid, uid), cfg.get("mention_cooldown", 3))
