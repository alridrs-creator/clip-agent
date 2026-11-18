"""GTA 6 screen events (optional plugin, OFF by default): full-screen game text such as a death, an arrest or a
mission result, found by matching small reference crops ("templates") of the real GTA 6 text against each frame.

How it works (per second of video, 2 frames a second):
  1. cheap gate: does the middle band of the screen have any sharp edges at all? Flat frames (black fades,
     sky, loading screens) stop here.
  2. template match: each crop in presets/gta6_screens/<label>.png is matched inside that band (normalised
     cross-correlation on edge maps, several scales). Edges, not pixels, so one template finds the white
     "wasted" over the red-tinted death replay and the grey one on black that follows it.
  3. a label counts when it matches in 2 frames within 1.5 s (one frame can be a fluke).

Templates are cut from real recordings (`python -m clipagent screen-template <video> <second> <label>`),
so the plugin only knows the screens someone has actually captured. No templates -> it does nothing.
Needs opencv-python (already used elsewhere); no OCR engine.

Turn on:  config signals_gta6_screen=true, or `spikes multi <video> --signals chat,audio,screen`
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from .common import PRESETS, config, tool

W, H = 640, 360
BAND = (0.26, 0.66, 0.12, 0.88)          # rows / cols of the frame where full-screen game text sits
TEMPLATE_DIR = PRESETS / "gta6_screens"
SCALES = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1)


def enabled() -> bool:
    return bool(config().get("signals_gta6_screen"))


def _frames(media: Path, start: float = 0.0, dur: float | None = None, fps: int = 2) -> np.ndarray:
    cmd = [tool("ffmpeg") or "ffmpeg", "-v", "quiet"]
    if start:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-i", str(media)]
    if dur:
        cmd += ["-t", f"{dur:.2f}"]
    cmd += ["-an", "-vf", f"fps={fps},scale={W}:{H}", "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    raw = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.uint8)
    k = len(raw) // (W * H)
    return raw[: k * W * H].reshape(k, H, W)


def _band(g: np.ndarray) -> np.ndarray:
    r0, r1, c0, c1 = BAND
    return g[int(H * r0):int(H * r1), int(W * c0):int(W * c1)]


def text_gate(g: np.ndarray) -> float:
    """Share of edge pixels in the middle band (0 = flat: nothing to match)."""
    import cv2
    return float(cv2.Canny(_band(g), 40, 120).mean() / 255)


def _edges(img: np.ndarray) -> np.ndarray:
    """Polarity-free edge strength (white-on-dark and dark-on-light text look the same)."""
    import cv2
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy) / 1020.0      # absolute scale (not per image: a dark, flat frame stays flat)
    return cv2.GaussianBlur(mag, (3, 3), 0)


_TPL: list[tuple[str, np.ndarray]] | None = None
_EDGE: dict[str, list] = {}


def templates() -> list[tuple[str, np.ndarray]]:
    """(label, grey crop) for every presets/gta6_screens/*.png; label = file name up to the first '_' digit run."""
    global _TPL
    if _TPL is None:
        import cv2
        _TPL = []
        for f in sorted(TEMPLATE_DIR.glob("*.png")) if TEMPLATE_DIR.exists() else []:
            img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                _TPL.append((f.stem.rsplit("_", 1)[0] if f.stem[-1].isdigit() else f.stem, img))
                _EDGE[f.stem] = [(s, _edges(cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)))
                                 for s in SCALES]
    return _TPL


def match_frame(g: np.ndarray, min_score: float = 0.6) -> tuple[str, float] | None:
    """Best template label in one 640x360 grey frame, or None."""
    import cv2
    band = _edges(_band(g))
    best = None
    templates()
    for key, scaled in _EDGE.items():
        label = key.rsplit("_", 1)[0] if key[-1].isdigit() else key
        for s, t in scaled:
            if t.shape[0] >= band.shape[0] or t.shape[1] >= band.shape[1]:
                continue
            res = cv2.matchTemplate(band, t, cv2.TM_CCOEFF_NORMED)
            # normalised correlation is unstable where the frame is nearly flat (maps, menus, black): only accept
            # places with at least 20% of the template's own edge contrast
            k = (t.shape[1], t.shape[0])
            mu = cv2.boxFilter(band, -1, k, normalize=True, borderType=cv2.BORDER_CONSTANT)
            mu2 = cv2.boxFilter(band * band, -1, k, normalize=True, borderType=cv2.BORDER_CONSTANT)
            sd = np.sqrt(np.maximum(mu2 - mu * mu, 0))
            x0, y0 = k[0] // 2, k[1] // 2
            sd = sd[y0:y0 + res.shape[0], x0:x0 + res.shape[1]]
            res = np.where(np.isfinite(res) & (sd >= 0.2 * float(t.std())), res, 0)
            v = float(res.max())
            if v >= min_score and (best is None or v > best[1]):
                best = (label, v)
    return best


def screen_events(media: Path, start: float = 0.0, dur: float | None = None, gate: float = 0.01) -> list[dict]:
    """[{t, label, score}] for each confirmed screen event (t = seconds from `start`)."""
    if not templates():
        return []
    fr = _frames(media, start, dur)
    hits = []
    for i, g in enumerate(fr):
        if text_gate(g) < gate:
            continue
        m = match_frame(g)
        if m:
            hits.append((i / 2, m[0], m[1]))
    out: list[dict] = []
    for j, (t, label, v) in enumerate(hits):        # confirmed by a second frame with the same label within 1.5 s
        if any(l2 == label and 0 < t2 - t <= 1.5 for t2, l2, _ in hits[j + 1:j + 4]):
            if not out or out[-1]["label"] != label or t - out[-1]["t"] > 5:
                out.append({"t": t, "label": label, "score": round(v, 3)})
    return out


def screen_score(events: list[dict], n: int) -> np.ndarray:
    """Per second: 1.0 at each confirmed GTA 6 screen event."""
    out = np.zeros(n)
    for e in events:
        if 0 <= int(e["t"]) < n:
            out[int(e["t"])] = 1.0
    return out


def save_template(media: Path, second: float, label: str, box: tuple[int, int, int, int] | None = None) -> Path:
    """Cut a template from a real frame: the text-dense part of the middle band (or `box` x,y,w,h in 640x360)."""
    import cv2
    g = _frames(media, max(0.0, second), 0.6)[0]
    if box:
        x, y, w, h = box
        crop = g[y:y + h, x:x + w]
    else:
        band = _band(g)
        e = cv2.Canny(band, 80, 200)
        rows = np.where(e.mean(axis=1) > 0.08 * 255)[0]
        cols = np.where(e.mean(axis=0) > 0.04 * 255)[0]
        if len(rows) < 4 or len(cols) < 4:
            raise SystemExit("no text found in the middle of that frame: pass --box x,y,w,h")
        crop = band[max(0, rows[0] - 3):rows[-1] + 4, max(0, cols[0] - 3):cols[-1] + 4]
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    n = len(list(TEMPLATE_DIR.glob(f"{label}_*.png")))
    out = TEMPLATE_DIR / f"{label}_{n + 1}.png"
    cv2.imwrite(str(out), crop)
    global _TPL
    _TPL = None
    _EDGE.clear()
    return out
