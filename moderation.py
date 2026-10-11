"""moderation.py - Discord-free moderation logic (durations, warns, spam, raids, filters)."""
import re
import time
from collections import defaultdict, deque

MAX_TIMEOUT = 28 * 24 * 3600  # Discord limit
_DUR_RE = re.compile(r"(\d+)\s*(w|d|h|m|s)?", re.I)
_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1, None: 60}


def parse_duration(text):
    """'10m', '1h30m', '2d', '90' (minutes) -> seconds. Raises ValueError."""
    if text is None:
        raise ValueError("no duration")
    t = str(text).strip().lower().replace(" ", "")
    if not t or not re.fullmatch(r"(\d+(w|d|h|m|s)?)+", t):
        raise ValueError(f"bad duration: {text!r} (try 10m, 1h, 1h30m, 2d)")
    total = 0
    for n, unit in _DUR_RE.findall(t):
        total += int(n) * _UNITS[(unit or None) and unit.lower()]
    if total <= 0:
        raise ValueError("duration must be > 0")
    return min(total, MAX_TIMEOUT)


def fmt_duration(seconds):
    seconds = int(seconds)
    parts = []
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= n:
            parts.append(f"{seconds // n}{unit}")
            seconds %= n
    return "".join(parts) or "0s"


def audit_reason(actor, reason):
    r = f"{actor}: {reason or 'no reason given'}"
    return r[:500]


# ---------------------------------------------------------------- warns

class WarnBook:
    """Stored warns; every `limit` warns triggers an auto-timeout."""

    def __init__(self, store, clock=time.time):
        self.store = store
        self.clock = clock

    def add(self, gid, uid, reason, moderator, limit):
        w = self.store.warns(gid, uid)
        w.append({"reason": (reason or "no reason")[:300], "mod": str(moderator), "ts": int(self.clock())})
        self.store.mark_dirty()
        n = len(w)
        return n, bool(limit and limit > 0 and n % limit == 0)

    def list(self, gid, uid):
        return list(self.store.warns(gid, uid))

    def clear(self, gid, uid):
        w = self.store.warns(gid, uid)
        n = len(w)
        w.clear()
        self.store.mark_dirty()
        return n

    def remove_last(self, gid, uid):
        w = self.store.warns(gid, uid)
        if w:
            w.pop()
            self.store.mark_dirty()
        return len(w)

    def top(self, gid, k=10):
        g = self.store.guild_warns(gid)
        return sorted(((len(v), uid) for uid, v in g.items() if v), reverse=True)[:k]


# ---------------------------------------------------------------- anti-spam

def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


class SpamDetector:
    """Flood (N msgs in S secs), duplicates (same text N times in S secs), mention spam."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.hist = defaultdict(deque)  # (gid, uid) -> deque[(t, norm_text)]

    def check(self, gid, uid, text, mentions, cfg):
        if not cfg.get("spam_enabled", True):
            return None
        now = self.clock()
        if mentions >= cfg.get("mention_limit", 6):
            self.hist.pop((gid, uid), None)
            return f"mention spam ({mentions} mentions in one message)"
        h = self.hist[(gid, uid)]
        h.append((now, _norm(text)))
        keep = max(cfg.get("flood_secs", 8), cfg.get("dup_secs", 30))
        while h and now - h[0][0] > keep:
            h.popleft()
        flood_n, flood_s = cfg.get("flood_msgs", 6), cfg.get("flood_secs", 8)
        recent = [x for x in h if now - x[0] <= flood_s]
        if len(recent) >= flood_n:
            h.clear()
            return f"flooding ({len(recent)} messages in {flood_s}s)"
        norm = _norm(text)
        if norm:
            dup_n, dup_s = cfg.get("dup_count", 4), cfg.get("dup_secs", 30)
            dups = sum(1 for t, m in h if m == norm and now - t <= dup_s)
            if dups >= dup_n:
                h.clear()
                return f"duplicate spam ({dups}x the same message)"
        return None


# ---------------------------------------------------------------- anti-raid

class RaidDetector:
    """Fires once when joins within `raid_secs` reach `raid_joins`."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.joins = defaultdict(deque)
        self.fired_at = {}

    def record_join(self, gid, cfg):
        if not cfg.get("raid_enabled", True):
            return False
        now = self.clock()
        window = cfg.get("raid_secs", 60)
        q = self.joins[gid]
        q.append(now)
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= cfg.get("raid_joins", 10):
            last = self.fired_at.get(gid)
            if last is None or now - last > window:
                self.fired_at[gid] = now
                return True
        return False


# ---------------------------------------------------------------- filters

INVITE_RE = re.compile(r"(discord\.gg|discord(app)?\.com/invite|dsc\.gg)/\S+", re.I)
LINK_RE = re.compile(r"https?://\S+|\bwww\.\S+|\b[a-z0-9-]+\.(com|net|org|io|gg|xyz|ru|ly|me|co|tv)(/\S*)?\b", re.I)


def link_violation(text, mode):
    """mode: off | invites | all -> 'invite' / 'link' / None"""
    if mode not in ("invites", "all") or not text:
        return None
    if INVITE_RE.search(text):
        return "invite"
    if mode == "all" and LINK_RE.search(text):
        return "link"
    return None


def badword_hit(text, words):
    if not text or not words:
        return None
    low = text.lower()
    toks = set(re.findall(r"[a-z0-9']+", low))
    for w in words:
        w = w.lower().strip()
        if not w:
            continue
        if (" " in w and w in low) or w in toks:
            return w
    return None
