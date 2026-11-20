"""Shared paths, config, clip metadata, ledger and blocklist."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "inbox"            # user drops raw gameplay here
LIBRARY = ROOT / "library"        # every fetched clip: library/<clip_id>/source.mp4 + meta.json
OUTPUT = ROOT / "output"          # finished posts: output/<post_id>/...
MUSIC = ROOT / "music"            # user-supplied no-copyright tracks + credits.json
BRAND = ROOT / "brand"            # logo, fonts, generated brand images
DATA = ROOT / "data"              # config, ledger, blocklist, state
for _d in (INBOX, LIBRARY, OUTPUT, MUSIC, BRAND, DATA, BRAND / "fonts"):
    _d.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA / "config.json"
LEDGER_FILE = DATA / "ledger.json"
BLOCKLIST_FILE = DATA / "blocklist.json"

DEFAULT_CONFIG = {
    "channel_name": "",
    "channel_handle": "",
    "preset": "",                 # presets/<name>.json applied with `preset <name>`
    "game_name": "",              # exact Twitch category name (from the preset)
    "game_short": "",             # short name for titles, e.g. "GTA 6"
    "spoiler_text": "SPOILERS AHEAD",
    "default_hashtags": ["#gaming", "#clips"],
    "language": "en",
    "encoder": "auto",            # auto | h264_nvenc | libx264
    "whisper_model": "auto",      # auto | tiny | base | small | medium | large-v3
    "font_file": "",              # path to a .ttf; empty = brand/fonts/*.ttf or Impact
    "caption_font_file": "",
    "subtitle_color": "&H0000FFFF",   # ASS BGR: yellow highlight
    "max_seconds": 59,
    "trim_bottom": 0.14,          # share of the source cut off the bottom (hides streamers' own burned-in captions)
    "credit_mode": "corner",      # corner (whole clip, small) | intro (first 3s, bigger) | both
    "spoiler_default": True,
    "music_volume": 0.12,
    "voice_male": "am_michael",
    "voice_female": "af_heart",
    "voice_fun": "af_nicole",
    "image_provider": "pollinations",  # pollinations (free, no key) | gemini
    "gemini_image_model": "gemini-2.5-flash-image",
    "buffer_media_url_prefix": "",     # public URL that mirrors output/ (needed for Buffer API video upload)
    "post_times": ["12:00", "18:00", "21:00"],
    "streamers": [],                   # Twitch logins to watch when no game category is set (streamer channels)
    "youtube_category_id": "20",       # YouTube category for uploads (20 = Gaming)
    "youtube_privacy": "public",       # public | unlisted | private for Composio YouTube uploads
}


def load_env() -> None:
    """Read KEY=VALUE lines from .env into os.environ (no extra dependency)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(read_json(CONFIG_FILE, {}))
    return cfg


PRESETS = ROOT / "presets"


def apply_preset(name: str) -> dict:
    p = PRESETS / f"{name}.json"
    if not p.exists():
        raise SystemExit(f"no preset {name!r}; available: {', '.join(x.stem for x in PRESETS.glob('*.json') if not x.stem.startswith('_'))}")
    data = read_json(p, {})
    keep = {k: v for k, v in data.items() if k in DEFAULT_CONFIG}
    return save_config(keep)


def save_config(updates: dict) -> dict:
    cfg = read_json(CONFIG_FILE, {})
    cfg.update(updates)
    write_json(CONFIG_FILE, cfg)
    return config()


def tool(name: str) -> str | None:
    """Find an executable: venv Scripts first, then PATH, then ./bin."""
    venv_scripts = Path(sys.executable).parent
    for cand in (venv_scripts / name, venv_scripts / f"{name}.exe", ROOT / "bin" / name, ROOT / "bin" / f"{name}.exe"):
        if cand.exists():
            return str(cand)
    return shutil.which(name)


