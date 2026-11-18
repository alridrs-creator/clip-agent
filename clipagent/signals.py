"""Multi-signal highlight detection (optional, OFF by default): chat + streamer audio energy + on-screen
game events, combined per second with weights from the active preset ("signals" block).

Signals (all per second of video):
  chat   messages/s (hype words count extra), shifted back by the chat delay, robust z-score
  audio  loudness (dB RMS) jump vs a rolling 3-min median: shouting, screaming, laughing
  event  "grey screen" game events: WASTED / BUSTED style full-screen desaturation, detected as a sudden drop
         of colour saturation that lasts >= 1.5 s (game-agnostic; GTA 6 HUD text is unknown until launch)

  speech hype phrases the streamer says ("no way", "let's go", "oh my god", "да ладно" ...), from the Whisper
         transcript (optional, OFF by default: config signals_speech=true or --signals ...,speech)
  screen GTA 6 full-screen text (death / arrest / mission result) by template matching, see gta6screen.py
         (optional, OFF by default: config signals_gta6_screen=true or --signals ...,screen)

Used by:  `spikes multi <video> [--chat file]` (VOD or a saved stream recording).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from .common import PRESETS, config, read_json, tool

import re

# chat that spikes for non-moments: subs, gifts, raids, giveaways, hello/goodbye spam
NOISE = re.compile(r"(?i)\b(subscribed|gifted|gift(ing)? subs?|raid(ing|ers)?|giveaway|!enter|w stream|good ?night|"
                   r"\bgn\b|bye+|welcome back|hello|hi chat|first|cheer\d+|bits|prime)\b")


def noise_share(sample: list[str]) -> float:
    """Share of chat lines in a window that are sub/raid/giveaway/greeting noise."""
    return sum(1 for m in sample if NOISE.search(m or "")) / max(1, len(sample))


DEFAULT_WEIGHTS = {"chat": 1.0, "audio": 0.45, "event": 3.0, "speech": 1.0, "screen": 4.0, "threshold": 4.0, "chat_delay": 6.0,
                   "pre": 22.0, "post": 14.0, "min_gap": 60.0,
                   "max_noise": 0.4, "skip_start": 600, "skip_end": 300}


def weights() -> dict:
    preset = config().get("preset") or ""
    w = read_json(PRESETS / f"{preset}.json", {}).get("signals", {}) if preset else {}
    return {**DEFAULT_WEIGHTS, **w}


# ---------------------------------------------------------------- features from media

def audio_db(media: Path, start: float = 0.0, dur: float | None = None, sr: int = 8000) -> np.ndarray:
    """Loudness per second (dB RMS)."""
    cmd = [tool("ffmpeg") or "ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-i", str(media)]
    if dur:
        cmd += ["-t", f"{dur:.2f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    pcm = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, dtype=np.int16).astype(np.float32) / 32768
    n = len(pcm) // sr
    if n == 0:
        return np.zeros(0)
    fr = pcm[: n * sr].reshape(n, sr)
    return 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-6)


def saturation(media: Path, start: float = 0.0, dur: float | None = None) -> np.ndarray:
    """Mean colour saturation per second (0..1) from 1 fps thumbnails."""
    w, h = 32, 18
    cmd = [tool("ffmpeg") or "ffmpeg", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-i", str(media)]
    if dur:
        cmd += ["-t", f"{dur:.2f}"]
    cmd += ["-an", "-vf", f"fps=1,scale={w}:{h}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    raw = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, dtype=np.uint8)
    n = len(raw) // (w * h * 3)
    if n == 0:
        return np.zeros(0)
    px = raw[: n * w * h * 3].reshape(n, w * h, 3).astype(np.float32)
    mx, mn = px.max(axis=2), px.min(axis=2)
    sat = np.where(mx > 20, (mx - mn) / np.maximum(mx, 1), 0)
    return sat.mean(axis=1)


# ---------------------------------------------------------------- per-signal scores

def _robust_z(x: np.ndarray, win: int, floor: float = 1e-3) -> np.ndarray:
    if len(x) == 0:
        return x
    k = max(5, win // 2)
    pad = np.pad(x, (k, k), mode="edge")
    out = np.zeros(len(x))
    for i in range(len(x)):
        w_ = pad[i:i + 2 * k + 1]
        med = np.median(w_)
        mad = max(np.median(np.abs(w_ - med)), floor)
        out[i] = (x[i] - med) / (1.4826 * mad)
    return out


def _robust_z_fast(x: np.ndarray, win: int, floor: float = 0.3) -> np.ndarray:
    """Same idea as _robust_z but on 10 s blocks for long VODs (hours)."""
    if len(x) < 4 * win:
        return _robust_z(x, win, floor)
    blk = 10
    nb = len(x) // blk
    meds = np.array([np.median(x[i * blk:(i + 1) * blk]) for i in range(nb)])
    k = max(3, win // blk // 2)
    pad = np.pad(meds, (k, k), mode="edge")
    med_b = np.array([np.median(pad[i:i + 2 * k + 1]) for i in range(nb)])
    mad_b = np.array([max(np.median(np.abs(pad[i:i + 2 * k + 1] - med_b[i])), floor) for i in range(nb)])
    med = np.repeat(med_b, blk)
    mad = np.repeat(mad_b, blk)
    med = np.pad(med, (0, len(x) - len(med)), mode="edge")
    mad = np.pad(mad, (0, len(x) - len(mad)), mode="edge")
    return (x - med) / (1.4826 * mad)


def chat_per_second(msgs: list[tuple[float, str]], n: int, delay: float) -> np.ndarray:
    from .spikes import HYPE
    sig = np.zeros(n)
    for t, m in msgs:
        i = int(t - delay)               # chat reacts late: credit the moment it reacts to
        if 0 <= i < n:
            sig[i] += 1 + 1.5 * len(HYPE.findall(m or ""))
    return np.convolve(sig, np.ones(5) / 5, mode="same")   # 5 s smoothing (chat is bursty)


def audio_score(db: np.ndarray) -> np.ndarray:
    # loudness barely moves on a calm stretch: a 1.5 dB floor keeps normal talking from looking like a spike
    z = _robust_z(db, 180, floor=1.5) if len(db) < 720 else _robust_z_fast(db, 180, floor=1.5)
    return np.clip(np.convolve(z, np.ones(2) / 2, mode="same"), 0, 8)


def event_score(sat: np.ndarray, min_len: int = 2) -> np.ndarray:
    """1.0 at the start of a sudden, lasting desaturation (grey 'WASTED/BUSTED' style screen)."""
    out = np.zeros(len(sat))
    if len(sat) < 20:
        return out
    base = np.array([np.median(sat[max(0, i - 60):i]) if i > 5 else sat[:5].mean() for i in range(len(sat))])
    grey = (sat < 0.35 * np.maximum(base, 0.08)) & (base > 0.12)
    run = 0
    for i, g in enumerate(grey):
        run = run + 1 if g else 0
        if run == min_len:
            out[i - min_len + 1] = 1.0
    return out


# streamer's own words (Whisper transcript): hype phrases in English and Russian
SPEECH_HYPE = re.compile(
    r"(?i)\b(no+ way|let'?s go+|oh my god|oh my gosh|o+mg|what the (hell|heck|f\w*)|holy (shit|crap|moly)|"
    r"are you (serious|kidding)|you'?re kidding|i can'?t believe|bro what|what just happened|oh no+|"
    r"no no no|run+ run|get out|he'?s dead|i'?m dead|wtf|"
    r"да ладно|ничего себе|не может быть|охренеть|офигеть|обалдеть|ни хрена себе|погнали|жесть|"
    r"пипец|капец|что за|какого|беги|вау)\b")


def speech_hits(words: list[dict]) -> list[tuple[float, str]]:
    """(start second, phrase) for every hype phrase in a [{word, start, end}] transcript."""
    hits, i = [], 0
    while i < len(words):
        text = " ".join(w["word"] for w in words[i:i + 5]).strip(" .,!?")
        m = SPEECH_HYPE.match(text)
        if m:
            hits.append((float(words[i]["start"]), m.group(0).lower()))
            i += max(1, len(m.group(0).split()))     # a phrase counts once, words are not reused
        else:
            i += 1
    return hits


def speech_score(words: list[dict], n: int) -> np.ndarray:
    """Per second: hype phrases said by the streamer (1 per phrase, 3 s smoothing, capped at 4)."""
    sig = np.zeros(n)
    for t, _ in speech_hits(words):
        if 0 <= int(t) < n:
            sig[int(t)] += 1.0
    return np.clip(np.convolve(sig, np.ones(3), mode="same"), 0, 4)


# ---------------------------------------------------------------- combine

def combine(chat_z: np.ndarray | None, aud: np.ndarray | None, ev: np.ndarray | None, w: dict,
            extra: dict | None = None) -> np.ndarray:
    """Weighted sum per second. extra = optional {"speech": arr, "screen": arr} (plugins that are off pass None)."""
    parts = [(chat_z, "chat"), (aud, "audio"), (ev, "event")] + [(v, k) for k, v in (extra or {}).items()]
    n = max(len(x) for x, _ in parts if x is not None)
    tot = np.zeros(n)
    for x, k in parts:
        if x is not None and len(x):
            tot[: min(n, len(x))] += w[k] * np.clip(x[:n], 0, None)
    return tot


def peaks(score: np.ndarray, parts: dict, w: dict, top: int = 10, msgs: list | None = None,
          skip_edges: bool = False) -> list[dict]:
    order = np.argsort(-score)
    out: list[dict] = []
    n = len(score)
    for i in order:
        if score[i] < w["threshold"] or len(out) >= top:
            break
        if any(abs(i - o["peak"]) < w["min_gap"] for o in out):
            continue
        if skip_edges and n > 2 * (w["skip_start"] + w["skip_end"]) and (i < w["skip_start"] or i > n - w["skip_end"]):
            # stream intro / outro (full VODs only: a short saved recording has no intro to skip)
            continue
        if msgs:
            sample = [m for t, m in msgs if i - 2 <= t - w["chat_delay"] <= i + 12][:20]
            if noise_share(sample) > w["max_noise"]:
                continue
        why = {k: round(float(v[i]), 2) for k, v in parts.items() if v is not None and i < len(v) and v[i] > 0.5}
        out.append({"peak": float(i), "start": float(max(0, i - w["pre"])), "end": float(i + w["post"]),
                    "score": round(float(score[i]), 2), "why": "multi: " + ", ".join(f"{k} {v}" for k, v in why.items())})
    return out


def multi_spikes(video: Path, chat_file: Path | None = None, top: int = 10, use: str = "chat,audio,event") -> list[dict]:
    """Offline: rank moments in a recording / VOD by all signals.
    use: any of chat, audio, event, speech (Whisper transcript, slow), screen (GTA 6 templates)."""
    from .spikes import load_chat
    w = weights()
    use_s = set(use.split(","))
    aud = audio_score(audio_db(video)) if "audio" in use_s else None
    ev = event_score(saturation(video)) if "event" in use_s else None
    n = max([len(x) for x in (aud, ev) if x is not None] + [int(probe_seconds(video))])
    chat_z = None
    if chat_file and "chat" in use_s:
        msgs = load_chat(Path(chat_file))
        n = max(n, int(max(t for t, _ in msgs)) + 1)
        chat_z = _robust_z_fast(chat_per_second(msgs, n, w["chat_delay"]), 300)
    extra = {}
    if "speech" in use_s:
        from .transcribe import transcribe
        words = transcribe(video, Path(video).with_suffix(".words.json"))
        extra["speech"] = speech_score(words, n)
    if "screen" in use_s:
        from .gta6screen import screen_events, screen_score
        extra["screen"] = screen_score(screen_events(video), n)
    score = combine(chat_z, aud, ev, w, extra)
    return peaks(score, {"chat": chat_z, "audio": aud, "event": ev, **extra}, w, top,
                 msgs=load_chat(Path(chat_file)) if chat_file and "chat" in use_s else None, skip_edges=True)


def probe_seconds(video: Path) -> float:
    r = subprocess.run([tool("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                        "default=nw=1:nk=1", str(video)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def to_json(x) -> str:
    return json.dumps(x, indent=2)
