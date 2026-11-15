"""Brand images: profile picture, banners, thumbnails (AI background + crisp text drawn by Pillow),
and post covers made from the best video frame.

Providers: 'pollinations' (free, no key) or 'gemini' (GEMINI_API_KEY, model from config).
"""
from __future__ import annotations

import base64
import io
import os
import urllib.parse
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .common import BRAND, config

SIZES = {
    "profile": (800, 800),
    "youtube_banner": (2560, 1440),     # safe area for text: centre 1546x423
    "twitch_banner": (1200, 480),
    "x_header": (1500, 500),
    "thumbnail": (1280, 720),           # 16:9 YouTube compilation cover
    "reel_cover": (1080, 1920),
}


def _gen_pollinations(prompt: str, w: int, h: int) -> Image.Image:
    # generate near the target aspect at a size the free service handles, then upscale/crop
    # free tier stamps a small logo bottom-right -> generate 10% taller and cut the bottom strip off
    scale = 1024 / max(w, h * 1.1)
    gw, gh = max(256, int(w * scale)), max(256, int(h * 1.1 * scale))
    url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
           f"?width={gw}&height={gh}&nologo=true")
    import time
    for attempt in range(4):           # anonymous free tier rate-limits per IP (HTTP 402/429)
        r = requests.get(url, timeout=180)
        if r.status_code not in (402, 429, 500, 502, 503):
            break
        time.sleep(20 * (attempt + 1))
    if r.status_code in (402, 429, 500, 502, 503):
        raise SystemExit("Pollinations free tier is rate-limiting (402/429). Wait a few minutes, or use "
                         "--provider gemini with a free GEMINI_API_KEY.")
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    return img.crop((0, 0, img.width, int(img.height / 1.1)))


def _gen_gemini(prompt: str, w: int, h: int) -> Image.Image:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY missing in .env (free key: aistudio.google.com/apikey)")
    model = config()["gemini_image_model"]
    aspect = f"Aspect ratio {w}:{h}. "
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": aspect + prompt}]}],
              "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}}, timeout=180)
    if r.status_code != 200:
        raise SystemExit(f"Gemini image error {r.status_code}: {r.text[:400]}")
    for part in r.json()["candidates"][0]["content"]["parts"]:
        data = part.get("inlineData") or part.get("inline_data")
        if data:
            return Image.open(io.BytesIO(base64.b64decode(data["data"]))).convert("RGB")
    raise SystemExit("Gemini returned no image (model may not support images on your plan).")


def generate(prompt: str, w: int, h: int, provider: str | None = None) -> Image.Image:
    provider = provider or ("gemini" if os.environ.get("GEMINI_API_KEY") else config()["image_provider"])
    prompt += " No text, no letters, no logos, no watermark. Original artwork, not a screenshot of any game."
    img = _gen_gemini(prompt, w, h) if provider == "gemini" else _gen_pollinations(prompt, w, h)
    return fit(img, w, h)


def fit(img: Image.Image, w: int, h: int) -> Image.Image:
    r = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * r + 0.5), int(img.height * r + 0.5)), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def _font(size: int) -> ImageFont.FreeTypeFont:
    from .render import font_file
    return ImageFont.truetype(str(font_file()), size)


def draw_text(img: Image.Image, text: str, box: tuple[int, int, int, int], color=(255, 255, 255),
              stroke=(0, 0, 0), max_size: int = 260, accent=None) -> Image.Image:
    """Big outlined text auto-fitted into box (x, y, w, h). Lines split on '|'. accent colours the 2nd line."""
    d = ImageDraw.Draw(img)
    lines = text.upper().split("|")
    x, y, bw, bh = box
    size = max_size
    while size > 20:
        f = _font(size)
        sw = max(2, size // 14)
        dims = [d.textbbox((0, 0), ln, font=f, stroke_width=sw) for ln in lines]
        tw = max(b[2] - b[0] for b in dims)
        th = sum(b[3] - b[1] for b in dims) + (len(lines) - 1) * size * 0.12
        if tw <= bw and th <= bh:
            break
        size -= 6
    cy = y + (bh - th) / 2
    for i, (ln, b) in enumerate(zip(lines, dims)):
        lw = b[2] - b[0]
        col = accent if (accent and i == 1) else color
        d.text((x + (bw - lw) / 2 - b[0], cy - b[1]), ln, font=f, fill=col, stroke_width=sw, stroke_fill=stroke)
        cy += (b[3] - b[1]) + size * 0.12
    return img


def make(kind: str, prompt: str, text: str = "", out: Path | None = None, provider: str | None = None) -> Path:
    w, h = SIZES[kind]
    img = generate(prompt, w, h, provider)
    if text:
        if kind == "youtube_banner":
            box = ((w - 1546) // 2 + 40, (h - 423) // 2, 1546 - 80, 423)
        elif kind == "profile":
            box = (int(w * 0.1), int(h * 0.3), int(w * 0.8), int(h * 0.4))
        elif kind == "reel_cover":
            box = (60, int(h * 0.18), w - 120, int(h * 0.3))
        else:
            box = (int(w * 0.05), int(h * 0.1), int(w * 0.9), int(h * 0.5))
        img = draw_text(img, text, box, accent=(255, 214, 0))
    out = out or BRAND / f"{kind}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    return out


def cover_from_frame(frame_png: Path, text: str, out: Path, vertical: bool = True) -> Path:
    """Post cover = real frame from the clip (best for originality) + big title."""
    w, h = SIZES["reel_cover"] if vertical else SIZES["thumbnail"]
    src = Image.open(frame_png).convert("RGB")
    # trim letterbox bars (black rows/cols) before cropping to the cover shape
    g = src.convert("L")
    rows = [sum(g.crop((0, y, g.width, y + 1)).getdata()) / g.width for y in range(g.height)]
    top = next((i for i, v in enumerate(rows) if v > 14), 0)
    bot = g.height - next((i for i, v in enumerate(reversed(rows)) if v > 14), 0)
    if bot - top > g.height * 0.5:
        src = src.crop((0, top, src.width, bot))
    img = fit(src, w, h)
    # darken top area for legibility
    grad = Image.new("L", (1, h))
    for yy in range(h):
        grad.putpixel((0, yy), int(170 * max(0, 1 - yy / (h * 0.55))))
    shade = Image.new("RGB", (w, h), (0, 0, 0))
    img = Image.composite(shade, img, grad.resize((w, h)))
    box = (60, int(h * 0.12), w - 120, int(h * 0.28)) if vertical else (60, 40, w - 120, int(h * 0.42))
    img = draw_text(img, text, box, accent=(255, 214, 0))
    img.save(out, quality=92)
    return out
