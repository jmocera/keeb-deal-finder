"""Price-history persistence — raw observations log, one row per fetched
deal per day, whether or not it clears the posting threshold. Backs the
quality gate in pipeline._process_deals and is also queryable directly in
Supabase Studio for trends.

Table: price_history (id bigserial pk, deal_id text, source text,
observed_at timestamptz default now(), sale_price numeric, list_price
numeric, discount_pct numeric, observed_date date) with a unique
constraint on (deal_id, observed_date), upserted via
`?on_conflict=deal_id,observed_date`.
"""

from datetime import date

from deal_bot import config, transport
from deal_bot.storage.supabase import _supabase_headers


def record_price_observations(deals: list[dict]) -> None:
    if not config.SUPABASE_URL or not config.get_supabase_key() or not deals:
        return
    url = f"{config.SUPABASE_URL}/rest/v1/price_history?on_conflict=deal_id,observed_date"
    today = date.today().isoformat()
    rows = [{
        "deal_id": d["id"],
        "source": d["source"],
        "sale_price": d["sale_price"],
        "list_price": d["list_price"],
        "discount_pct": d["discount_pct"],
        "observed_date": today,
    } for d in deals]
    headers = _supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    resp = transport.request("POST", url, headers=headers, json=rows, timeout=30)
    if resp is None:
        print("[supabase] price_history upsert failed after retries")
        return
    if resp.status_code not in (200, 201, 204):
        print(f"[supabase] price_history upsert returned {resp.status_code}: {resp.text[:300]}")


def get_price_history_stats_bulk(deal_ids: list[str]) -> dict[str, dict]:
    """Batched replacement for querying price_history one deal at a time
    inside the posting loop — at 350+ deals a run (more once Best Buy is
    live), one live request per deal was 350+ sequential round-trips just
    for history lookups. This fetches everything in a handful of chunked
    requests instead.

    Returns {deal_id: {"days": distinct_days_observed, "lowest":
    lowest_price_ever_recorded, "drops": day-over-day price decreases,
    "lowest_date": ISO date of the earliest day that hit the floor}}; an ID
    with no history simply isn't a key in the result, so callers should use
    .get(id, {}) / .get(id) and treat missing keys as "no history".

    Distinct days, not raw row count, on purpose — if this runs
    frequently, several observations can land on the same day without the
    retailer's price ever actually changing, which wouldn't tell us
    anything about real price behavior over time. Multiple same-day rows
    (legacy pre-unique-constraint dupes) collapse to the day's minimum.
    `drops` counts day-over-day DECREASES across sorted distinct days —
    the raw material for the AI prompts' factual price-trend line."""
    results: dict[str, dict] = {}
    if not config.SUPABASE_URL or not config.get_supabase_key() or not deal_ids:
        return results

    unique_ids = list(dict.fromkeys(deal_ids))  # de-dupe, keep it simple
    chunk_size = 100  # keeps each "in.(...)" query string comfortably short
    url = f"{config.SUPABASE_URL}/rest/v1/price_history"
    rows_by_deal: dict[str, list[tuple[str, float]]] = {}
    for i in range(0, len(unique_ids), chunk_size):
        chunk = unique_ids[i:i + chunk_size]
        resp = transport.request(
            "GET", url, headers=_supabase_headers(),
            params={"deal_id": f"in.({','.join(chunk)})", "select": "deal_id,sale_price,observed_at"},
            timeout=20,
        )
        if resp is None:
            print("[supabase] price_history bulk stats failed after retries")
            continue
        if resp.status_code != 200:
            print(f"[supabase] price_history bulk stats returned {resp.status_code}: {resp.text[:300]}")
            continue
        for row in resp.json():
            rows_by_deal.setdefault(row["deal_id"], []).append((row["observed_at"][:10], row["sale_price"]))

    for deal_id, rows in rows_by_deal.items():
        # Collapse any same-day duplicates (legacy rows predate the unique
        # constraint) to the day's minimum observed price.
        by_day: dict[str, float] = {}
        for day, price in rows:
            by_day[day] = min(price, by_day[day]) if day in by_day else price
        days_sorted = sorted(by_day)
        lowest_price = min(by_day.values())
        results[deal_id] = {
            "days": len(days_sorted),
            "lowest": lowest_price,
            "drops": sum(
                1 for prev, cur in zip(days_sorted, days_sorted[1:])
                if by_day[cur] < by_day[prev]
            ),
            # Earliest date that touched the floor (not the latest).
            "lowest_date": next(d for d in days_sorted if by_day[d] == lowest_price),
        }

    return results