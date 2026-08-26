"""The orchestrator — fetch, filter, post, and log one full run."""

import argparse
import time
from datetime import datetime, timezone

from deal_bot import config, transport
from deal_bot.ai.categorizer import categorize_deals
from deal_bot.ai.classifier import classify_desirable_deals
from deal_bot.ai.deal_scorer import score_deals
from deal_bot.ai.spec_extraction import extract_clean_specs_batch
from deal_bot.ai.verdicts import build_verdicts_batch
from deal_bot.integrations.bluesky import post_to_bluesky
from deal_bot.integrations.discord import (
    build_categorizer_embed,
    build_digest_embed,
    build_quality_scorer_embed,
    build_run_log_embed,
    build_shadow_classification_embed,
    post_to_discord,
    post_deal_to_guilds,
    post_digest_to_guilds,
    _post_webhook,
)
from deal_bot.sources.bestbuy import fetch_bestbuy_search
from deal_bot.sources.shopify import fetch_all_shopify_stores
from deal_bot.sources.woot import fetch_woot_feed
from deal_bot.storage.guilds import (
    load_guild_destinations,
    load_guild_posted_ids,
    mark_initial_sync_complete,
    record_guild_post,
)
from deal_bot.storage.price_history import (
    get_price_history_stats_bulk,
    record_price_observations,
)
from deal_bot.storage.supabase import (
    _supabase_headers,
    load_seen,
    prune_seen,
    record_posted_deal,
    upsert_seen_entry,
)


