"""Find the streamer's webcam overlay so it can go on top and gameplay below.

Heuristic: a face that sits in the same small region across most sampled frames is a facecam overlay.
Writes a preview PNG with the detected box so the agent (Claude can see images) can confirm or correct it.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def detect_facecam(video: Path, preview_png: Path | None = None, samples: int = 24) -> dict | None:
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces, frame_for_preview = [], None
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (i + 0.5) / samples))
        ok, frame = cap.read()
        if not ok:
            continue
        if frame_for_preview is None or i == samples // 2:
            frame_for_preview = frame
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for (x, y, w, h) in casc.detectMultiScale(gray, 1.1, 6, minSize=(max(24, W // 40), max(24, W // 40))):
            if w < W * 0.28:                       # big faces = in-game characters / full-cam streams
                faces.append((x + w / 2, y + h / 2, w, h))
    cap.release()
    if not faces:
        return None
    pts = np.array(faces)
    # largest cluster of face centres within ~6% of the frame width
    best, best_n = None, 0
    for p in pts:
        m = np.hypot(pts[:, 0] - p[0], pts[:, 1] - p[1]) < W * 0.06
        if m.sum() > best_n:
            best, best_n = pts[m], m.sum()
    if best_n < max(4, samples * 0.35):
        return None
    cx, cy, fw, fh = np.median(best, axis=0)
    # typical webcam box ~ 3.8x face width, between 4:3 and 16:9
    bw = min(W * 0.45, fw * 3.8)
    bh = min(H * 0.5, bw * 0.78)
    x, y = cx - bw / 2, cy - bh * 0.45
    # snap to the frame edge if close (overlays usually touch a corner)
    if x < W * 0.08: x = 0
    if x + bw > W * 0.92: x = W - bw
    if y < H * 0.08: y = 0
    if y + bh > H * 0.92: y = H - bh
    box = {k: int(round(v)) & ~1 for k, v in dict(x=max(0, x), y=max(0, y), w=bw, h=bh).items()}
    box["confidence"] = round(best_n / samples, 2)
    if preview_png is not None and frame_for_preview is not None:
        img = frame_for_preview.copy()
        cv2.rectangle(img, (box["x"], box["y"]), (box["x"] + box["w"], box["y"] + box["h"]), (0, 255, 255), 4)
        cv2.imwrite(str(preview_png), img)
    return box


def save_frame(video: Path, t: float, out_png: Path) -> Path:
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, frame = cap.read()
    cap.release()
    if ok:
        cv2.imwrite(str(out_png), frame)
    return out_png
