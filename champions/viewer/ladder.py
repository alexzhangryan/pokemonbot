"""The bot's rating and rank on the official ladder, for the viewer (D83).

Elo comes back in the battle protocol after every rated game and the trace
records it, but rank does not, and neither do GXE or the Glicko estimate.
Showdown publishes both on two public endpoints:

- `https://pokemonshowdown.com/users/<user>.json`: the user's rating per
  format (`elo`, `gxe`, `rpr`, `rprd`, `w`, `l`).
- `https://pokemonshowdown.com/ladder/<format>.json`: the top 500 of the
  format's ladder, in order. A user's rank is their position in it, and a
  user outside it has no published rank -- the site shows none either.

Both are fetched on demand and cached for a minute per key, which keeps the
viewer from polling the site harder than a person refreshing the ladder page.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

SITE = "https://pokemonshowdown.com"
USER_AGENT = "champions-bot-viewer (https://github.com/alexzhangryan/pokemonbot)"
CACHE_TTL_S = 60.0
#: How many the ladder page publishes; a rank beyond it is unknown, not large.
TOP = 500

Fetch = Callable[[str], Awaitable[Any]]


async def fetch_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


def _userid(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


class LadderLookup:
    """Rating and rank for one user in one format, cached."""

    def __init__(self, fetch: Fetch = fetch_json, ttl_s: float = CACHE_TTL_S) -> None:
        self._fetch = fetch
        self._ttl_s = ttl_s
        self._cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def lookup(self, username: str, format_id: str) -> dict[str, Any]:
        key = (_userid(username), format_id.lower())
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None and time.time() - cached[0] < self._ttl_s:
                return cached[1]
            result = await self._lookup(*key)
            self._cache[key] = (time.time(), result)
            return result

    async def _lookup(self, userid: str, format_id: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "username": userid,
            "format": format_id,
            "fetched_at": time.time(),
            "top": TOP,
        }
        try:
            user = await self._fetch(f"{SITE}/users/{userid}.json")
        except Exception as error:  # noqa: BLE001 - the site's failure is the result
            result["error"] = f"could not fetch the user's ratings: {error}"
            return result
        rating = ((user or {}).get("ratings") or {}).get(format_id)
        if not rating:
            result["rated"] = False
            return result
        result["rated"] = True
        for field in ("elo", "gxe", "rpr", "rprd", "w", "l"):
            if rating.get(field) is not None:
                result[field] = rating[field]
        result["username"] = (user or {}).get("username") or userid

        try:
            ladder = await self._fetch(f"{SITE}/ladder/{format_id}.json")
        except Exception as error:  # noqa: BLE001
            result["rank_error"] = f"could not fetch the ladder: {error}"
            return result
        toplist = (ladder or {}).get("toplist") or []
        result["rank"] = next(
            (i + 1 for i, entry in enumerate(toplist) if entry.get("userid") == userid), None
        )
        result["top"] = len(toplist) or TOP
        return result