# ---------------------------------------------------------------------------
# RUN LOG — one row per run_once() call, written whether the run succeeds
# or raises, so run history is visible without needing to watch console
# output (important since this runs unattended on a GitHub Actions
# schedule with no console to check). Also mirrored to a Discord channel
# via RUN_LOG_WEBHOOK_URL, if set, so a failure is visible somewhere you'll
# actually notice rather than only being a queryable row in Supabase.
# ---------------------------------------------------------------------------
def log_run(
    *, deals_checked: int, posted: int, skipped_already_seen: int,
    skipped_no_better_price: int, skipped_below_threshold: int,
    skipped_not_near_historical_low: int, digest_sent: bool, error: str | None,
    skipped_not_desirable: int = 0, shadow_sent: bool = False,
) -> None:
    if config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY:
        url = f"{config.SUPABASE_URL}/rest/v1/run_log"
        row = {
            "deals_checked": deals_checked,
            "posted": posted,
            "skipped_already_seen": skipped_already_seen,
            "skipped_no_better_price": skipped_no_better_price,
            "skipped_below_threshold": skipped_below_threshold,
            "skipped_not_near_historical_low": skipped_not_near_historical_low,
            "skipped_not_desirable": skipped_not_desirable,
            "shadow_sent": shadow_sent,
            "digest_sent": digest_sent,
            "error": error,
        }
        headers = _supabase_headers()
        headers["Prefer"] = "return=minimal"
        resp = transport.request("POST", url, headers=headers, json=[row])
        if resp is not None and resp.status_code not in (200, 201, 204):
            print(f"[supabase] run_log insert returned {resp.status_code}: {resp.text[:300]}")

    if config.RUN_LOG_WEBHOOK_URL:
        embed = build_run_log_embed(
            deals_checked=deals_checked, posted=posted,
            skipped_already_seen=skipped_already_seen,
            skipped_no_better_price=skipped_no_better_price,
            skipped_below_threshold=skipped_below_threshold,
            skipped_not_near_historical_low=skipped_not_near_historical_low,
            skipped_not_desirable=skipped_not_desirable,
            digest_sent=digest_sent, error=error,
        )
        _post_webhook(config.RUN_LOG_WEBHOOK_URL, {"embeds": [embed]}, "run-log")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def run_once() -> None:
    seen = load_seen()
    if seen is None:
        # A hard load failure means we can't trust dedupe state — running
        # with an empty seen map would treat every deal as new and risk
        # double-posting. Log and bail BEFORE fetching feeds so we don't
        # waste source calls on a run that will abort anyway.
        log_run(
            deals_checked=0, posted=0, skipped_already_seen=0,
            skipped_no_better_price=0, skipped_below_threshold=0,
            skipped_not_near_historical_low=0,
            skipped_not_desirable=0, shadow_sent=False, digest_sent=False,
            error="load_seen failed (Supabase unreachable) — bailed to avoid double-posting",
        )
        return
    destinations = load_guild_destinations()
    if destinations is None:
        log_run(
            deals_checked=0, posted=0, skipped_already_seen=0,
            skipped_no_better_price=0, skipped_below_threshold=0,
            skipped_not_near_historical_low=0,
            skipped_not_desirable=0, shadow_sent=False, digest_sent=False,
            error="load_guild_destinations failed (Supabase unreachable) — bailed to avoid mis-delivery",
        )
        return
    guild_posted_ids_map = {
        str(dest["guild_id"]): load_guild_posted_ids(dest["guild_id"])
        for dest in destinations
    }
    all_deals = []
    # Mutated in place by _process_deals rather than returned at the end —
    # if the loop raises partway through (after some deals already
    # posted), run_once() still has accurate partial counts for log_run()
    # instead of reporting an all-zero run that actually did something.
    stats = {
        "new_count": 0,
        "skipped_already_seen": 0,
        "skipped_no_better_price": 0,
        "skipped_below_threshold": 0,
        "skipped_not_near_historical_low": 0,
        "skipped_not_desirable": 0,
        "digest_sent": False,
        "shadow_sent": False,
    }
    # Tallied in-memory for the end-of-run digest — no need to persist
    # "source" into seen_deals since the digest only covers this single
    # run, not a longer window.
    digest_stats = {source: {"count": 0, "total_savings": 0.0, "best": None} for source in config.DIGEST_SOURCE_ORDER}

    # Everything below is wrapped so a run_log row gets written even if
    # something raises partway through — otherwise a crashed run would
    # leave no record at all, just a red X in the Actions tab.
    try:
        for feed in config.WOOT_FEEDS:
            all_deals.extend(fetch_woot_feed(feed))
            time.sleep(1.1)  # stay comfortably under Woot's 1 req/sec limit

        for term in config.BESTBUY_SEARCH_TERMS:
            all_deals.extend(fetch_bestbuy_search(term))
            time.sleep(0.3)

        all_deals.extend(fetch_all_shopify_stores())  # throttles between stores internally

        # Woot's "Electronics" and "Computers" feeds can both list the
        # same item, and Best Buy's search terms can overlap the same way
        # (e.g. "gaming mouse" and "mouse" returning the same SKU) — so
        # all_deals can contain the same deal_id more than once before
        # this point. Dedup once, here, so every downstream step (price
        # history, the historical-low gate, and the posting loop) sees
        # each deal exactly once per run instead of double-counting it.
        all_deals = list({d["id"]: d for d in all_deals}.values())

        # Log a price observation for every fetched deal, not just ones
        # that end up posting — see the PRICE HISTORY section.
        record_price_observations(all_deals)

        # One batched lookup for every deal's price history instead of a
        # live request per deal inside the posting loop.
        history_map = get_price_history_stats_bulk([d["id"] for d in all_deals])

        _process_deals(
            all_deals, seen, digest_stats, stats, history_map,
            destinations=destinations,
            guild_posted_ids_map=guild_posted_ids_map,
        )
    except Exception as e:
        log_run(
            deals_checked=len(all_deals), posted=stats["new_count"],
            skipped_already_seen=stats["skipped_already_seen"],
            skipped_no_better_price=stats["skipped_no_better_price"],
            skipped_below_threshold=stats["skipped_below_threshold"],
            skipped_not_near_historical_low=stats["skipped_not_near_historical_low"],
            skipped_not_desirable=stats["skipped_not_desirable"],
            shadow_sent=stats["shadow_sent"], digest_sent=stats["digest_sent"],
            error=str(e),
        )
        raise
    else:
        log_run(
            deals_checked=len(all_deals), posted=stats["new_count"],
            skipped_already_seen=stats["skipped_already_seen"],
            skipped_no_better_price=stats["skipped_no_better_price"],
            skipped_below_threshold=stats["skipped_below_threshold"],
            skipped_not_near_historical_low=stats["skipped_not_near_historical_low"],
            skipped_not_desirable=stats["skipped_not_desirable"],
            shadow_sent=stats["shadow_sent"], digest_sent=stats["digest_sent"],
            error=None,
        )


