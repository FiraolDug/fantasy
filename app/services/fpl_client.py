"""
Thin client for the official (unofficial-but-real) FPL API.
Keep ALL outbound FPL calls behind this module so the rate limiter
in app/services/fpl_sync.py has one place to enforce the 50-60 req/min
ceiling — never call fantasy.premierleague.com directly from a router.
"""
import httpx

FPL_BASE = "https://fantasy.premierleague.com/api"


class FPLManagerNotFound(Exception):
    pass


def fetch_manager_entry(manager_id: str) -> dict:
    url = f"{FPL_BASE}/entry/{manager_id}/"
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
    if resp.status_code == 404:
        raise FPLManagerNotFound(manager_id)
    resp.raise_for_status()
    data = resp.json()
    return {
        "manager_id": str(manager_id),
        "team_name": data.get("name", ""),
        "manager_name": f"{data.get('player_first_name', '')} {data.get('player_last_name', '')}".strip(),
    }
