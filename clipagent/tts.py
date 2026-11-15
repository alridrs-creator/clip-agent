"""Free voiceover. Two engines:

- kokoro (default when models/ has the files): open-source Kokoro-82M, runs locally, sounds much more human.
  Voices like af_heart, af_bella (female, US), am_michael, am_fenrir, am_puck (male, US), bm_george (male, UK).
  Model files (~350 MB, Apache-2.0): https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0
- edge: Microsoft Edge online voices (edge-tts). No download, more robotic.

Returns word timings for captions (Kokoro: from a quick Whisper pass over the clean voice track).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .common import ROOT, config

KOKORO_MODEL = ROOT / "models" / "kokoro-v1.0.onnx"
KOKORO_VOICES = ROOT / "models" / "voices-v1.0.bin"
_kokoro = None


def kokoro_available() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return KOKORO_MODEL.exists() and KOKORO_VOICES.exists()


def _engine_for(voice: str) -> str:
    if "Neural" in voice:                 # en-US-GuyNeural style names = edge
        return "edge"
    return "kokoro" if kokoro_available() else "edge"


def _kokoro_speak(text: str, voice: str, out_audio: Path, speed: float) -> None:
    global _kokoro
    import soundfile as sf
    from kokoro_onnx import Kokoro
    if _kokoro is None:
        _kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
    lang = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "i": "it", "p": "pt-br", "h": "hi",
            "j": "ja", "z": "cmn"}.get(voice[0], "en-us")
    samples, sr = _kokoro.create(text, voice=voice, speed=speed, lang=lang)
    sf.write(str(out_audio), samples, sr)


async def _edge_speak(text: str, voice: str, out_audio: Path, rate: str) -> list[dict]:
    import edge_tts
    words = []
    comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    with open(out_audio, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                s = chunk["offset"] / 1e7
                words.append({"word": chunk["text"], "start": round(s, 2),
                              "end": round(s + chunk["duration"] / 1e7, 2)})
    return words


def voiceover(text: str, out_audio: Path, voice: str = "male", rate: str = "+8%", timings: bool = True):
    """voice: 'male' | 'female' | a voice name (af_heart, am_michael, en-US-GuyNeural, ...).
    Writes out_audio (.wav for kokoro, .mp3 for edge - the suffix is adjusted) and returns word timings."""
    cfg = config()
    v = {"male": cfg["voice_male"], "female": cfg["voice_female"], "fun": cfg.get("voice_fun", "af_nicole")}.get(voice, voice)
    eng = _engine_for(v)
    if eng == "kokoro":
        out_audio = out_audio.with_suffix(".wav")
        speed = 1.0 + int(rate.strip("%+") or 0) / 100 if rate else 1.0
        _kokoro_speak(text, v, out_audio, speed)
        words = []
        if timings:
            from .transcribe import transcribe
            words = transcribe(out_audio, out_audio.with_suffix(".words.json"))
    else:
        if "Neural" not in v:
            v = "en-US-GuyNeural" if voice != "female" else "en-US-JennyNeural"
        out_audio = out_audio.with_suffix(".mp3")
        words = asyncio.run(_edge_speak(text, v, out_audio, rate))
        out_audio.with_suffix(".words.json").write_text(json.dumps(words), encoding="utf-8")
    return words, out_audio


def list_voices(lang_prefix: str = "en") -> list[dict]:
    out = []
    if kokoro_available():
        from kokoro_onnx import Kokoro
        k = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
        accent = {"a": "US", "b": "UK"}
        for name in sorted(k.get_voices()):
            if name[0] in accent:
                out.append({"name": name, "engine": "kokoro (human-like, local)",
                            "gender": "female" if name[1] == "f" else "male", "accent": accent[name[0]]})
    import edge_tts
    vs = asyncio.run(edge_tts.list_voices())
    out += [{"name": v["ShortName"], "engine": "edge (online, more robotic)", "gender": v["Gender"].lower()}
            for v in vs if v["ShortName"].startswith(lang_prefix)]
    return out