def _enrich_with_price_history(deal: dict, prior: dict | None) -> None:
    """Attach the price-history badge fields the embed/caption/analysis
    prompts read. "Lowest seen" here means the lowest we've ever ALERTED on
    (seen_deals), versus the item's true floor in price_history.

    is_new_low is STRICT (<): merely matching the previous low is not a new
    record. A tie takes the carry-forward branch, so the original
    lowest_price_date is preserved rather than refreshed to today."""
    prior_lowest = (prior or {}).get("lowest_price")
    sale = deal["sale_price"]
    deal["is_new_low"] = prior_lowest is not None and sale < prior_lowest
    if prior_lowest is not None and prior_lowest <= sale:
        deal["lowest_price"] = prior_lowest
        deal["lowest_price_date"] = prior.get("lowest_price_date")
    else:
        deal["lowest_price"] = sale
        deal["lowest_price_date"] = datetime.now(timezone.utc).isoformat()


def _skip_reason(deal: dict, prior: dict | None, history_days: int, history_low: float | None) -> str | None:
    """The deterministic pre-post filter, factored out so the gate decisions
    are independently testable. Returns the stats key explaining WHY the deal
    is skipped, or None when it should proceed to posting. Mirrors the
    historical behavior exactly."""
    if prior:
        prior_price = prior.get("sale_price")
        if prior_price is None:
            return "skipped_already_seen"
        if deal["sale_price"] >= prior_price - config.MIN_DOLLAR_SAVINGS:
            return "skipped_no_better_price"
    if deal["discount_pct"] is None or deal["discount_pct"] < config.MIN_DISCOUNT_PERCENT:
        return "skipped_below_threshold"
    if deal["list_price"] and (deal["list_price"] - deal["sale_price"]) < config.MIN_DOLLAR_SAVINGS:
        return "skipped_below_threshold"
    # Price-history quality gate: once there's enough real history for this
    # exact item, require the sale price to be near its own recorded floor,
    # not just far from the retailer's list price. Dormant until enough days.
    if history_days >= config.PRICE_HISTORY_MIN_DAYS and history_low is not None:
        ceiling = history_low * (1 + config.PRICE_HISTORY_TOLERANCE_PERCENT / 100)
        if deal["sale_price"] > ceiling:
            return "skipped_not_near_historical_low"
    return None


