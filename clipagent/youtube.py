"""YouTube source (yt-dlp): search, info + 'most replayed' heatmap, section downloads with credit."""
from __future__ import annotations

import json
from pathlib import Path

from .common import (DATA, clip_dir, creator, is_blocked, known_source_ids, new_id, probe, run,
                     save_clip_meta, tool)

YTDLP = lambda: tool("yt-dlp") or "yt-dlp"  # noqa: E731


def search(query: str, n: int = 10, sort_recent: bool = False) -> list[dict]:
    import urllib.parse
    if sort_recent:   # results page sorted by upload date (ytsearchdate was removed from yt-dlp)
        src = "https://www.youtube.com/results?sp=CAI%253D&search_query=" + urllib.parse.quote_plus(query)
    else:
        src = f"ytsearch{n}:{query}"
    res = run([YTDLP(), "--flat-playlist", "-J", "--no-warnings", "--playlist-end", str(n), src], quiet=True)
    j = json.loads(res.stdout)
    seen = known_source_ids()
    out = []
    for e in j.get("entries", []):
        cr = creator(e.get("channel") or e.get("uploader") or "?", "youtube",
                     (e.get("uploader_id") or e.get("channel") or "?").lstrip("@"))
        out.append({"id": e["id"], "title": e.get("title"), "channel": cr["name"], "handle": cr["handle"],
                    "views": e.get("view_count"), "seconds": e.get("duration"),
                    "url": f"https://www.youtube.com/watch?v={e['id']}",
                    "blocked": is_blocked(cr), "already_used": any(s.startswith(f"youtube:{e['id']}") for s in seen if s)})
    return out


def info(url: str) -> Path:
    """Save full metadata (incl. heatmap + channel handle) to data/yt/<id>.info.json."""
    out_dir = DATA / "yt"
    out_dir.mkdir(exist_ok=True)
    res = run([YTDLP(), "-J", "--no-warnings", url], quiet=True)
    j = json.loads(res.stdout)
    p = out_dir / f"{j['id']}.info.json"
    p.write_text(json.dumps(j), encoding="utf-8")
    return p


def _creator_from_info(j: dict) -> dict:
    handle = (j.get("uploader_id") or "").lstrip("@") or j.get("channel") or "unknown"
    return creator(j.get("channel") or j.get("uploader") or handle, "youtube", handle,
                   j.get("uploader_url") or j.get("channel_url"))


def download(url: str, start: float | None = None, end: float | None = None, title: str | None = None) -> dict:
    ip = info(url)
    j = json.loads(ip.read_text(encoding="utf-8"))
    cr = _creator_from_info(j)
    if is_blocked(cr):
        raise SystemExit(f"{cr['handle']} is on the blocklist.")
    clip_id = new_id("yt")
    d = clip_dir(clip_id)
    d.mkdir(parents=True, exist_ok=True)
    cmd = [YTDLP(), "-q", "--no-warnings", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
           "--merge-output-format", "mp4", "-o", str(d / "source.%(ext)s")]
    if start is not None and end is not None:
        cmd += ["--download-sections", f"*{start:.1f}-{end:.1f}", "--force-keyframes-at-cuts"]
    run(cmd + [url])
    src = next(p for p in d.glob("source.*") if p.suffix in (".mp4", ".mkv", ".webm"))
    meta = {
        "id": clip_id, "source": "youtube",
        "source_id": f"youtube:{j['id']}:{int(start or 0)}",
        "title": title or j.get("title"),
        "url": f"https://youtu.be/{j['id']}" + (f"?t={int(start)}" if start else ""),
        "file": src.name, "creator": cr, "yt_start": start, "yt_end": end,
        "info_json": str(ip), **probe(src),
    }
    return save_clip_meta(meta)
