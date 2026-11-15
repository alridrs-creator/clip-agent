"""Setup check: tools, keys, PC specs. Prints a checklist the agent walks the user through."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess

from .common import BRAND, MUSIC, ROOT, config, tool


def _gpu() -> dict:
    smi = shutil.which("nvidia-smi")
    if smi:
        r = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            name, mem = r.stdout.strip().splitlines()[0].split(",")
            return {"name": name.strip(), "vram_gb": round(int(mem) / 1024, 1), "nvidia": True}
    return {"name": "none detected (or non-NVIDIA)", "vram_gb": 0, "nvidia": False}


def _ram_gb() -> float:
    try:
        import ctypes

        class M(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("m", ctypes.c_ulong), ("t", ctypes.c_ulonglong),
                        ("a", ctypes.c_ulonglong), ("x", ctypes.c_ulonglong * 5)]
        m = M()
        m.l = ctypes.sizeof(M)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return round(m.t / 2**30, 1)
    except Exception:
        try:
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
        except Exception:
            return 0.0


def check() -> dict:
    gpu, ram = _gpu(), _ram_gb()
    cpu = platform.processor() or platform.machine()
    cores = os.cpu_count() or 0
    if gpu["nvidia"] and gpu["vram_gb"] >= 6 and ram >= 16:
        verdict = "great: GPU encoding + fast GPU subtitles"
    elif ram >= 8 and cores >= 4:
        verdict = "ok: works, CPU rendering ~1-3 min per short, subtitles slower"
    else:
        verdict = "weak: expect very slow renders - keep clips under 30s, use whisper_model=tiny"
    tools = {t: bool(tool(t)) for t in ("ffmpeg", "ffprobe", "yt-dlp", "streamlink")}
    tools["TwitchDownloaderCLI (optional)"] = bool(tool("TwitchDownloaderCLI"))
    from .tts import kokoro_available
    tools["kokoro human voices (models/ files)"] = kokoro_available()
    keys = {
        "TWITCH_CLIENT_ID + SECRET": bool(os.environ.get("TWITCH_CLIENT_ID") and os.environ.get("TWITCH_CLIENT_SECRET")),
        "BUFFER_API_KEY": bool(os.environ.get("BUFFER_API_KEY")),
        "GEMINI_API_KEY (optional)": bool(os.environ.get("GEMINI_API_KEY")),
    }
    cfg = config()
    return {
        "pc": {"os": platform.platform(), "cpu": cpu, "cores": cores, "ram_gb": ram, "gpu": gpu, "verdict": verdict},
        "tools": tools,
        "keys": keys,
        "channel": {"name": cfg.get("channel_name") or None, "handle": cfg.get("channel_handle") or None,
                    "game": cfg.get("game_name") or None, "preset": cfg.get("preset") or None},
        "assets": {
            "fonts_in_brand/fonts": [p.name for p in (BRAND / "fonts").glob("*.[ot]tf")],
            "music_tracks": len([p for p in MUSIC.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac")]),
            "music_credits_json": (MUSIC / "credits.json").exists(),
            "env_file": (ROOT / ".env").exists(),
        },
    }
