"""vision.py - 'quietly judge pictures' with NO AI: Pillow pixel statistics + rules.

analyze(bytes) -> dict of cheap traits (brightness, contrast, saturation, hue, colorfulness, sharpness,
noise, orientation, resolution, screenshot/meme/monochrome/glow heuristics, EXIF hints).
ImageJudge decides what to do with an image post (react / one-line verdict) and builds the verdict
from line banks in image_lines.py. Nothing here ever describes a person: if the picture looks like a
selfie (lots of skin tones, portrait framing, front camera) only lighting/framing/background is judged.
"""
import hashlib
import io
import math
import re
import time
from collections import deque

try:
    from PIL import Image, ImageChops, ImageFilter, ImageStat
    Image.MAX_IMAGE_PIXELS = 60_000_000  # decompression-bomb guard
    HAVE_PIL = True
except Exception:  # pragma: no cover - feature just switches off
    HAVE_PIL = False

import image_lines as IL

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MAX_BYTES = 8 * 1024 * 1024

ASK_RE = re.compile(
    r"\b(is (this|it|that|my \w+) (fire|good|mid|trash|cool|clean|hard|tuff|ass|bad|nice|decent|valid|it)|"
    r"fire or (what|not|nah|mid)|rate (this|it|my|that|pls|please)|^rate\b|thoughts\??\s*$|what do (you|u|y'?all|yall) think|"
    r"how does (this|it|that) look|opinions?\??\s*$|be honest|honest opinion|roast (this|my|it)|judge (this|it|my)|"
    r"out of 10|/10\b|(good|cool|fire|clean) (or|right)\??|is this (a )?w\b|w or l|hot or not|verdict)", re.I)

UI_SIZES = {(1920, 1080), (2560, 1440), (3840, 2160), (1366, 768), (1440, 900), (1536, 864), (1280, 720), (1600, 900),
            (2880, 1800), (2560, 1600), (3024, 1964), (3456, 2234), (1280, 800), (1680, 1050), (1920, 1200),
            (1170, 2532), (1179, 2556), (1284, 2778), (1290, 2796), (1125, 2436), (828, 1792), (750, 1334),
            (1242, 2688), (1080, 2400), (1080, 2340), (1080, 1920), (1440, 3200), (1440, 3088), (1080, 2408),
            (720, 1600), (1440, 2560), (1242, 2208), (2048, 2732), (1668, 2388), (1640, 2360), (2732, 2048)}
SHOT_NAME_RE = re.compile(r"screen ?shot|screenshot|scr_|^image\d*\.png$|^unknown\.png$|^capture|snip", re.I)
MEME_NAME_RE = re.compile(r"meme|caption|funny", re.I)

_HUES = [(20, "red"), (45, "orange"), (68, "yellow"), (160, "green"), (195, "teal"), (255, "blue"),
         (290, "purple"), (335, "pink"), (361, "red")]


def hue_name(deg):
    for top, name in _HUES:
        if deg < top:
            return name
    return "red"


def is_image_attachment(filename, content_type=None, size=0):
    fn = (filename or "").lower()
    ok = fn.endswith(IMAGE_EXT) or (content_type or "").startswith("image/")
    return ok and not fn.endswith(".svg") and (size or 0) <= MAX_BYTES


def _exif(img):
    out = {"device": "", "hour": None, "night": False, "front_cam": False}
    try:
        ex = img.getexif()
    except Exception:
        return out
    if not ex:
        return out
    make = str(ex.get(0x010F, "") or "").strip("\x00 ")
    model = str(ex.get(0x0110, "") or "").strip("\x00 ")
    if model:
        mk = make.split()[0].lower() if make else ""
        plain = not mk or mk in ("apple", "google") or mk in model.lower()
        out["device"] = model if plain else f"{make.split()[0].capitalize()} {model}"
        out["device"] = re.sub(r"[^\w .+-]", "", out["device"])[:32]
    try:
        sub = ex.get_ifd(0x8769)
    except Exception:
        sub = {}
    dto = str(sub.get(0x9003, "") or ex.get(0x0132, "") or "")
    m = re.match(r"\d{4}:\d\d:\d\d (\d\d):", dto)
    if m:
        out["hour"] = int(m.group(1))
    try:
        exp = float(sub.get(0x829A, 0) or 0)
    except (TypeError, ValueError):
        exp = 0.0
    iso = sub.get(0x8827, 0)
    if isinstance(iso, (tuple, list)):
        iso = iso[0] if iso else 0
    try:
        iso = int(iso or 0)
    except (TypeError, ValueError):
        iso = 0
    out["night"] = exp >= 0.1 or iso >= 1600
    lens = str(sub.get(0xA434, "") or "").lower()
    out["front_cam"] = "front" in lens
    return out