def _process_deals(
    all_deals: list[dict], seen: dict, digest_stats: dict, stats: dict, history_map: dict,
    destinations: list[dict] | None = None,
    guild_posted_ids_map: dict[str, set[str]] | None = None,
) -> None:
    """Posting pipeline, structured in explicit phases so AI judgment runs
    BEFORE posting (the desirability classifier can gate when
    CLASSIFIER_MODE=gate) while remaining behavior-neutral in the default
    shadow mode.

    Phase A) deterministic filter -> candidates; B) batched AI enrichment
    (specs + consolidated caption/analysis verdicts) + optional classifier
    gate; C) post loop; D) capped Bluesky + digest + shadow reports.
    Mutates `stats` in place rather than returning at the end — run_once()
    depends on that for crash-accurate run_log counts."""
    destinations = list(destinations or [])
    guild_posted_ids_map = guild_posted_ids_map if guild_posted_ids_map is not None else {}
    bluesky_candidates = []  # collected here, ranked and capped after the loop

    # ---- PHASE A — deterministic filter into a candidate list -------------
    candidates = []
    for deal in all_deals:
        prior = seen.get(deal["id"])
        history = history_map.get(deal["id"]) or {}
        reason = _skip_reason(deal, prior, history.get("days", 0), history.get("lowest"))
        if reason is not None:
            stats[reason] += 1
            continue
        # Price-history tracking for the embed badge (see the helper's
        # docstring for the strict-new-low / tie-keeps-date semantics),
        # plus the raw trend facts ({days, lowest, drops, lowest_date})
        # the AI prompts narrate as pre-verified context.
        _enrich_with_price_history(deal, prior)
        deal["price_trend"] = history or None
        candidates.append(deal)

    # ---- PHASE B — batched AI enrichment ----------------------------------
    # Spec extraction runs for EVERY deal — with Steam retired, all sources
    # (Woot, Best Buy, Shopify) benefit equally from title cleanup, so the
    # old Steam special-case is gone and nothing is excluded up front.
    spec_results = extract_clean_specs_batch([d["title"] for d in candidates])
    for deal, result in zip(candidates, spec_results):
        deal["clean_title"] = result["clean_title"]
        deal["specs"] = result["specs"]

    # One batched call per run produces BOTH the short caption (Bluesky +
    # private mirror) and the long Discord analysis — previously two
    # separate call chains over identical context. Fails open per-item.
    verdicts = build_verdicts_batch(candidates)
    for deal, verdict in zip(candidates, verdicts):
        deal["caption"] = verdict["caption"]
        deal["analysis"] = verdict["analysis"]

    # Optional real gate (CLASSIFIER_MODE=gate): DROP-verdict candidates are
    # removed before posting. Fails open — a model/parse failure returns
    # everything as keep, so an unusable response never suppresses a deal.
    if candidates and config.CLASSIFIER_MODE == "gate":
        keep, drop, model_used = classify_desirable_deals(candidates)
        if model_used and drop:
            dropped_ids = {d["id"] for d in drop}
            stats["skipped_not_desirable"] += len(drop)
            candidates = [d for d in candidates if d["id"] not in dropped_ids]
            print(f"[gate] desirability classifier withheld {len(drop)} candidate(s)")

    # ---- PHASE C — post loop ---------------------------------------------
    # Initial sync: first run after /setup seeds current candidates as
    # already-posted so a new guild is not flooded. Delivery starts next run.
    if destinations:
        for dest in destinations:
            if dest.get("initial_sync_complete"):
                continue
            guild_id = str(dest["guild_id"])
            posted = guild_posted_ids_map.setdefault(guild_id, set())
            seeded_ok = True
            for deal in candidates:
                if deal["id"] in posted:
                    continue
                if record_guild_post(guild_id, deal["id"], deal["sale_price"]):
                    posted.add(deal["id"])
                else:
                    seeded_ok = False
            if seeded_ok:
                mark_initial_sync_complete(guild_id)
                dest["initial_sync_complete"] = True

    for deal in candidates:
        if destinations:
            delivered = post_deal_to_guilds(deal, destinations, guild_posted_ids_map)
            posted_ok = delivered > 0
            if not posted_ok:
                enabled = [d for d in destinations if d.get("enabled", True)]
                already = enabled and all(
                    deal["id"] in guild_posted_ids_map.get(str(d["guild_id"]), set())
                    for d in enabled
                )
                if already:
                    seen[deal["id"]] = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "sale_price": deal["sale_price"],
                        "lowest_price": deal["lowest_price"],
                        "lowest_price_date": deal["lowest_price_date"],
                    }
                    upsert_seen_entry(deal["id"], deal["source"], seen[deal["id"]])
                continue
        elif not post_to_discord(deal):
            continue

        seen[deal["id"]] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sale_price": deal["sale_price"],
            "lowest_price": deal["lowest_price"],
            "lowest_price_date": deal["lowest_price_date"],
        }
        upsert_seen_entry(deal["id"], deal["source"], seen[deal["id"]])
        record_posted_deal(deal)
        stats["new_count"] += 1
        time.sleep(2)

        source_stats = digest_stats.setdefault(deal["source"], {"count": 0, "total_savings": 0.0, "best": None})
        source_stats["count"] += 1
        if deal["list_price"]:
            source_stats["total_savings"] += deal["list_price"] - deal["sale_price"]
        best = source_stats["best"]
        if best is None or (deal["discount_pct"] or 0) > (best["discount_pct"] or 0):
            source_stats["best"] = {
                "title": deal["title"],
                "url": deal["url"],
                "discount_pct": deal["discount_pct"],
            }

        # Bluesky candidacy only — actual posting happens after the loop,
        # capped to the top BLUESKY_MAX_POSTS_PER_RUN by $ saved. Requires a
        # known list price to rank by savings.
        if (deal["discount_pct"] is not None and deal["discount_pct"] >= config.BLUESKY_MIN_DISCOUNT_PERCENT
                and deal["list_price"]):
            bluesky_candidates.append(deal)

    # ---- PHASE: post-loop — prune, digest, capped bluesky -----------------
    prune_seen(config.SEEN_TTL_DAYS)
    print(
        f"[run] checked {len(all_deals)} deals — "
        f"{stats['new_count']} posted, "
        f"{stats['skipped_already_seen']} already posted at this price or better, "
        f"{stats['skipped_no_better_price']} same item but not enough of a price drop, "
        f"{stats['skipped_below_threshold']} below the discount/savings threshold, "
        f"{stats['skipped_not_near_historical_low']} not near their historical low"
        + (f", {stats['skipped_not_desirable']} withheld by the desirability gate" if stats["skipped_not_desirable"] else "")
    )

    # Only send a digest when there's something to report — an empty
    # "0 posted" message every run would just be noise.
    if stats["new_count"] > 0:
        if destinations:
            post_digest_to_guilds(build_digest_embed(digest_stats), destinations)
            stats["digest_sent"] = True
        elif config.DIGEST_WEBHOOK_URL:
            stats["digest_sent"] = _post_webhook(
                config.DIGEST_WEBHOOK_URL, {"embeds": [build_digest_embed(digest_stats)]}, "digest"
            )

    bluesky_candidates.sort(key=lambda d: d["list_price"] - d["sale_price"], reverse=True)
    for deal in bluesky_candidates[:config.BLUESKY_MAX_POSTS_PER_RUN]:
        if post_to_bluesky(deal):
            print(f"[bluesky] posted: {deal['title'][:60]}")
        time.sleep(1)

    # SHADOW MODE: report what the desirability classifier would have
    # kept/dropped from this run's CANDIDATES. Judging all candidates (not
    # just what posted) means shadow data accumulates even on runs where
    # nothing clears the deterministic filters — the review data gate-mode
    # promotion depends on. Nothing here changes what already posted above.
    # In gate mode this embed is skipped: the classifier's verdicts already
    # decided what posted, so a "would have dropped" report is meaningless.
    if candidates and config.SHADOW_CLASSIFIER_WEBHOOK_URL and config.CLASSIFIER_MODE != "gate":
        keep, drop, model_used = classify_desirable_deals(candidates)
        if model_used:
            _post_webhook(
                config.SHADOW_CLASSIFIER_WEBHOOK_URL,
                {"embeds": [build_shadow_classification_embed(keep, drop, model_used)]},
                "shadow-classifier",
            )
            stats["shadow_sent"] = True

    # SHADOW MODE: report the deal quality scorer's 1-10 ratings for the
    # run's candidates, and what it would have dropped below
    # MIN_QUALITY_SCORE. Nothing here changes what already posted above.
    if candidates and config.SHADOW_QUALITY_SCORER_WEBHOOK_URL:
        scores, model_used = score_deals(candidates)
        if model_used:
            _post_webhook(
                config.SHADOW_QUALITY_SCORER_WEBHOOK_URL,
                {"embeds": [build_quality_scorer_embed(candidates, scores, model_used, config.MIN_QUALITY_SCORE)]},
                "shadow-quality-scorer",
            )
            stats["shadow_sent"] = True

    # SHADOW MODE: report the category tagger's per-deal classification for
    # the run's candidates. Observation only — not yet used to gate or route.
    if candidates and config.SHADOW_CATEGORIZER_WEBHOOK_URL:
        categories, model_used = categorize_deals(candidates)
        if model_used:
            _post_webhook(
                config.SHADOW_CATEGORIZER_WEBHOOK_URL,
                {"embeds": [build_categorizer_embed(candidates, categories, model_used)]},
                "shadow-categorizer",
            )
            stats["shadow_sent"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="keep running instead of a single pass (local testing only — GitHub Actions uses its own schedule)")
    parser.add_argument("--interval", type=int, default=1800, help="seconds between polls when --loop is set")
    args = parser.parse_args()

    if args.loop:
        while True:
            run_once()
            time.sleep(args.interval)
    else:
        run_once()
