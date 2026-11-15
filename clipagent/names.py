"""Channel-name availability check across platforms (best effort - some sites hide this from scripts)."""
from __future__ import annotations

import os
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}


def _get(url: str):
    try:
        return requests.get(url, headers=UA, timeout=15, allow_redirects=True)
    except requests.RequestException:
        return None


def youtube(h: str) -> str:
    r = _get(f"https://www.youtube.com/@{h}")
    if r is None:
        return "unknown"
    return "taken" if r.status_code == 200 else ("free" if r.status_code == 404 else "unknown")


def twitch(h: str) -> str:
    if os.environ.get("TWITCH_CLIENT_ID"):
        try:
            from .twitch import user
            return "taken" if user(h) else "free*"   # * Twitch also reserves names of banned/deleted accounts
        except Exception:
            pass
    return "unknown"


def tiktok(h: str) -> str:
    r = _get(f"https://www.tiktok.com/@{h}")
    if r is None:
        return "unknown"
    if '"uniqueId":"' + h.lower() in r.text.lower() or f'"uniqueid":"{h.lower()}"' in r.text.lower():
        return "taken"
    if "couldn't find this account" in r.text.lower() or r.status_code == 404:
        return "free"
    return "unknown"


def instagram(h: str) -> str:
    r = _get(f"https://www.instagram.com/{h}/")
    if r is None:
        return "unknown"
    if r.status_code == 404:
        return "free"
    if re.search(rf'"username":"{re.escape(h)}"', r.text, re.I) or f"@{h.lower()}" in r.text.lower():
        return "taken"
    return "unknown"   # Instagram usually needs login - agent should check in the browser


def check(handle: str) -> dict:
    h = handle.lstrip("@")
    return {"handle": h, "youtube": youtube(h), "twitch": twitch(h), "tiktok": tiktok(h), "instagram": instagram(h)}