def run(cmd: list, cwd: Path | None = None, quiet: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    if not quiet:
        print("$", " ".join(str(c) for c in cmd)[:400], file=sys.stderr, flush=True)
    res = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if check and res.returncode != 0:
        raise RuntimeError(f"command failed ({res.returncode}): {cmd[0]}\n{res.stderr[-3000:]}")
    return res


def probe(path: Path) -> dict:
    res = run([tool("ffprobe") or "ffprobe", "-v", "error", "-print_format", "json", "-show_format",
               "-show_streams", path], quiet=True)
    info = json.loads(res.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fps = 30.0
    if v and v.get("avg_frame_rate", "0/0") != "0/0":
        n, d = v["avg_frame_rate"].split("/")
        fps = float(n) / float(d) if float(d) else 30.0
    return {
        "duration": float(info["format"].get("duration", 0)),
        "width": int(v["width"]) if v else 0,
        "height": int(v["height"]) if v else 0,
        "fps": fps,
        "has_audio": a is not None,
    }


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s[:n] or "clip"


def new_id(prefix: str) -> str:
    return f"{prefix}{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------- creators & credits

PLATFORM_URL = {
    "twitch": "https://twitch.tv/{h}",
    "youtube": "https://youtube.com/@{h}",
    "tiktok": "https://tiktok.com/@{h}",
    "instagram": "https://instagram.com/{h}",
    "kick": "https://kick.com/{h}",
}


def creator(name: str, platform: str, handle: str | None = None, url: str | None = None) -> dict:
    handle = (handle or name).lstrip("@")
    return {
        "name": name,
        "platform": platform,
        "handle": handle,
        "url": url or PLATFORM_URL.get(platform, "{h}").format(h=handle),
    }


def credit_line(c: dict, short: bool = False) -> str:
    """On-screen / caption credit, e.g. 'twitch.tv/xQc' or '@MrBeast on YouTube'."""
    p, h = c["platform"], c["handle"]
    if p == "twitch":
        return f"twitch.tv/{h}"
    if p == "kick":
        return f"kick.com/{h}"
    if p in ("youtube", "tiktok", "instagram"):
        return f"@{h}" if short else f"@{h} on {p.capitalize() if p != 'youtube' else 'YouTube'}"
    return h


# ---------------------------------------------------------------- clip library

def clip_dir(clip_id: str) -> Path:
    return LIBRARY / clip_id


def save_clip_meta(meta: dict) -> dict:
    meta.setdefault("added", time.strftime("%Y-%m-%dT%H:%M:%S"))
    write_json(clip_dir(meta["id"]) / "meta.json", meta)
    return meta


def load_clip(clip_id: str) -> dict:
    meta = read_json(clip_dir(clip_id) / "meta.json", None)
    if meta is None:
        raise SystemExit(f"unknown clip id: {clip_id}")
    return meta


def all_clips() -> list[dict]:
    out = []
    for d in sorted(LIBRARY.iterdir()):
        m = read_json(d / "meta.json", None)
        if m:
            out.append(m)
    return out


def known_source_ids() -> set:
    return {m.get("source_id") for m in all_clips() if m.get("source_id")}


# ---------------------------------------------------------------- blocklist (creators who asked us to stop)

def blocklist() -> list[dict]:
    return read_json(BLOCKLIST_FILE, [])


def is_blocked(c: dict) -> bool:
    h = c.get("handle", "").lower()
    n = c.get("name", "").lower()
    for b in blocklist():
        if b["handle"].lower() in (h, n) and b.get("platform") in (None, "", c.get("platform")):
            return True
    return False


def add_block(handle: str, platform: str | None, reason: str) -> None:
    bl = blocklist()
    if not any(b["handle"].lower() == handle.lower() and b.get("platform") == platform for b in bl):
        bl.append({"handle": handle.lstrip("@"), "platform": platform, "reason": reason,
                   "since": time.strftime("%Y-%m-%d")})
        write_json(BLOCKLIST_FILE, bl)


# ---------------------------------------------------------------- ledger of posts

def ledger() -> list[dict]:
    return read_json(LEDGER_FILE, [])


def upsert_post(entry: dict) -> None:
    led = ledger()
    for i, e in enumerate(led):
        if e["post_id"] == entry["post_id"]:
            led[i] = {**e, **entry}
            break
    else:
        led.append(entry)
    write_json(LEDGER_FILE, led)


def get_post(post_id: str) -> dict:
    for e in ledger():
        if e["post_id"] == post_id:
            return e
    raise SystemExit(f"unknown post id: {post_id}")
