"""
The only module that talks to fantasy.premierleague.com.
Everything it returns is treated as untrusted input and only used for comparison.
"""
import re
import time
import unicodedata
from dataclasses import dataclass

import httpx

from app.config import settings
from app.services.fpl_sync import fpl_rate_limiter

FPL_BASE = "https://fantasy.premierleague.com/api"
MANAGER_ID_RE = re.compile(r"^[1-9][0-9]{0,9}$")


class FPLManagerNotFound(Exception):
    pass


class FPLUpstreamError(Exception):
    pass


@dataclass(frozen=True)
class FPLEntry:
    manager_id: str
    team_name: str
    country_code: str | None      # ISO alpha-2 from FPL's player_region_iso_code_short


def parse_manager_id(raw: str) -> str | None:
    """Digits only, no leading zero, max 10 digits. Returns the canonical string or None."""
    raw = (raw or "").strip()
    return raw if MANAGER_ID_RE.fullmatch(raw) else None


def normalize_team_name(name: str) -> str:
    """Unicode NFC, trimmed, inner whitespace collapsed. Case is preserved."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", name or "")).strip()


def names_match(submitted: str, actual: str) -> bool:
    a, b = normalize_team_name(submitted), normalize_team_name(actual)
    if not a or not b:
        return False
    return a == b if settings.team_name_case_sensitive else a.casefold() == b.casefold()


def fetch_manager_entry(manager_id: str) -> FPLEntry:
    if parse_manager_id(manager_id) is None:
        raise FPLManagerNotFound(manager_id)
    fpl_rate_limiter.acquire()
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=8, headers={"User-Agent": "Mozilla/5.0 (compatible; FPLPlatform/1.0)"}) as c:
                resp = c.get(f"{FPL_BASE}/entry/{manager_id}/")
            if resp.status_code == 404:
                raise FPLManagerNotFound(manager_id)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise FPLUpstreamError(f"FPL returned {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("name"), str):
                raise FPLUpstreamError("Unexpected FPL response shape")
            region = data.get("player_region_iso_code_short")
            return FPLEntry(
                manager_id=manager_id,
                team_name=data["name"],
                country_code=region.upper() if isinstance(region, str) and region else None,
            )
        except FPLManagerNotFound:
            raise
        except (httpx.HTTPError, ValueError, FPLUpstreamError) as exc:
            last_exc = exc
            time.sleep(0.4)
    raise FPLUpstreamError(str(last_exc))