def _skin(r, g, b):
    return (r > 95 and g > 40 and b > 20 and max(r, g, b) - min(r, g, b) > 15 and abs(r - g) > 15
            and r > g and r > b)


def analyze(data, filename=""):
    """Pixel statistics for one image (bytes). Raises ValueError on unreadable input."""
    if not HAVE_PIL:
        raise ValueError("Pillow not installed")
    if len(data) > MAX_BYTES:
        raise ValueError("too big")
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        fmt = (img.format or "").upper()
        animated = bool(getattr(img, "is_animated", False))
        ex = _exif(img)
        try:
            img.seek(0)
        except EOFError:
            pass
        if fmt == "JPEG":
            img.draft("RGB", (768, 768))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(bg, img)
        img = img.convert("RGB")
    except Exception as e:
        raise ValueError(f"unreadable image: {type(e).__name__}") from e

    f = {"w": w, "h": h, "mp": round(w * h / 1e6, 2), "format": fmt, "animated": animated,
         "filename": filename or ""}
    f.update(ex)
    ar = w / h if h else 1.0
    f["aspect"] = round(ar, 3)
    f["orientation"] = "square" if 0.9 <= ar <= 1.1 else "landscape" if ar > 1.1 else "portrait"
    f["wide"] = ar >= 2.2
    f["tall"] = ar <= 0.48

    mid = img.copy()
    mid.thumbnail((384, 384))
    gray = mid.convert("L")
    st = ImageStat.Stat(gray)
    bright = st.mean[0] / 255
    contrast = st.stddev[0] / 128
    f["brightness"], f["contrast"] = round(bright, 3), round(contrast, 3)
    hist = gray.histogram()
    n = sum(hist) or 1
    f["dark_frac"] = round(sum(hist[:45]) / n, 3)
    f["clip_frac"] = round(sum(hist[250:]) / n, 3)

    gw, gh = gray.size
    inner = (2, 2, max(3, gw - 2), max(3, gh - 2))  # filters produce junk on the 1px border
    lap = gray.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)).crop(inner)
    f["sharpness"] = round(ImageStat.Stat(lap).var[0], 1)
    edges = gray.filter(ImageFilter.FIND_EDGES).crop(inner)
    eh = edges.histogram()
    en = sum(eh) or 1
    f["flat_frac"] = round(sum(eh[:3]) / en, 3)
    f["strong_edge_frac"] = round(sum(eh[60:]) / en, 3)
    med = gray.filter(ImageFilter.MedianFilter(3))
    f["noise"] = round(ImageStat.Stat(ImageChops.difference(gray, med).crop(inner)).mean[0], 2)

    small = img.copy()
    small.thumbnail((128, 128))
    sw, sh = small.size
    px = list(small.getdata())
    hsv = list(small.convert("HSV").getdata())
    total = len(px) or 1
    rg_l, yb_l = [], []
    sat_sum = 0.0
    bins = [0] * 12
    glow_bins = [0] * 12
    colored = glow = skin = skin_c = 0
    for i, ((r, g, b), (hh, ss, vv)) in enumerate(zip(px, hsv)):
        rg_l.append(r - g)
        yb_l.append(0.5 * (r + g) - b)
        s, v = ss / 255, vv / 255
        sat_sum += s
        if s > 0.22 and v > 0.18:
            colored += 1
            bins[int(hh / 256 * 12) % 12] += 1
        if s > 0.45 and v > 0.55:
            glow += 1
            glow_bins[int(hh / 256 * 12) % 12] += 1
        if _skin(r, g, b):
            skin += 1
            x, y = i % sw, i // sw
            if sw * 0.2 <= x <= sw * 0.8 and sh * 0.15 <= y <= sh * 0.85:
                skin_c += 1

    def _std(a):
        m = sum(a) / len(a)
        return math.sqrt(sum((v - m) ** 2 for v in a) / len(a)), m
    srg, mrg = _std(rg_l)
    syb, myb = _std(yb_l)
    f["colorfulness"] = round(math.sqrt(srg ** 2 + syb ** 2) + 0.3 * math.sqrt(mrg ** 2 + myb ** 2), 1)
    f["saturation"] = round(sat_sum / total, 3)
    f["colored_frac"] = round(colored / total, 3)
    top = max(range(12), key=lambda k: bins[k])
    f["hue_share"] = round(bins[top] / colored, 3) if colored else 0.0
    f["color"] = hue_name(top * 30 + 15) if colored / total > 0.03 else ("black" if bright < 0.2 else
                                                                         "white" if bright > 0.85 else "gray")
    gtop = max(range(12), key=lambda k: glow_bins[k])
    f["glow_frac"] = round(glow / total, 3)
    f["glow_color"] = hue_name(gtop * 30 + 15) if glow else ""
    f["skin_frac"] = round(skin / total, 3)
    center_n = max(1, int(sw * 0.6) * int(sh * 0.7))
    f["skin_center"] = round(skin_c / center_n, 3)
    f["unique_colors"] = len(set((r >> 3, g >> 3, b >> 3) for (r, g, b) in px))

    # bands for meme detection (rows on the 128px thumbnail)
    gl = list(small.convert("L").getdata())

    def band(y0, y1):
        vals = gl[int(y0 * sh) * sw:int(y1 * sh) * sw] or [128]
        return sum(v > 225 for v in vals) / len(vals), sum(v < 30 for v in vals) / len(vals)
    tw, tb = band(0, 0.14)
    bw, bb = band(0.86, 1.0)
    mw, mb = band(0.3, 0.7)
    f["meme_bands"] = bool((tw > 0.7 or bw > 0.7) and mw < 0.45) or bool((tb > 0.8 and bb > 0.8) and mb < 0.5
                                                                           and f["colored_frac"] > 0.1)
    _derive(f)
    return f


