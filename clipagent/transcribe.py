"""Word-level transcription with faster-whisper (GPU if available, else CPU)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .common import config


def _add_cuda_dll_dirs() -> None:
    """pip's nvidia-cublas/cudnn wheels put DLLs in site-packages/nvidia/*/bin - make them findable on Windows."""
    if os.name != "nt":
        return
    for sp in sys.path:
        base = Path(sp) / "nvidia"
        if base.is_dir():
            for b in base.glob("*/bin"):
                os.add_dll_directory(str(b))
                os.environ["PATH"] = str(b) + os.pathsep + os.environ["PATH"]


def _pick_model() -> tuple[str, str, str]:
    choice = config()["whisper_model"]
    try:
        import ctranslate2
        has_gpu = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        has_gpu = False
    if choice == "auto":
        choice = "medium" if has_gpu else "small"
    return choice, ("cuda" if has_gpu else "cpu"), ("float16" if has_gpu else "int8")


_MODEL = None


def _decode(media: Path):
    import subprocess

    import numpy as np

    from .common import tool
    raw = subprocess.run([tool("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(media: Path, out_json: Path, language: str | None = None) -> list[dict]:
    """Returns [{word, start, end}] and caches to out_json."""
    if out_json.exists():
        return json.loads(out_json.read_text(encoding="utf-8"))
    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel
    name, device, ctype = _pick_model()
    lang = language or config().get("language") or None
    global _MODEL
    audio = _decode(media)   # decode with ffmpeg ourselves (avoids PyAV version clashes)
    try:
        if _MODEL is None:
            _MODEL = WhisperModel(name, device=device, compute_type=ctype)
        segs, _ = _MODEL.transcribe(audio, language=lang, word_timestamps=True, vad_filter=True)
        segs = list(segs)
    except Exception as e:  # CUDA libs missing etc. -> CPU fallback
        if device == "cpu":
            raise
        print(f"GPU transcription failed ({str(e)[:120]}); falling back to CPU. "
              "Fix: pip install nvidia-cublas-cu12 nvidia-cudnn-cu12", file=sys.stderr, flush=True)
        _MODEL = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
        segs, _ = _MODEL.transcribe(audio, language=lang, word_timestamps=True, vad_filter=True)
        segs = list(segs)
    words = [{"word": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2)}
             for s in segs for w in (s.words or []) if w.word.strip()]
    out_json.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    return words


def sentences(words: list[dict], max_gap: float = 0.9) -> list[dict]:
    """Group words into sentences/thoughts: split on . ! ? or a pause longer than max_gap."""
    out, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > max_gap):
            out.append(cur)
            cur = []
        cur.append(w)
        if w["word"].rstrip().endswith((".", "!", "?")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"start": s[0]["start"], "end": s[-1]["end"], "text": " ".join(x["word"] for x in s)} for s in out]


def clip_transcript(clip_id: str) -> list[dict]:
    """Sentence-level transcript of a whole library clip (cached), for picking cuts that make sense."""
    from .common import clip_dir, load_clip
    m = load_clip(clip_id)
    d = clip_dir(clip_id)
    words = transcribe(d / m["file"], d / "words.json")
    return sentences(words)


def snap_to_sentences(sents: list[dict], start: float, end: float, slack: float = 4.0) -> tuple[float, float]:
    """Move start back to the beginning of the sentence it cuts into, end forward to the end of the sentence."""
    for s in sents:
        if s["start"] < start < s["end"] and start - s["start"] <= slack:
            start = s["start"] - 0.15
        if s["start"] < end < s["end"] and s["end"] - end <= slack:
            end = s["end"] + 0.3
    return max(0.0, start), end
