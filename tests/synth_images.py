"""Synthetic test images (generated in-process, deterministic). No files are committed."""
import io
import random

from PIL import Image, ImageDraw, ImageFilter


def _bytes(img, fmt="JPEG", **kw):
    b = io.BytesIO()
    img.save(b, fmt, **kw)
    return b.getvalue()


def _noise(w, h, lo, hi, rng, color=False):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            if color:
                px[x, y] = (rng.randint(lo, hi), rng.randint(lo, hi), rng.randint(lo, hi))
            else:
                v = rng.randint(lo, hi)
                px[x, y] = (v, v, v)
    return img


def dark_blurry(seed=1):
    rng = random.Random(seed)
    img = _noise(160, 120, 0, 70, rng, color=True).resize((1200, 900))
    d = ImageDraw.Draw(img)
    d.ellipse((400, 300, 700, 600), fill=(60, 50, 45))
    return _bytes(img.filter(ImageFilter.GaussianBlur(14)), quality=85)


def bright_colorful(seed=2):
    rng = random.Random(seed)
    img = Image.new("RGB", (1600, 1200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    cols = [(255, 30, 30), (30, 220, 40), (30, 60, 255), (255, 220, 0), (255, 0, 200), (0, 230, 230), (255, 120, 0)]
    for y in range(0, 1200, 40):
        for x in range(0, 1600, 40):
            d.rectangle((x, y, x + 39, y + 39), fill=rng.choice(cols))
    return _bytes(img, quality=92)


def monochrome(seed=3):
    rng = random.Random(seed)
    img = _noise(200, 150, 40, 210, rng).resize((1600, 1200), Image.BICUBIC)
    d = ImageDraw.Draw(img)
    for i in range(30):
        x, y = rng.randint(0, 1500), rng.randint(0, 1100)
        d.rectangle((x, y, x + 90, y + 60), outline=(250, 250, 250), width=4)
    return _bytes(img, quality=90)


def screenshot(seed=4):
    rng = random.Random(seed)
    img = Image.new("RGB", (1920, 1080), (246, 247, 249))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1920, 64), fill=(32, 34, 37))
    d.rectangle((0, 64, 300, 1080), fill=(230, 232, 236))
    for row in range(14):
        y = 110 + row * 66
        x = 340
        for _ in range(rng.randint(4, 12)):
            wlen = rng.randint(30, 120)
            d.rectangle((x, y, x + wlen, y + 14), fill=(40, 40, 45))
            x += wlen + 14
    d.rectangle((1600, 980, 1880, 1040), fill=(88, 101, 242))
    return _bytes(img, "PNG")


def meme(seed=5):
    rng = random.Random(seed)
    w, h = 800, 800
    img = _noise(80, 60, 30, 200, rng, color=True).resize((w, int(h * 0.7))).filter(ImageFilter.GaussianBlur(2))
    canvas = Image.new("RGB", (w, h), (255, 255, 255))
    canvas.paste(img, (0, int(h * 0.15)))
    d = ImageDraw.Draw(canvas)
    for y0 in (30, int(h * 0.88)):
        x = 60
        while x < w - 120:
            ln = rng.randint(40, 110)
            d.rectangle((x, y0, x + ln, y0 + 34), fill=(0, 0, 0))
            x += ln + 22
    return _bytes(canvas, quality=90)


def glow_room(seed=6, blur=3):
    """Halo-style helmet lit blue in a dark room (Michael's example)."""
    rng = random.Random(seed)
    img = _noise(120, 160, 0, 25, rng, color=True).resize((900, 1200))
    d = ImageDraw.Draw(img)
    d.ellipse((250, 300, 650, 760), fill=(20, 22, 30))
    d.rectangle((300, 470, 600, 560), fill=(40, 150, 255))
    d.ellipse((330, 330, 380, 380), fill=(30, 120, 255))
    return _bytes(img.filter(ImageFilter.GaussianBlur(blur)), quality=88)


def skin_portrait(seed=7):
    img = Image.new("RGB", (900, 1200), (90, 110, 140))
    d = ImageDraw.Draw(img)
    d.ellipse((200, 200, 700, 900), fill=(224, 172, 140))
    return _bytes(img.filter(ImageFilter.GaussianBlur(1)), quality=90)


def wallpaper(seed=8):
    """Flat vector-art wallpaper at a screen size: NOT a screenshot."""
    img = Image.new("RGB", (1920, 1080), (30, 60, 110))
    d = ImageDraw.Draw(img)
    d.polygon([(0, 1080), (900, 300), (1920, 1080)], fill=(60, 110, 170))
    d.polygon([(600, 1080), (1500, 500), (1920, 900), (1920, 1080)], fill=(90, 150, 210))
    return _bytes(img.filter(ImageFilter.GaussianBlur(1)), "PNG")


ALL = {"dark_blurry": dark_blurry, "bright_colorful": bright_colorful, "monochrome": monochrome,
       "screenshot": screenshot, "meme": meme, "glow_room": glow_room, "skin_portrait": skin_portrait}
