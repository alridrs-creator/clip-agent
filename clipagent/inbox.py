"""User's own recordings: anything dropped in inbox/ becomes a library clip (moved, not copied)."""
from __future__ import annotations

import shutil

from .common import INBOX, clip_dir, config, creator, new_id, probe, save_clip_meta, slug

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".flv"}


def import_inbox(owner_handle: str | None = None, platform: str = "youtube") -> list[dict]:
    cfg = config()
    handle = owner_handle or cfg.get("channel_handle") or cfg.get("channel_name") or "me"
    out = []
    for f in sorted(INBOX.iterdir()):
        if f.suffix.lower() not in VIDEO_EXT:
            continue
        clip_id = new_id("up")
        d = clip_dir(clip_id)
        d.mkdir(parents=True, exist_ok=True)
        dest = d / f"source{f.suffix.lower()}"
        shutil.move(str(f), dest)
        meta = {
            "id": clip_id, "source": "upload", "source_id": f"upload:{slug(f.stem)}:{dest.stat().st_size}",
            "title": f.stem, "url": "", "file": dest.name, "original_name": f.name,
            "creator": creator(handle, platform, handle), "own_footage": True, **probe(dest),
        }
        out.append(save_clip_meta(meta))
    return out