def _derive(f):
    b, c = f["brightness"], f["contrast"]
    f["very_dark"] = b < 0.13
    f["dark"] = b < 0.27 or f["dark_frac"] > 0.6
    f["overexposed"] = (b > 0.83 or f["clip_frac"] > 0.4)
    f["low_contrast"] = c < 0.12
    f["high_contrast"] = c > 0.42
    f["mono"] = f["saturation"] < 0.07 and f["colored_frac"] < 0.05
    f["duotone"] = (not f["mono"]) and f["colored_frac"] > 0.3 and f["hue_share"] > 0.8
    f["vibrant"] = f["colorfulness"] > 55 or (f["saturation"] > 0.42 and f["colored_frac"] > 0.4)
    f["glow"] = f["dark"] and 0.005 <= f["glow_frac"] <= 0.4
    # dim / low-contrast photos naturally have weaker edges: scale the blur threshold with contrast
    blur_cut = 25 * max(0.3, min(1.0, c / 0.25))
    f["blurry"] = f["sharpness"] < blur_cut and not (f["low_contrast"] and f["mono"] and f["flat_frac"] > 0.97)
    f["sharp"] = f["sharpness"] > 400
    shot = 0
    if (f["w"], f["h"]) in UI_SIZES:
        shot += 2
    named = bool(SHOT_NAME_RE.search(f["filename"]))
    if named:
        shot += 3
    texty = f["flat_frac"] > 0.55 and 0.03 < f["strong_edge_frac"] < 0.35  # flat UI + crisp text-like edges
    if texty:
        shot += 2
    if f["unique_colors"] < 700 and f["noise"] < 3:
        shot += 1
    if f["format"] == "PNG":
        shot += 1
    if f["device"]:
        shot -= 3
    f["screenshot"] = shot >= 4 and (named or texty)  # flat wallpapers/vector art aren't screenshots
    f["meme"] = f["meme_bands"] or bool(MEME_NAME_RE.search(f["filename"]))
    if f["screenshot"] or f["meme"]:
        f["blurry"] = False  # UI/caption content: focus talk makes no sense
    f["grainy"] = f["noise"] > 9 and not f["screenshot"]
    f["hires"] = f["mp"] >= 8
    f["tiny"] = f["mp"] < 0.12
    # people-safe mode: likely a selfie / face-heavy photo -> never judge the person
    f["people"] = (not f["screenshot"] and not f["meme"]) and (
        f["front_cam"] or f["skin_center"] > 0.28 or (f["skin_frac"] > 0.22 and f["orientation"] != "landscape"))
    f["night_hint"] = f["night"] or (f["hour"] is not None and (f["hour"] >= 21 or f["hour"] < 5))
    f["score"] = score(f)
    f["primary"] = primary(f)
    return f


