"""Creator asked us to stop -> blocklist them and list every post that used their footage."""
from __future__ import annotations

import shutil

from .common import OUTPUT, add_block, all_clips, clip_dir, ledger, upsert_post


def takedown(handle: str, platform: str | None = None, reason: str = "creator request",
             delete_files: bool = False) -> dict:
    h = handle.lstrip("@").lower()
    add_block(h, platform, reason)
    hit = lambda c: c and (c.get("handle", "").lower() == h or c.get("name", "").lower() == h) and \
        (platform in (None, "", c.get("platform")))  # noqa: E731
    posts = []
    for e in ledger():
        if any(hit(c) for c in e.get("creators", [])):
            e["status"] = "takedown"
            e["takedown_reason"] = reason
            upsert_post(e)
            posts.append({"post_id": e["post_id"], "buffer": e.get("buffer"), "scheduled_for": e.get("scheduled_for"),
                          "published_urls": e.get("published_urls", [])})
            if delete_files:
                shutil.rmtree(OUTPUT / e["post_id"], ignore_errors=True)
    clips = [m["id"] for m in all_clips() if hit(m.get("creator"))]
    if delete_files:
        for cid in clips:
            shutil.rmtree(clip_dir(cid), ignore_errors=True)
    return {"blocked": h, "posts_to_remove": posts, "library_clips": clips,
            "todo": "Delete these posts from Buffer's queue and from each platform where already published."}
