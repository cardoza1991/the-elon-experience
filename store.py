"""store.py - tiny JSON persistence for config, warns and per-user memory.

Everything lives in data/state.json (gitignored). Writes are atomic
(tmp file + os.replace) and debounced: callers mark_dirty() and the bot
flushes periodically and on shutdown.
"""
import copy
import json
import os
import threading
import time

DEFAULT_CONFIG = {
    # --- personality / reply policy ---
    "jump_rate": 0.08,          # chance to jump into a random message
    "topic_jump_rate": 0.25,    # chance when the message hits an Elon topic
    "channel_cooldown": 45,     # seconds between unsolicited jump-ins per channel
    "user_cooldown": 120,       # seconds between unsolicited jump-ins at the same user
    "mention_cooldown": 3,      # anti-abuse throttle for @mentions/replies per user
    "tweet_threshold": 0.15,    # min TF-IDF cosine for an auto-tweet
    "ignore_channels": [],      # never jump in here (mentions still answered)
    "roast_channels": [],       # if non-empty, only jump in here
    "immune_roles": [],         # role ids immune to auto-mod
    "modlog": None,             # channel id for mod-log embeds
    # --- warns ---
    "warn_limit": 3,            # auto-timeout every N warns
    "warn_timeout": "1h",
    # --- anti-spam ---
    "spam_enabled": True,
    "flood_msgs": 6, "flood_secs": 8,
    "dup_count": 4, "dup_secs": 30,
    "mention_limit": 6,
    "spam_timeout": "5m",
    # --- anti-raid ---
    "raid_enabled": True,
    "raid_joins": 10, "raid_secs": 60,
    "raid_action": "lock",      # lock | alert
    # --- filters ---
    "link_filter": "off",       # off | invites | all
    "badwords_enabled": False,
    "badwords": [],
    "blocklist_extra": [],      # extra words the bot must never echo
}


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.dirty = False
        self.data = {"guilds": {}, "users": {}, "warns": {}}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = json.load(f)
                for k in self.data:
                    if isinstance(loaded.get(k), dict):
                        self.data[k] = loaded[k]
            except Exception as e:  # corrupt file: keep it aside, start fresh
                print(f"store: could not read {path}: {e}")
                try:
                    os.replace(path, path + f".corrupt-{int(time.time())}")
                except OSError:
                    pass

    # ---- config ----
    def guild_raw(self, gid):
        return self.data["guilds"].setdefault(str(gid), {})

    def cfg(self, gid):
        """Effective config for a guild (defaults + overrides)."""
        c = copy.deepcopy(DEFAULT_CONFIG)
        c.update(self.data["guilds"].get(str(gid), {}).get("config", {}))
        return c

    def set_cfg(self, gid, key, value):
        if key not in DEFAULT_CONFIG:
            raise KeyError(key)
        with self.lock:
            self.guild_raw(gid).setdefault("config", {})[key] = value
            self.mark_dirty()

    def guild_state(self, gid, key, default=None):
        return self.data["guilds"].get(str(gid), {}).get(key, default)

    def set_guild_state(self, gid, key, value):
        with self.lock:
            self.guild_raw(gid)[key] = value
            self.mark_dirty()

    # ---- users ----
    def user(self, gid, uid):
        k = f"{gid}:{uid}"
        u = self.data["users"].get(k)
        if u is None:
            u = {"name": "", "msgs": [], "roasts": 0, "words": {}, "topics": {}, "recent": []}
            self.data["users"][k] = u
        return u

    # ---- warns ----
    def warns(self, gid, uid):
        return self.data["warns"].setdefault(str(gid), {}).setdefault(str(uid), [])

    def guild_warns(self, gid):
        return self.data["warns"].get(str(gid), {})

    # ---- io ----
    def mark_dirty(self):
        self.dirty = True

    def save(self, force=False):
        if not self.path or not (self.dirty or force):
            return False
        with self.lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.dirty = False
        return True
