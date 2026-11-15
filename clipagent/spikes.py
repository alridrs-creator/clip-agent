"""Spike finder: locate highlight moments from chat activity, audio loudness or YouTube 'most replayed'.

Every finder returns windows [{start, end, peak, score, why}] sorted best first.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from .common import tool

HYPE = re.compile(r"\b(LUL|LULW|KEKW|OMEGALUL|LMAO+|LOL+|W+|POG\w*|CLIP\s*IT|CLIPPED|HOLY|WTF|NO\s*WAY|"
                  r"BRO|AINTNOWAY|ICANT|monkaS|Kappa|EZ|GG|\?{2,}|!{2,})\b", re.I)


def load_chat(path: Path) -> list[tuple[float, str]]:
    """(seconds, message) from TwitchDownloader JSON, chat-downloader 'simple' JSON, or live .jsonl."""
    path = Path(path)
    if path.suffix == ".jsonl":
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                j = json.loads(line)
                out.append((float(j["t"]), j.get("msg", "")))
        return out
    j = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(j, dict) and "comments" in j:          # TwitchDownloader
        out = []
        for c in j["comments"]:
            msg = c.get("message", {})
            body = msg.get("body") if isinstance(msg, dict) else str(msg)
            out.append((float(c.get("content_offset_seconds", 0)), body or ""))
        return out
    if isinstance(j, dict) and "messages" in j:          # our simple format
        return [(float(m["t"]), m.get("msg", "")) for m in j["messages"]]
    raise SystemExit(f"unknown chat format: {path}")


def _windows_from_signal(sig: np.ndarray, bin_s: float, top: int, pre: float, post: float,
                         min_gap: float, why: str) -> list[dict]:
    if len(sig) < 5:
        return []
    k = max(3, int(300 / bin_s))                       # ~5 min rolling baseline
    pad = np.pad(sig, (k, k), mode="reflect" if len(sig) > k else "edge")
    base = np.array([np.median(pad[i:i + 2 * k + 1]) for i in range(len(sig))])
    mad = np.array([np.median(np.abs(pad[i:i + 2 * k + 1] - base[i])) for i in range(len(sig))])
    # floor the spread so near-constant stretches (quiet chat, stream edges) can't explode the score
    floor = max(1.0, float(np.median(mad)))
    z = (sig - base) / (1.4826 * np.maximum(mad, floor))
    smooth = np.convolve(z, np.ones(3) / 3, mode="same")
    order = np.argsort(-smooth)
    picked: list[dict] = []
    for i in order:
        if smooth[i] < 3.0 or len(picked) >= top:
            break
        t = float(i) * bin_s
        if any(abs(t - p["peak"]) < min_gap for p in picked):
            continue
        picked.append({"peak": round(t, 1), "start": round(max(0.0, t - pre), 1), "end": round(t + post, 1),
                       "score": round(float(smooth[i]), 2), "why": why})
    return picked


def chat_spikes(path: Path, top: int = 10, bin_s: float = 5, pre: float = 35, post: float = 15,
                delay: float = 6) -> list[dict]:
    """Chat reacts a few seconds AFTER the moment, so windows reach back further than forward."""
    msgs = load_chat(path)
    if not msgs:
        return []
    end = max(t for t, _ in msgs)
    n = int(end // bin_s) + 1
    sig = np.zeros(n)
    for t, m in msgs:
        sig[int(t // bin_s)] += 1 + 1.5 * len(HYPE.findall(m or ""))
    wins = _windows_from_signal(sig, bin_s, top, pre, post, min_gap=60, why="chat spike")
    for w in wins:                                     # shift for chat reaction delay
        w["peak"] = round(max(0.0, w["peak"] - delay), 1)
        w["start"] = round(max(0.0, w["start"] - delay), 1)
        w["end"] = round(max(w["start"] + 10, w["end"] - delay), 1)
        lo, hi = w["peak"] - 5, w["peak"] + delay + 10
        sample = [m for t, m in msgs if lo <= t <= hi][:8]
        w["chat_sample"] = sample
    return wins


def audio_spikes(video: Path, top: int = 10, bin_s: float = 1.0, pre: float = 20, post: float = 10) -> list[dict]:
    """Loud moments (explosions, screaming streamer) from the audio track."""
    sr = 8000
    proc = subprocess.run([tool("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                           "-ar", str(sr), "-f", "s16le", "-"], capture_output=True)
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768
    hop = int(sr * bin_s)
    if len(pcm) < hop * 5:
        return []
    frames = pcm[: len(pcm) // hop * hop].reshape(-1, hop)
    rms = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6)
    return _windows_from_signal(rms, bin_s, top, pre, post, min_gap=40, why="audio spike")


def loud_moments(video: Path, threshold_db: float = 6.0) -> list[float]:
    """Short punchy peaks inside an already-cut clip (used for shake/zoom effects)."""
    sr = 8000
    proc = subprocess.run([tool("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                           "-ar", str(sr), "-f", "s16le", "-"], capture_output=True)
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768
    hop = sr // 10
    if len(pcm) < hop * 10:
        return []
    frames = pcm[: len(pcm) // hop * hop].reshape(-1, hop)
    rms = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6)
    med = np.median(rms)
    hits, last = [], -10.0
    for i, v in enumerate(rms):
        t = i / 10
        if v > med + threshold_db and v == rms[max(0, i - 5): i + 6].max() and t - last > 2.5:
            hits.append(round(t, 1))
            last = t
    return hits[:8]


def heatmap_spikes(info_json: Path, top: int = 5, pre: float = 15, post: float = 25) -> list[dict]:
    """YouTube 'Most replayed' graph (yt-dlp saves it as 'heatmap')."""
    info = json.loads(Path(info_json).read_text(encoding="utf-8"))
    hm = info.get("heatmap") or []
    if not hm:
        return []
    hm = sorted(hm, key=lambda h: -h["value"])
    out: list[dict] = []
    for h in hm:
        t = (h["start_time"] + h["end_time"]) / 2
        if any(abs(t - o["peak"]) < 45 for o in out):
            continue
        out.append({"peak": round(t, 1), "start": round(max(0, t - pre), 1), "end": round(t + post, 1),
                    "score": round(h["value"], 3), "why": "most replayed"})
        if len(out) >= top:
            break
    return out