def score(f):
    s = 6.0
    if f["meme"]:
        s = 6.5
    elif f["screenshot"]:
        s = 4.0
    s += 1.2 * f["vibrant"] + 1.0 * f["sharp"] + 0.5 * f["hires"] + 1.0 * f["glow"] + 0.4 * f["high_contrast"]
    s -= 2.0 * f["blurry"] + 1.0 * f["grainy"] + 1.5 * f["tiny"] + 1.5 * f["overexposed"]
    s -= 1.5 * (f["very_dark"] and not f["glow"]) + 1.0 * (f["low_contrast"] and not f["screenshot"])
    jitter = int(hashlib.md5(repr(sorted((k, v) for k, v in f.items() if k != "filename")).encode()).hexdigest(), 16)
    s += ((jitter % 11) - 5) / 10
    return max(1, min(10, int(round(s))))


def primary(f):
    if f["meme"]:
        return "meme"
    if f["screenshot"]:
        return "screenshot"
    if f["glow"]:
        return "glow_blurry" if f["blurry"] else "glow"
    if f["dark"] and f["blurry"]:
        return "dark_blurry"
    if f["blurry"]:
        return "blurry"
    if f["mono"]:
        return "mono"
    if f["dark"]:
        return "dark"
    if f["overexposed"]:
        return "overexposed"
    if f["duotone"]:
        return "duotone"
    if f["vibrant"]:
        return "vibrant"
    if f["grainy"]:
        return "grainy"
    if f["low_contrast"]:
        return "flat"
    if f["tiny"]:
        return "tiny"
    if f["wide"] or f["tall"]:
        return "wide" if f["wide"] else "tall"
    if f["hires"] and f["sharp"]:
        return "hires"
    return "generic"


# buckets allowed when the photo might show a person: lighting / framing / background only
PEOPLE_SAFE = {"glow", "glow_blurry", "dark", "overexposed", "flat", "mono", "duotone", "vibrant", "wide", "tall",
               "hires", "tiny"}
PEOPLE_EMOJI = ["📸", "👀", "💡", "🔥"]


def emojis(f):
    """1-2 reaction emojis that 'quietly judge' the picture."""
    out = []

    def add(e):
        if e not in out and len(out) < 2:
            out.append(e)
    if f["people"]:
        if f["glow"] or (f["vibrant"] and not f["blurry"]):
            add("🔥")
        if f["dark"]:
            add("💡")
        if f["hires"] or f["sharp"]:
            add("📸")
        add("👀")
        return out
    if f["meme"] or f["screenshot"]:
        add("🤡" if f["meme"] else "🤨")
        if f["score"] >= 8:
            add("🔥")
        elif f["screenshot"] and f["meme"]:
            add("🤨")
        return out
    if f["glow"]:
        add("👀")
        if not f["blurry"]:
            add("🔥")
    if f["dark"] and f["blurry"]:
        add("💀")
    if f["blurry"]:
        add("🥴")
    if f["mono"]:
        add("🗿")
    if f["vibrant"] and (f["sharp"] or not f["blurry"]):
        add("🔥")
    if f["hires"] and f["sharp"]:
        add("📸")
    if f["dark"]:
        add("👀")
    if f["overexposed"]:
        add("😎")
    if f["grainy"] or f["tiny"]:
        add("🤨")
    if not out:
        add("👀" if f["score"] < 7 else "🔥")
    return out


