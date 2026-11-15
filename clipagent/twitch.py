"""Twitch sources: top clips (Helix API), VOD segments, VOD chat.

Needs TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env (free app at dev.twitch.tv/console).
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
from pathlib import Path

import requests

from .common import (DATA, clip_dir, config, creator, is_blocked, known_source_ids, new_id, probe,
                     read_json, run, save_clip_meta, tool, write_json)

HELIX = "https://api.twitch.tv/helix"
TOKEN_FILE = DATA / "twitch_token.json"


def _token() -> str:
    cid, secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    if not cid or not secret:
        raise SystemExit("Missing TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET in .env - run the setup guide.")
    cached = read_json(TOKEN_FILE, {})
    if cached.get("token") and cached.get("expires", 0) > time.time() + 60 and cached.get("cid") == cid:
        return cached["token"]
    r = requests.post("https://id.twitch.tv/oauth2/token", data={
        "client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}, timeout=20)
    r.raise_for_status()
    j = r.json()
    write_json(TOKEN_FILE, {"token": j["access_token"], "expires": time.time() + j["expires_in"], "cid": cid})
    return j["access_token"]


def helix(path: str, **params) -> dict:
    r = requests.get(f"{HELIX}/{path}", params=params, timeout=20, headers={
        "Client-Id": os.environ["TWITCH_CLIENT_ID"], "Authorization": f"Bearer {_token()}"})
    if r.status_code == 401:
        TOKEN_FILE.unlink(missing_ok=True)
        r = requests.get(f"{HELIX}/{path}", params=params, timeout=20, headers={
            "Client-Id": os.environ["TWITCH_CLIENT_ID"], "Authorization": f"Bearer {_token()}"})
    r.raise_for_status()
    return r.json()


def game_id(name: str | None = None) -> str:
    name = name or config()["game_name"]
    if not name:
        raise SystemExit("No game set. Run `preset gta6` (or another preset) or `config game_name=\"<Twitch category>\"`.")
    data = helix("games", name=name)["data"]
    if not data:
        hits = helix("search/categories", query=name, first=5)["data"]
        if not hits:
            raise SystemExit(f"Twitch category not found: {name}")
        return hits[0]["id"]
    return data[0]["id"]


def user(login: str) -> dict | None:
    data = helix("users", login=login.lstrip("@").lower())["data"]
    return data[0] if data else None


def top_clips(hours: int = 24, limit: int = 20, broadcaster: str | None = None,
              min_views: int = 0, language: str | None = None) -> list[dict]:
    """Most-viewed clips in the last N hours, for the game or for one streamer."""
    started = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"first": 100, "started_at": started}
    if broadcaster:
        u = user(broadcaster)
        if not u:
            raise SystemExit(f"Twitch user not found: {broadcaster}")
        params["broadcaster_id"] = u["id"]
    else:
        params["game_id"] = game_id()
    clips, cursor = [], None
    while len(clips) < limit * 3:
        if cursor:
            params["after"] = cursor
        j = helix("clips", **params)
        clips += j["data"]
        cursor = j.get("pagination", {}).get("cursor")
        if not cursor:
            break
    gid = None if broadcaster else params.get("game_id")
    seen = known_source_ids()
    out = []
    for c in sorted(clips, key=lambda c: -c["view_count"]):
        cr = creator(c["broadcaster_name"], "twitch", c["broadcaster_name"])
        if c["view_count"] < min_views or is_blocked(cr) or f"twitch_clip:{c['id']}" in seen:
            continue
        if language and c.get("language") != language:
            continue
        if gid and c.get("game_id") != gid:
            continue
        out.append({"id": c["id"], "title": c["title"], "streamer": c["broadcaster_name"],
                    "clipped_by": c["creator_name"], "views": c["view_count"], "seconds": c["duration"],
                    "url": c["url"], "created_at": c["created_at"], "language": c.get("language")})
        if len(out) >= limit:
            break
    return out


def download_clip(clip_url_or_id: str) -> dict:
    """Download one Twitch clip into the library with full credit metadata."""
    cid = clip_url_or_id.rstrip("/").split("/")[-1].split("?")[0]
    info = helix("clips", id=cid)["data"]
    if not info:
        raise SystemExit(f"clip not found: {cid}")
    c = info[0]
    cr = creator(c["broadcaster_name"], "twitch", c["broadcaster_name"])
    if is_blocked(cr):
        raise SystemExit(f"{cr['handle']} is on the blocklist (asked us not to use their clips).")
    clip_id = new_id("tw")
    d = clip_dir(clip_id)
    d.mkdir(parents=True, exist_ok=True)
    run([tool("yt-dlp") or "yt-dlp", "-q", "--no-warnings", "-f", "best", "-o", str(d / "source.%(ext)s"), c["url"]])
    src = next(d.glob("source.*"))
    meta = {
        "id": clip_id, "source": "twitch_clip", "source_id": f"twitch_clip:{c['id']}",
        "title": c["title"], "url": c["url"], "file": src.name,
        "creator": cr, "clipped_by": c["creator_name"], "views": c["view_count"],
        "language": c.get("language"), "created_at": c["created_at"],
        **probe(src),
    }
    return save_clip_meta(meta)


# ---------------------------------------------------------------- VODs + chat

def vod_info(vod_url_or_id: str) -> dict:
    vid = re.sub(r"\D", "", vod_url_or_id.rstrip("/").split("/")[-1])
    data = helix("videos", id=vid)["data"]
    if not data:
        raise SystemExit(f"VOD not found: {vid}")
    return data[0]


def latest_vods(streamer: str, n: int = 5) -> list[dict]:
    u = user(streamer)
    if not u:
        raise SystemExit(f"Twitch user not found: {streamer}")
    vids = helix("videos", user_id=u["id"], type="archive", first=n)["data"]
    return [{"id": v["id"], "title": v["title"], "duration": v["duration"], "created_at": v["created_at"],
             "url": v["url"], "views": v["view_count"]} for v in vids]


TDL_REPO = "https://api.github.com/repos/lay295/TwitchDownloader/releases/latest"


def ensure_twitchdownloader() -> str:
    """Path to TwitchDownloaderCLI (github.com/lay295/TwitchDownloader, MIT). Downloads it into ./bin on first use."""
    found = tool("TwitchDownloaderCLI")
    if found:
        return found
    import io
    import platform
    import zipfile
    from .common import ROOT
    system, machine = platform.system(), platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    suffix = {"Windows": "Windows-x64", "Darwin": "MacOSArm64" if arm else "MacOS-x64",
              "Linux": "LinuxArm64" if arm else "Linux-x64"}.get(system)
    if not suffix:
        raise SystemExit("Install TwitchDownloaderCLI manually from github.com/lay295/TwitchDownloader/releases into ./bin")
    rel = requests.get(TDL_REPO, timeout=30).json()
    asset = next((a for a in rel.get("assets", []) if a["name"].startswith("TwitchDownloaderCLI")
                  and a["name"].endswith(f"-{suffix}.zip")), None)
    if not asset:
        raise SystemExit("Couldn't find a TwitchDownloaderCLI build for this system; install it into ./bin manually.")
    print(f"downloading {asset['name']} ...", file=__import__("sys").stderr, flush=True)
    data = requests.get(asset["browser_download_url"], timeout=600).content
    bindir = ROOT / "bin"
    bindir.mkdir(exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if name.startswith("TwitchDownloaderCLI") or name.upper().startswith("COPYRIGHT"):
                z.extract(name, bindir)
    exe = bindir / ("TwitchDownloaderCLI.exe" if system == "Windows" else "TwitchDownloaderCLI")
    if system != "Windows":
        exe.chmod(0o755)
    return str(exe)


def download_chat(vod_url_or_id: str, out: Path) -> Path:
    """Chat replay JSON via TwitchDownloaderCLI (auto-installed into ./bin on first use)."""
    v = vod_info(vod_url_or_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    run([ensure_twitchdownloader(), "chatdownload", "--id", v["id"], "-o", out, "--collision", "Overwrite"])
    return out


def download_vod_segment(vod_url_or_id: str, start: float, end: float, title: str | None = None) -> dict:
    v = vod_info(vod_url_or_id)
    cr = creator(v["user_name"], "twitch", v["user_login"])
    if is_blocked(cr):
        raise SystemExit(f"{cr['handle']} is on the blocklist.")
    clip_id = new_id("tv")
    d = clip_dir(clip_id)
    d.mkdir(parents=True, exist_ok=True)
    run([tool("yt-dlp") or "yt-dlp", "-q", "--no-warnings", "-f", "best", "--download-sections",
         f"*{start:.1f}-{end:.1f}", "--force-keyframes-at-cuts", "-o", str(d / "source.%(ext)s"), v["url"]])
    src = next(d.glob("source.*"))
    meta = {
        "id": clip_id, "source": "twitch_vod", "source_id": f"twitch_vod:{v['id']}:{int(start)}",
        "title": title or v["title"], "url": f"{v['url']}?t={int(start)}s", "file": src.name,
        "creator": cr, "vod_start": start, "vod_end": end, **probe(src),
    }
    return save_clip_meta(meta)
