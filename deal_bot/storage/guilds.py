"""Per-guild delivery state — destinations and per-guild post dedupe.

Tables:
  guild_destinations (guild_id text pk, channel_id text, enabled bool,
                      initial_sync_complete bool, created_at, updated_at)
  guild_deal_posts   (guild_id text, deal_id text, sale_price numeric,
                      posted_at timestamptz, primary key (guild_id, deal_id))

Accessed via PostgREST the same way as storage/supabase.py. GETs and
idempotent upserts go through transport.request (safe to retry). Failures
print and never raise. load_guild_destinations returns None on hard
failure so the pipeline can bail; every other loader fail-opens to []/set().
"""

from datetime import datetime, timezone

from deal_bot import config, transport
from deal_bot.storage.supabase import _supabase_headers


def load_guild_destinations() -> list[dict] | None:
    """Enabled guild destinations, or [] if unconfigured / no rows.

    Returns None on network exhaustion or non-200 — callers treat that as
    fatal (same shape as load_seen) so a half-loaded destination list
    cannot silently drop guilds for a run.
    """
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        return []
    url = (
        f"{config.SUPABASE_URL}/rest/v1/guild_destinations"
        "?enabled=eq.true"
        "&select=guild_id,channel_id,enabled,initial_sync_complete"
    )
    resp = transport.request("GET", url, headers=_supabase_headers())
    if resp is None:
        print("[guilds] load_guild_destinations failed after retries")
        return None
    if resp.status_code != 200:
        print(f"[guilds] load_guild_destinations returned {resp.status_code}: {resp.text[:300]}")
        return None
    out = []
    for row in resp.json():
        out.append({
            "guild_id": str(row["guild_id"]),
            "channel_id": str(row["channel_id"]),
            "enabled": bool(row.get("enabled", True)),
            "initial_sync_complete": bool(row.get("initial_sync_complete", False)),
        })
    return out


def upsert_guild_destination(guild_id, channel_id) -> None:
    """/setup — enable this guild and reset initial_sync_complete so the
    next pipeline run seeds the current candidate list as baseline."""
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        print("[guilds] no Supabase config — cannot upsert destination")
        return
    url = f"{config.SUPABASE_URL}/rest/v1/guild_destinations"
    headers = _supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "enabled": True,
        "initial_sync_complete": False,
        "updated_at": now,
    }
    resp = transport.request("POST", url, headers=headers, json=[row])
    if resp is None:
        print(f"[guilds] upsert destination failed for {guild_id} after retries")
        return
    if resp.status_code not in (200, 201, 204):
        print(f"[guilds] upsert destination for {guild_id} returned {resp.status_code}: {resp.text[:300]}")


def disable_guild_destination(guild_id) -> None:
    """/disable — stop delivering to this guild. Row is kept so /setup can re-enable."""
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        print("[guilds] no Supabase config — cannot disable destination")
        return
    url = f"{config.SUPABASE_URL}/rest/v1/guild_destinations"
    headers = _supabase_headers()
    headers["Prefer"] = "return=minimal"
    resp = transport.request(
        "PATCH", url, headers=headers,
        params={"guild_id": f"eq.{guild_id}"},
        json={"enabled": False, "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if resp is None:
        print(f"[guilds] disable destination failed for {guild_id} after retries")
        return
    if resp.status_code not in (200, 204):
        print(f"[guilds] disable destination for {guild_id} returned {resp.status_code}: {resp.text[:300]}")


def mark_initial_sync_complete(guild_id) -> None:
    """Called after the first run's baseline seed succeeds for this guild."""
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        return
    url = f"{config.SUPABASE_URL}/rest/v1/guild_destinations"
    headers = _supabase_headers()
    headers["Prefer"] = "return=minimal"
    resp = transport.request(
        "PATCH", url, headers=headers,
        params={"guild_id": f"eq.{guild_id}"},
        json={"initial_sync_complete": True, "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if resp is None:
        print(f"[guilds] mark_initial_sync_complete failed for {guild_id} after retries")
        return
    if resp.status_code not in (200, 204):
        print(f"[guilds] mark_initial_sync_complete for {guild_id} returned {resp.status_code}: {resp.text[:300]}")


def load_guild_posted_ids(guild_id) -> set[str]:
    """Deal ids already delivered (or seeded) to this guild.

    Returns set() on no-config, no-rows, network failure, or non-200.
    Paginates at 1000 (Supabase PostgREST default max-rows).
    """
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        return set()
    url = f"{config.SUPABASE_URL}/rest/v1/guild_deal_posts"
    posted: set[str] = set()
    offset = 0
    page_size = 1000
    while True:
        resp = transport.request(
            "GET", url, headers=_supabase_headers(),
            params={
                "guild_id": f"eq.{guild_id}",
                "select": "deal_id",
                "limit": str(page_size),
                "offset": str(offset),
            },
        )
        if resp is None:
            print(f"[guilds] load_guild_posted_ids failed for {guild_id} after retries")
            return set()
        if resp.status_code != 200:
            print(f"[guilds] load_guild_posted_ids for {guild_id} returned {resp.status_code}: {resp.text[:300]}")
            return set()
        rows = resp.json()
        posted.update(str(row["deal_id"]) for row in rows)
        if len(rows) < page_size:
            break
        offset += page_size
    return posted


def record_guild_post(guild_id, deal_id, sale_price) -> bool:
    """Immediate write after a successful delivery (or initial-sync seed).

    Returns True on success (including 'no supabase config' is False — a
    seed must not mark sync complete if it could not persist). Failures
    print and never raise.
    """
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        print("[guilds] no Supabase config — cannot record guild post")
        return False
    url = f"{config.SUPABASE_URL}/rest/v1/guild_deal_posts?on_conflict=guild_id,deal_id"
    headers = _supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    row = {
        "guild_id": str(guild_id),
        "deal_id": str(deal_id),
        "sale_price": sale_price,
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = transport.request("POST", url, headers=headers, json=[row])
    if resp is None:
        print(f"[guilds] record_guild_post failed for {guild_id}/{deal_id} after retries")
        return False
    if resp.status_code not in (200, 201, 204):
        print(f"[guilds] record_guild_post for {guild_id}/{deal_id} returned {resp.status_code}: {resp.text[:300]}")
        return False
    return True