def _clock_word(hour):
    if hour is None:
        return ""
    if hour < 5:
        return f"{hour or 12}am, aka goblin hours"
    if hour < 12:
        return f"{hour}am"
    if hour == 12:
        return "noon"
    return f"{hour - 12}pm"


class ImageJudge:
    """Decides react / verdict for image posts and writes the verdict. Pure python, injectable clock/rng."""

    def __init__(self, brain_obj, clock=time.monotonic):
        self.brain = brain_obj
        self.clock = clock
        self.rng = brain_obj.rng
        self.reacts = {}       # (gid, cid) -> deque of times
        self.last_verdict = {}  # (gid, cid) -> time of last unprompted verdict
        brain_obj.banks.update(IL.BANKS)

    @staticmethod
    def asked(text):
        return bool(ASK_RE.search(text or ""))

    def react_allowed(self, gid, cid, cfg):
        now = self.clock()
        q = self.reacts.setdefault((gid, cid), deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= cfg.get("image_react_per_min", 8):
            return False
        q.append(now)
        return True

    def plan(self, gid, cid, uid, text, *, cfg, explicit=False, ignored=False, other_target=False, abusive=False):
        """-> (react: bool, verdict: bool). Call once per image post BEFORE downloading it."""
        mode = cfg.get("images", "on")
        if mode == "off" or ignored:
            return False, False
        react = self.react_allowed(gid, cid, cfg)
        if mode == "react-only" or abusive:
            return react, False
        if other_target and not explicit:
            return react, False
        if explicit or self.asked(text):
            return react, self.brain.allow_directed(gid, uid, cfg)
        now = self.clock()
        key = (gid, cid)
        if (now - self.last_verdict.get(key, -1e9) >= cfg.get("image_cooldown", 300)
                and self.rng.random() < cfg.get("image_reply_rate", 0.10)):
            self.last_verdict[key] = now
            return react, True
        return react, False

    def verdict(self, f, *, gid, uid, name, cfg, asked=False, extra=()):
        u = self.brain.store.user(gid, uid)
        ctx = {
            "name": name or "bro", "score": str(f["score"]), "color": f["color"] if f["color"] not in ("gray",) else "",
            "glow": f["glow_color"] or f["color"], "device": f["device"], "time": _clock_word(f["hour"]),
            "res": f"{f['w']}x{f['h']}", "mp": f"{f['mp']:g}", "_tone": cfg.get("tone", "normal"),
        }
        bucket = f["primary"]
        if f["people"] and bucket not in PEOPLE_SAFE:
            bucket = "people_safe"
        elif f["people"] and self.rng.random() < 0.5:
            bucket = "people_safe"
        parts = []
        if asked:
            lvl = "fire_hi" if f["score"] >= 7 else "fire_mid" if f["score"] >= 4 else "fire_lo"
            o = self.brain.pick(lvl, ctx, u, extra)
            if o:
                parts.append(o)
        line = None
        if f["device"] and not f["screenshot"] and self.rng.random() < 0.2:
            line = self.brain.pick("device", ctx, u, extra)
        elif f["night_hint"] and f["dark"] and self.rng.random() < 0.3:
            line = self.brain.pick("night", ctx, u, extra)
        line = line or self.brain.pick(bucket, ctx, u, extra) or self.brain.pick("generic_img", ctx, u, extra)
        if line:
            parts.append(line)
        text = " ".join(parts) or f"{f['score']}/10. No notes. Actually, many notes. Keeping them."
        if "/10" not in text and self.rng.random() < 0.6:
            text += f" {f['score']}/10."
        if self.brain.guard.hit(text, extra):
            text = f"{f['score']}/10. I've seen worse. I've seen better. Mostly better."
        self.brain.store.mark_dirty()
        return text
