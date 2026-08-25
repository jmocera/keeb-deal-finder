<task id="ses_fca0af9f7ffeJglB16k3SdXZ9f" state="completed">
<task_result>
I've read all the critical files. Below is the review + concrete contracts. I'll be tight; no boilerplate.

---

# 1. Plan review — corrections to the original Phase list

The plan is largely sound, but **six concrete corrections** are required before any coding agent implements it. Every line reference in the original plan checks out against the repo (pipeline.py:24/136 ✓, pipeline.py:258-270 ✓, config.py:247/252 ✓, discord.py:118 ✓, test_sources.py:119-141 ✓, .env.example:17 ✓, deal_bot.yml:44 ✓). The plan's structural understanding of the merge/dedup/gate/shadow logic is correct. The corrections below are all **missed side-effects**, not wrong assumptions.

### C1 — `test_pipeline.py` patches a now-nonexistent symbol (BLOCKER)
`tests/test_pipeline.py:37` and `:54` both `patch("deal_bot.pipeline.fetch_steam_specials")`. Once `pipeline.py:24` deletes the import, `unittest.mock.patch` raises `AttributeError` at decoration time → **both `RunOnceBailTests` tests fail to even collect.** The plan's Phase 8 only mentions `test_shopify.py`; Phase 1 must also edit `test_pipeline.py`.

**Fix:** Remove the two `patch("deal_bot.pipeline.fetch_steam_specials")` lines (and the `with`-clause entries `mock_steam`/`mock_`steam) from `RunOnceBailTests`. No behavioral change — Steam was mocked to no-op anyway.

### C2 — `test_spec_extraction.py:152-205` `ProcessDealsSteamSkipTests` becomes dead (BLOCKER)
This entire class (`test_steam_deal_never_calls_spec_extraction`, `test_non_steam_deal_calls_batched_spec_extraction`) asserts the Steam-specific Phase B branch the plan deletes in Phase 1. After Phase B simplification, `extract_clean_specs_batch` is called on every candidate regardless of source, so `test_steam_deal_never_calls_spec_extraction` would fail.

**Fix:** Delete the entire `ProcessDealsSteamSkipTests` class (lines 152-205) and its docstring. Replace with a single regression test `test_spec_extraction_called_for_all_sources` that runs a `Woot` deal and a `Shopify` deal through `_process_deals` and asserts `extract_clean_specs_batch` is called once with both titles (proving the post-Steam simplification didn't accidentally drop the call).

### C3 — `test_categorizer.py` fixtures break on `DEAL_CATEGORIES` change (BLOCKER)
Every fixture in `tests/test_categorizer.py` returns the old category tokens (`storage\ncomponent\ngame`, `peripheral`, `display`, `Game Controller`, `storage device`, etc.). After Phase 2 changes `DEAL_CATEGORIES` to `["board","switch","keycaps","accessory","other"]`, the strict-path validator `c in valid` rejects every fixture → **all 14 `CategorizeDealsTests`/`LenientParseTests` methods fail.** The plan's Phase 8 doesn't mention this.

**Fix:** Phase 2 must also rewrite every `mock_call.return_value` and the `_extract_categories` literal-test in `test_categorizer.py` to use the new tokens. Specific substitutions:
- `"storage\ncomponent\ngame"` → `"board\nswitch\nkeycaps"`
- `"storage\ncomponent\nperipheral"` → `"board\nswitch\naccessory"`
- `"Game Controller\nstorage device\nthe display"` → `"Keyboard Kit\nswitch pack\nthe board"` (must still fail to parse — line-anchored regex rejects contaminated lines)
- `test_extract_categories_line_anchored` literal: `"Game\nStorage Device\ngames\n- display\n1. peripheral"` → `"board\nSwitch Pack\nboardss\n- keycaps\n1. accessory"` (expects `["board","keycaps","accessory"]`)
- `"toaster fan lamp"` (negative fixture) — unchanged, still invalid.

Also `_make_deal` titles `f"Deal {i}"` are source-agnostic — keep.

### C4 — Docstring leaks the plan's Phase 4 missed
- `deal_bot/sources/__init__.py:1` — `"Deal sources — Woot, Best Buy, Steam."`
- `deal_bot/__init__.py:5` — `"sources: deal fetchers (Woot, Best Buy, Steam)"`
- `deal_bot/transport.py:11` — `"Woot/Best Buy/Steam"` in the scope comment
- `tests/test_sources.py:1` — `"Tests for the deal sources (woot/bestbuy/steam)"`
- `tests/test_sources.py:13` — `from deal_bot.sources import bestbuy, steam, woot` — **must drop `steam`** or ImportError.
- `deal_bot/pipeline.py:258-260` — the comment block "Spec extraction, Woot/Best Buy only. Steam titles are already clean..." must be deleted in Phase 1 (not just the code).

The plan's Phase 4 list is incomplete; add the above.

### C5 — Phase 6 design ambiguity: collection_handles vs store-root `/products.json`
The plan says `DEFAULT_SHOPIFY_STORES = [{name, base_url, collection_handles}]` AND `SHOPIFY_MAX_COLLECTIONS_PER_STORE=1`. The verification note says every store serves `/products.json` (store root). Two distinct fetch shapes exist:

- `{base_url}/products.json?limit=250` — ALL products, newest first, single request, no collection scoping.
- `{base_url}/collections/{handle}/products.json?limit=250` — only that collection's products.

**Decision needed:** If `collection_handles` is provided AND non-empty, fetch each via `/collections/{handle}/products.json`, capped at `SHOPIFY_MAX_COLLECTIONS_PER_STORE` handles. If `collection_handles` is `[]` or missing, fall back to store-root `/products.json`. This is the most flexible contract; I'll specify it below. The plan as written is ambiguous on this — the agent must NOT implement "always fetch store-root" because then `collection_handles` is dead config.

### C6 — Pagination risk the plan missed entirely
Shopify's `/products.json` returns at most **30 products per page by default**, or up to **250 with `?limit=250`**. No `Link` header traversal is in the plan. For KBDfans (hundreds of products) this means under-fetching. **Contract below mandates `?limit=250` and a single-page fetch per collection**, with a clear comment that pagination is a future-work item (not silently truncating). This is a coverage risk the plan must acknowledge; flagging in the final risk section.

### C7 — `test_weekly_digest.py:56` and seed names
`test_weekly_digest.py:56` asserts the mock return value `"This week's best PC and gaming deals: ..."` — that's the mocked `_call_openrouter` return, not the prompt, so the test still passes after Phase 3 re-themes the system prompt. **No fix needed**, but the seed names in `weekly_digest.py:116-119` (Phase 4) include `"Elden Ring (Steam)"` and `"Logitech G502 X Wireless Mouse"` — both must be replaced with keeb-themed names since Steam is gone and mice are now in WOOT_EXCLUDE.

### C8 — `posted_deals.source` for Shopify
The plan says `source="Shopify"` for all stores. `posted_deals.source` is a free-text column — no Supabase migration needed. But the `weekly_digest` user-prompt line `f"- [{d['source']}] {d['title']}..."` will read `"[Shopify]"` for every keeb store, losing store identity. Since Phase 6 puts the store name in the title (`"{title} ({store})"`), the digest still carries it. **No fix needed**, but the agent must not strip the `({store})` suffix from titles.

---

# 2. Concrete contracts

## (a) Config contract — `deal_bot/config.py`

Add the following **after** the existing `BESTBUY_SEARCH_TERMS` block (currently ending at line 223). Replace the existing `WOOT_EXCLUDE_KEYWORDS`, `WOOT_INCLUDE_KEYWORDS`, `BESTBUY_SEARCH_TERMS`, and `DEAL_CATEGORIES` definitions in place; append the Shopify block new.

```python
# ---------------------------------------------------------------------------
# Woot/Best Buy keyword filters — re-themed for mechanical-keyboard focus.
# A Woot deal must match >=1 WOOT_INCLUDE_KEYWORDS (case-insensitive
# substring) AND match zero WOOT_EXCLUDE_KEYWORDS. The exclude list kills
# combos/bundles/headsets/webcams so accessory noise doesn't flood the
# channel; the include list requires an actual keeb term.
# ---------------------------------------------------------------------------
WOOT_EXCLUDE_KEYWORDS = [
    # Apparel / non-tech (carryover)
    "squishmallow", "plush", "stuffed animal", "funko",
    "apparel", "shirt", "hoodie", "sneaker", "shoes",
    "cookware", "kitchen", "furniture", "decor", "bedding", "mattress",
    # Anti-flood: combos, bundles, and non-keyboard peripherals
    "mouse", "mice", "combo", "bundle", "headset", "webcam",
    "microphone", "mic ", "speaker", "monitor", "display",
    "laptop", "chromebook", "router", "console", "controller",
    "gpu", "graphics card", "video card", "motherboard", "cpu",
    "processor", "ssd", "nvme", "hard drive", "power supply", "psu",
    "ram ", "memory", "pc case", "cpu cooler",
]

WOOT_INCLUDE_KEYWORDS = [
    # Generic keeb terms — must appear in title to pass
    "keyboard", "mechanical keyboard", "keycap", "key switch",
    "switch", "keycap set", "artisan", "deskmat", "switch opener",
    "switch tester", "pcb", "stabilizer", "stabiliser", "plate mount",
    # Brands the audience cares about
    "kbdfans", "novelkeys", "cannonkeys", "divinikey", "dailyclack",
    "primekb", "kinetic labs", "akko", "gmmk", "glorious",
    "rama", "keychron", "leopold", "hhkb", "realforce", "wooting",
    "drop ", "keebio", "splitkb",
    # Switch / keycap families
    "cherry mx", "gateron", "kailh", "tecsee", "jwick", "everglide",
    "boba", "tactile", "linear", "hall effect", "magnetic",
    "pbt ", "abs ", "doubleshot", "pudding",
]

# Best Buy keyword searches — re-themed. Best Buy has a real keyboards
# category; these are the search terms that surface it.
BESTBUY_SEARCH_TERMS = [
    "mechanical keyboard", "gaming keyboard", "keycaps",
    "keyboard switch", "keyboard kit", "custom keyboard",
    "deskmat", "keyboard wrist rest",
]

# ---------------------------------------------------------------------------
# Categories — keeb-focused. "other" is an explicit junk bucket so Woot /
# Best Buy accessory noise that slips through the include/exclude filters
# can be tagged-and-reviewed rather than appearing as "accessory".
# ---------------------------------------------------------------------------
DEAL_CATEGORIES = ["board", "switch", "keycaps", "accessory", "other"]

# ---------------------------------------------------------------------------
# Shopify source — config-driven list of stores. Each store is a dict with:
#   name              : human-readable, used in title suffix "({name})" and
#                       in the Discord embed footer when present.
#   base_url          : store root, no trailing slash, e.g. "https://kbdfans.com".
#   collection_handles: list of Shopify collection slugs to scope the fetch
#                       (e.g. ["keyboards","switches"]). If empty/missing,
#                       the fetcher falls back to the store-root products.json
#                       (all products, newest first).
# SHOPIFY_STORES (env) is a JSON string of the same shape; if parseable and
# non-empty it overrides DEFAULT_SHOPIFY_STORES so GitHub Actions can change
# the store list without a code commit. Parsing fails CLOSED: a malformed
# JSON string yields DEFAULT_SHOPIFY_STORES and a printed warning (never an
# empty list, which would silently disable the source).
# ---------------------------------------------------------------------------
import json as _json

DEFAULT_SHOPIFY_STORES = [
    {"name": "KBDfans",      "base_url": "https://kbdfans.com",      "collection_handles": ["keyboards", "keycaps", "switches"]},
    {"name": "NovelKeys",    "base_url": "https://novelkeys.com",    "collection_handles": []},
    {"name": "CannonKeys",   "base_url": "https://cannonkeys.com",   "collection_handles": []},
    {"name": "Divinikey",    "base_url": "https://divinikey.com",    "collection_handles": []},
    {"name": "DailyClack",   "base_url": "https://dailyclack.com",   "collection_handles": []},
    {"name": "PrimeKB",      "base_url": "https://primekb.com",      "collection_handles": []},
    # Kinetic Labs is intentionally OMITTED: it does not serve Shopify's
    # /products.json (returns 404/HTML). Add only if/when its storefront
    # exposes the Shopify JSON endpoint.
]

SHOPIFY_WEBHOOK_URL = os.environ.get("SHOPIFY_WEBHOOK_URL", "")

# Cap how many collection_handles we fetch per store, regardless of how
# many are configured. Conservative default of 1 keeps per-run request
# volume low; raise via env once the source has earned trust.
SHOPIFY_MAX_COLLECTIONS_PER_STORE = int(os.environ.get("SHOPIFY_MAX_COLLECTIONS_PER_STORE", "1"))

# Politeness sleep between store fetches (uniform random in seconds, parsed
# as "min,max"). 2-5s is well under any documented Shopify rate limit and
# adds jitter so multiple stores don't form a synchronized burst.
_SHOPIFY_THROTTLE_RANGE = (
    float(os.environ.get("SHOPIFY_THROTTLE_MIN", "2")),
    float(os.environ.get("SHOPIFY_THROTTLE_MAX", "5")),
)


def _parse_shopify_stores(raw: str) -> list[dict]:
    """Parse SHOPIFY_STORES env (JSON string) into a validated list of
    {name, base_url, collection_handles} dicts. Returns [] on missing/
    malformed input — the caller treats [] as "use DEFAULT_SHOPIFY_STORES"."""
    if not raw or not raw.strip():
        return []
    try:
        parsed = _json.loads(raw)
    except (ValueError, TypeError) as e:
        print(f"[config] SHOPIFY_STORES JSON parse failed ({e}) — using defaults")
        return []
    if not isinstance(parsed, list) or not parsed:
        return []
    out = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        base_url = entry.get("base_url")
        if not (isinstance(name, str) and name.strip()
                and isinstance(base_url, str) and base_url.strip()):
            continue
        handles = entry.get("collection_handles") or []
        if not isinstance(handles, list):
            handles = []
        out.append({
            "name": name.strip(),
            "base_url": base_url.strip().rstrip("/"),
            "collection_handles": [str(h).strip() for h in handles if str(h).strip()],
        })
    return out


SHOPIFY_STORES = _parse_shopify_stores(os.environ.get("SHOPIFY_STORES", "")) or list(DEFAULT_SHOPIFY_STORES)
```

Then **edit the existing derived-constants block** (currently lines 244-252):

```python
SOURCE_WEBHOOKS = {
    "Woot": WOOT_WEBHOOK_URL,
    "Best Buy": BESTBUY_WEBHOOK_URL,
    "Shopify": SHOPIFY_WEBHOOK_URL,
}

# Fixed display order for the digest's per-source fields — sources with
# nothing posted this run are simply left out.
DIGEST_SOURCE_ORDER = ["Woot", "Best Buy", "Shopify"]
```

And **delete** the `STEAM_WEBHOOK_URL = ...` line (currently line 40) plus its 3-line comment.

---

## (b) Shopify adapter contract — `deal_bot/sources/shopify.py`

Complete file. Imports `transport` (shared retry), `config`, and `discount_percent` from `base.py` — same pattern as `woot.py` / `bestbuy.py`. Throttling uses `time.sleep(random.uniform(*config._SHOPIFY_THROTTLE_RANGE))` between stores; **never** inside a single response (one HTTP call per collection is the unit of throttling).

```python
"""Shopify deal source — config-driven list of mechanical-keyboard stores.

Most boutique keeb storefronts (KBDfans, NovelKeys, CannonKeys, Divinikey,
DailyClack, PrimeKB) are standard Shopify installs and expose a public
/products.json endpoint (and /collections/{handle}/products.json) with no
authentication. This source reads those, filters to real discounts
(compare_at_price > price, with the "0.00"/""/None sentinel normalized to
"no compare price"), and emits Deal dicts with source="Shopify" plus an
extra "store" key carrying the store name for the embed footer.

Read-only GETs go through the shared transport (transient blips retried
instead of losing the whole store for this run). Per-store failures are
isolated: one store's 404/HTML/timeout doesn't drop the others.

CLI for manual verification:
    python -m deal_bot.sources.shopify --dry-run
    python -m deal_bot.sources.shopify --dry-run --store KBDfans
"""

import argparse
import random
import time
from typing import Any

from deal_bot import config, transport
from deal_bot.sources.base import discount_percent

# Shopify caps page size at 250; one page per collection is the contract.
# Pagination across pages is intentionally NOT implemented yet — for the
# configured stores the first 250 newest products comfortably covers the
# active-discount surface; if a store grows past that, add Link-header
# traversal here rather than silently truncating.
_PAGE_LIMIT = 250


def _to_float(raw: Any) -> float | None:
    """Shopify returns prices as STRINGS (e.g. "79.99"). Some stores send
    "0.00" or "" as a sentinel for "no compare_at_price"; treat those the
    same as None. Returns None for anything that isn't a positive float —
    a price of 0 is not a real sale price, it's a freebie/glitch."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (ValueError, TypeError):
        return None
    if v <= 0:
        return None
    return v


def _normalize_product(product: dict, store_name: str, store_slug: str) -> dict | None:
    """Turn one Shopify product JSON object into a Deal dict, or None if
    it isn't a real discount (no compare_at_price, sold out, price<=0, etc).

    Multi-variant products: pick the cheapest AVAILABLE variant that has a
    real discount. If no variant is available, the product is sold out and
    we drop it (consistent with Woot's IsSoldOut filter). If multiple
    available variants have a discount, the cheapest-by-sale-price wins —
    that's the most attractive framing and the one the embed will show.
    """
    pid = product.get("id")
    handle = product.get("handle")
    title = product.get("title") or ""
    if pid is None or not handle or not title:
        return None

    variants = product.get("variants") or []
    if not isinstance(variants, list) or not variants:
        return None

    best: dict | None = None
    for v in variants:
        if not isinstance(v, dict):
            continue
        # Shopify variant "available" is a bool; absent = treat as unavailable
        if v.get("available") is False:
            continue
        sale = _to_float(v.get("price"))
        if sale is None or sale <= 0:
            continue
        list_price = _to_float(v.get("compare_at_price"))
        if list_price is None or list_price <= sale:
            # No real discount on this variant
            continue
        if best is None or sale < best["sale_price"]:
            best = {"sale_price": sale, "list_price": list_price}
    if best is None:
        return None

    images = product.get("images") or []
    image = images[0].get("src") if (isinstance(images, list) and images
                                     and isinstance(images[0], dict)) else None

    return {
        "id": f"shopify:{store_slug}:{pid}",
        "source": "Shopify",
        "title": f"{title} ({store_name})",
        "url": f"{config.SHOPIFY_STORE_BASE_URLS[store_slug]}/products/{handle}",
        "image": image,
        "list_price": best["list_price"],
        "sale_price": best["sale_price"],
        "discount_pct": discount_percent(best["list_price"], best["sale_price"]),
        "store": store_name,
    }


def fetch_shopify_store(store: dict) -> list[dict]:
    """Fetch one store's deals. Honors SHOPIFY_MAX_COLLECTIONS_PER_STORE by
    capping the number of collection endpoints we hit. If
    collection_handles is empty, fetches the store-root products.json
    (all products, newest first). Per-store failure is isolated: a 404,
    non-JSON, or transport-None returns [] and prints — it never raises."""
    name = store["name"]
    base_url = store["base_url"]
    slug = name.lower().replace(" ", "")
    handles = store.get("collection_handles") or []
    capped = handles[:max(0, config.SHOPIFY_MAX_COLLECTIONS_PER_STORE)]

    if capped:
        urls = [f"{base_url}/collections/{h}/products.json?limit={_PAGE_LIMIT}" for h in capped]
    else:
        urls = [f"{base_url}/products.json?limit={_PAGE_LIMIT}"]

    deals: list[dict] = []
    seen_ids: set[str] = set()
    for url in urls:
        resp = transport.request("GET", url, headers={"Accept": "application/json"}, timeout=20)
        if resp is None:
            print(f"[shopify:{name}] {url} failed after retries")
            continue
        if resp.status_code != 200:
            # A 404 here likely means a non-Shopify storefront or a typo'd
            # collection handle — log and move on, never raise.
            print(f"[shopify:{name}] {url} returned {resp.status_code}: {resp.text[:200]}")
            continue
        try:
            products = resp.json().get("products", [])
        except (ValueError, TypeError) as e:
            print(f"[shopify:{name}] {url} returned non-JSON ({e})")
            continue
        for product in products:
            deal = _normalize_product(product, name, slug)
            if deal is None:
                continue
            if deal["id"] in seen_ids:
                # Same product can appear in multiple collections of the
                # same store — dedup within the store before returning.
                continue
            seen_ids.add(deal["id"])
            deals.append(deal)
    return deals


def fetch_all_shopify_stores() -> list[dict]:
    """Fetch every configured store. Throttles between stores using
    SHOPIFY_THROTTLE_MIN/MAX (uniform random). One store's failure doesn't
    affect the others — each call is wrapped in try/except as a belt-and-
    suspenders guard against unexpected raises (transport already returns
    None rather than raising)."""
    if not config.SHOPIFY_STORES:
        return []
    all_deals: list[dict] = []
    for i, store in enumerate(config.SHOPIFY_STORES):
        if i > 0:
            time.sleep(random.uniform(*config._SHOPIFY_THROTTLE_RANGE))
        try:
            all_deals.extend(fetch_shopify_store(store))
        except Exception as e:
            # Defensive: a malformed store config or unexpected response
            # shape must not abort the whole source.
            print(f"[shopify:{store.get('name','?')}] unexpected error: {e}")
    return all_deals


# Lookup table built once at import (referenced by _normalize_product's URL
# construction). Kept as a module-global so tests can monkeypatch
# config.SHOPIFY_STORES and rebuild via _rebuild_store_base_urls().
def _rebuild_store_base_urls() -> dict[str, str]:
    return {s["name"].lower().replace(" ", ""): s["base_url"].rstrip("/") for s in config.SHOPIFY_STORES}


config.SHOPIFY_STORE_BASE_URLS = _rebuild_store_base_urls()


def main() -> None:
    parser = argparse.ArgumentParser(description="Shopify source dry-run")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and print deals, post nothing")
    parser.add_argument("--store", default=None,
                        help="only fetch this store name (case-sensitive)")
    args = parser.parse_args()
    if not args.dry_run:
        parser.error("only --dry-run is supported (this source never posts on its own)")

    if args.store:
        matches = [s for s in config.SHOPIFY_STORES if s["name"] == args.store]
        if not matches:
            print(f"no store named {args.store!r}; configured: {[s['name'] for s in config.SHOPIFY_STORES]}")
            return
        deals = fetch_shopify_store(matches[0])
    else:
        deals = fetch_all_shopify_stores()
    print(f"[shopify] {len(deals)} deal(s)")
    for d in deals:
        pct = f"{d['discount_pct']}%" if d["discount_pct"] is not None else "?"
        print(f"  {d['store']:14s} ${d['sale_price']:>7.2f} (was ${d['list_price']:>7.2f}, {pct} off) — {d['title'][:70]}")


if __name__ == "__main__":
    main()
```

**Notes for the implementer:**
- `source = "Shopify"` is the routing key — `SOURCE_WEBHOOKS["Shopify"]` must be set or `post_to_discord` will skip with the existing "no webhook URL set" path (no crash).
- The `store` extra key is read by the discord embed footer (contract (c) below).
- `_normalize_product` picks the **cheapest available variant with a discount**; if a product has 5 variants and only variant 3 is on sale, the embed shows variant 3's price. This matches Woot's `SalePrice.Minimum` semantics (cheapest price advertised).
- Throttling inserts **between stores**, never within a single store's collection list (those are sub-second and from the same host — Shopify handles them fine; cross-store is where politeness matters).
- `config.SHOPIFY_STORE_BASE_URLS` is a derived global built at import. Tests that monkeypatch `config.SHOPIFY_STORES` must call `shopify._rebuild_store_base_urls()` (or set `config.SHOPIFY_STORE_BASE_URLS` directly). The test contract below uses the direct-set form.

---

## (c) Pipeline wiring contract — `deal_bot/pipeline.py`

**One insertion, three deletions, one footer tweak. No core merge/dedup/gate logic changes.**

### Edit 1 — imports (after Phase 1 deletes Steam import at line 24)
Replace the deleted `from deal_bot.sources.steam import fetch_steam_specials` line with:

```python
from deal_bot.sources.shopify import fetch_all_shopify_stores
```

### Edit 2 — fetch call in `run_once()` (after Phase 1 deletes the `all_deals.extend(fetch_steam_specials())` line at 136)
Insert, in place of the deleted Steam line:

```python
        all_deals.extend(fetch_all_shopify_stores())
        # Throttling happens INSIDE fetch_all_shopify_stores (per-store sleep)
        # so no extra time.sleep here — the Woot/BestBuy sleeps above are
        # per-feed/per-search-term, which is a different unit.
```

The merge/dedup `all_deals = list({d["id"]: d for d in all_deals}.values())` at line 145 stays exactly as-is — Shopify IDs `shopify:{slug}:{pid}` are globally unique and never collide with `woot:`/`bestbuy:` prefixes.

### Edit 3 — Phase B spec-extraction simplification (lines 257-270)
Replace the entire block:

```python
    # ---- PHASE B — batched AI enrichment ----------------------------------
    # Spec extraction runs on every candidate (post-Steam simplification:
    # all sources have messy retail titles worth cleaning; Shopify product
    # titles in particular often carry SEO clutter like "PBT Keycap Set
    # Cherry Profile Dye-Sublimated KBDfans").
    if candidates:
        spec_results = extract_clean_specs_batch([d["title"] for d in candidates])
        for deal, result in zip(candidates, spec_results):
            deal["clean_title"] = result["clean_title"]
            deal["specs"] = result["specs"]
```

The `non_steam = [d for d in candidates if d["source"] != "Steam"]` filter and the second loop (lines 267-270) that assigns `clean_title = title; specs = []` for Steam are both deleted.

### Edit 4 — discord embed footer (Phase 4 leak fix)
In `deal_bot/integrations/discord.py:118`, change:

```python
        "footer": {"text": deal["source"]},
```

to:

```python
        "footer": {"text": deal.get("store") or deal["source"]},
```

For Woot/BestBuy deals (no `store` key), this still renders the source name. For Shopify deals, it renders the store name (e.g. "KBDfans"), which is what the user actually wants to see. **No other embed field changes** — the title still carries `({store})` for explicitness.

### What NOT to touch
- `_skip_reason` (lines 200-222) — discount floor, dollar floor, historical-low gate all apply to Shopify deals unchanged.
- `_enrich_with_price_history` (lines 181-197) — Shopify deals accumulate price_history like any other source.
- The post loop (lines 292-322) — `seen[deal["id"]]`, `upsert_seen_entry`, `record_posted_deal`, digest_stats, bluesky_candidates all key off `deal["source"]` / `deal["id"]` and work with `"Shopify"` unchanged.
- `_post_webhook`, `post_to_discord` retry logic — unchanged.
- Shadow-mode embeds (`build_shadow_classification_embed`, `build_quality_scorer_embed`, `build_categorizer_embed`) — unchanged; they read `d["source"]` and `d["title"]` which both work for Shopify.

---

## (d) Steam retirement contract — file-by-file

### Delete
- `deal_bot/sources/steam.py` (whole file, 49 lines).

### Edit `deal_bot/pipeline.py`
- Line 24: delete `from deal_bot.sources.steam import fetch_steam_specials`
- Line 136: delete `all_deals.extend(fetch_steam_specials())  # single request, no loop needed`
- Lines 257-270: replace with the simplified Phase B block in contract (c) Edit 3. This also deletes the comment block at 258-260 ("Spec extraction, Woot/Best Buy only. Steam titles are already clean...").

### Edit `deal_bot/config.py`
- Lines 38-40: delete the 3-line comment + `STEAM_WEBHOOK_URL = os.environ.get("STEAM_WEBHOOK_URL", "")`.
- Line 247 (`SOURCE_WEBHOOKS`): delete the `"Steam": STEAM_WEBHOOK_URL,` entry.
- Line 252 (`DIGEST_SOURCE_ORDER`): remove `"Steam"` from the list (final value: `["Woot", "Best Buy", "Shopify"]` per contract (a)).

### Edit `deal_bot/sources/__init__.py`
- Line 1 docstring: `"Deal sources — Woot, Best Buy, Steam. Each returns..."` → `"Deal sources — Woot, Best Buy, Shopify. Each returns..."`.

### Edit `deal_bot/__init__.py`
- Line 5: `"- sources: deal fetchers (Woot, Best Buy, Steam)"` → `"- sources: deal fetchers (Woot, Best Buy, Shopify)"`.
- Line 1: `"VoltDrop deal bot — finds, vets, and posts electronics/PC-parts deals."` → `"VoltDrop deal bot — finds, vets, and posts mechanical-keyboard deals."` (Phase 4 theme fix; same line).

### Edit `deal_bot/transport.py`
- Line 11: `"the read-only source GETs (Woot/Best Buy/Steam)."` → `"the read-only source GETs (Woot/Best Buy/Shopify)."`. Scope comment only; no behavior change.

### Edit `.env.example`
- Line 17: delete `STEAM_WEBHOOK_URL=`.
- Add (after `BESTBUY_WEBHOOK_URL=`):
  ```
  SHOPIFY_WEBHOOK_URL=
  ```
- Add (in the tuning-constants block at the bottom):
  ```
  # Shopify source — JSON list of {name, base_url, collection_handles}.
  # If unset, DEFAULT_SHOPIFY_STORES in config.py is used (KBDfans,
  # NovelKeys, CannonKeys, Divinikey, DailyClack, PrimeKB). In GitHub
  # Actions this is a repository Variable (not a Secret) — it's config,
  # not credentials.
  SHOPIFY_STORES=
  SHOPIFY_MAX_COLLECTIONS_PER_STORE=1
  SHOPIFY_THROTTLE_MIN=2
  SHOPIFY_THROTTLE_MAX=5
  ```

### Edit `.github/workflows/deal_bot.yml`
- Line 44: delete `STEAM_WEBHOOK_URL: ${{ secrets.STEAM_WEBHOOK_URL }}`.
- Add (after `BESTBUY_WEBHOOK_URL:`):
  ```yaml
          SHOPIFY_WEBHOOK_URL: ${{ secrets.SHOPIFY_WEBHOOK_URL }}
  ```
- Add (in the env block, near the other repo Variables):
  ```yaml
          SHOPIFY_STORES: ${{ vars.SHOPIFY_STORES }}
          SHOPIFY_MAX_COLLECTIONS_PER_STORE: ${{ vars.SHOPIFY_MAX_COLLECTIONS_PER_STORE }}
          SHOPIFY_THROTTLE_MIN: ${{ vars.SHOPIFY_THROTTLE_MIN }}
          SHOPIFY_THROTTLE_MAX: ${{ vars.SHOPIFY_THROTTLE_MAX }}
  ```

### Edit `tests/test_sources.py`
- Line 1 docstring: `"(woot/bestbuy/steam)"` → `"(woot/bestbuy/shopify)"`.
- Line 13: `from deal_bot.sources import bestbuy, steam, woot` → `from deal_bot.sources import bestbuy, shopify, woot`.
- Lines 119-141: delete the entire `SteamTests` class.

### Edit `tests/test_pipeline.py` (correction C1)
- `RunOnceBailTests.test_load_seen_none_bails_and_logs` (line 36-43): remove the `with patch("deal_bot.pipeline.fetch_steam_specials") as mock_steam:` wrapper and the `mock_steam` line; keep the `mock_woot` patch and assertions.
- `RunOnceBailTests.test_empty_seen_proceeds_to_feeds` (line 52-58): remove the `patch("deal_bot.pipeline.fetch_steam_specials", return_value=[])` line from the `with` chain.

### Edit `tests/test_spec_extraction.py` (correction C2)
- Lines 152-205: delete the entire `ProcessDealsSteamSkipTests` class.
- Add a replacement class (Phase 1 regression that the post-simplification spec-extraction branch still works for all sources):
  ```python
  class ProcessDealsSpecExtractionTests(unittest.TestCase):
      """Post-Steam-retirement regression: spec extraction runs on every
      candidate regardless of source (the old Steam-only skip branch is
      gone). Patches out side effects so this never touches real services."""
      def _make_deal(self, source: str) -> dict:
          return {
              "id": f"{source.lower()}:test-123", "source": source,
              "title": "Some Raw Messy Title", "url": "https://example.com/deal",
              "image": None, "sale_price": 20.0, "list_price": 40.0, "discount_pct": 50.0,
          }
      def _run(self, deals):
          stats = {"new_count": 0, "skipped_already_seen": 0, "skipped_no_better_price": 0,
                   "skipped_below_threshold": 0, "skipped_not_near_historical_low": 0,
                   "skipped_not_desirable": 0, "digest_sent": False, "shadow_sent": False}
          digest_stats = {s: {"count": 0, "total_savings": 0.0, "best": None} for s in config.DIGEST_SOURCE_ORDER}
          saved = (config.SHADOW_CLASSIFIER_WEBHOOK_URL,
                   config.SHADOW_QUALITY_SCORER_WEBHOOK_URL,
                   config.SHADOW_CATEGORIZER_WEBHOOK_URL)
          config.SHADOW_CLASSIFIER_WEBHOOK_URL = ""
          config.SHADOW_QUALITY_SCORER_WEBHOOK_URL = ""
          config.SHADOW_CATEGORIZER_WEBHOOK_URL = ""
          try:
              pipeline._process_deals(deals, seen={}, digest_stats=digest_stats,
                                      stats=stats, history_map={})
          finally:
              (config.SHADOW_CLASSIFIER_WEBHOOK_URL,
               config.SHADOW_QUALITY_SCORER_WEBHOOK_URL,
               config.SHADOW_CATEGORIZER_WEBHOOK_URL) = saved
      @patch("deal_bot.pipeline.build_verdicts_batch", return_value=[{"caption": "", "analysis": ""}, {"caption": "", "analysis": ""}])
      @patch("deal_bot.pipeline.prune_seen")
      @patch("deal_bot.pipeline.post_to_discord", return_value=False)
      @patch("deal_bot.pipeline.extract_clean_specs_batch")
      def test_spec_extraction_called_for_all_sources(self, mock_extract, _post, _prune, _verdicts):
          mock_extract.return_value = [{"clean_title": "T", "specs": []}, {"clean_title": "T", "specs": []}]
          self._run([self._make_deal("Woot"), self._make_deal("Shopify")])
          mock_extract.assert_called_once_with(["Some Raw Messy Title", "Some Raw Messy Title"])
  ```

---

## (e) AI prompt contract — 10 prompts, keeb-themed

For each: same JSON output schema (where applicable), same character limits, same fail-open semantics. The implementer must NOT touch the validation code in `verdicts.py:_validate_item`, `spec_extraction.py:_validate_result`, `deal_analyst.py:_parse_items`, `classifier.py:_parse_keep_drop`, `deal_scorer.py:_extract_scores`, `categorizer.py:_extract_categories`, `weekly_digest.build_weekly_digest` — only the prompt strings change.

### Prompt 1 — `config.OPENROUTER_CAPTION_SYSTEM_PROMPT` (config.py:87-96)
**Replace the entire triple-quoted string.** Same 140-char limit, same "no URLs / no markdown / 2-4 hashtags" rules.

```
You write short, data-backed technical verdicts for a deal-finding bot aimed at mechanical-keyboard enthusiasts — not marketing copy. You'll be given a product's clean title, its known specs (if any), current and list price, and price-history context (whether this is a new all-time low, or what the lowest tracked price has been).

Output ONLY the verdict text — no preamble, no explanation, no quotation marks, no markdown formatting, no code fences.

Write exactly 1-2 concise sentences explaining *why* this deal is actually noteworthy — a real price-history signal (e.g. a genuine all-time low), real value-for-money given the specs you were given, or a specific use case those specs support. Take a direct, analytical, enthusiast tone. Do not use hype phrases like "insane deal," "don't miss out," or "act now." Never state a spec, switch feel, or feature that wasn't explicitly given to you — if you don't have enough information to say something specific and true, keep it simple rather than inventing detail.

Keep the entire output under 140 characters (including hashtags). End with 2 to 4 relevant, space-separated hashtags chosen specifically for this item — vary them based on what the deal actually is, don't reuse the same generic tags every time. Never include a URL or link.

Example, given a GMK Noah keycap set at a new all-time low of $89.99 (was $134.99):
Lowest we've tracked this GMK Noah set — a genuine all-time low, not just a markdown. In-stock GMK at a real floor price. #MechKeys #Keycaps #KeebDeals
```

### Prompt 2 — `config.OPENROUTER_ANALYSIS_SYSTEM_PROMPT` (config.py:101-109)
**Replace the entire string.** Same 350-char limit.

```
You write short expert analysis for a deal-finding bot aimed at mechanical-keyboard enthusiasts. Given a product's clean title, known specs (if any), current and list price, and price-history context, write 2-3 concise sentences explaining what makes this deal genuinely noteworthy:

- What kind of build or use case it fits (e.g. a tactile-switch daily driver, a GMK set for a 65% build, a hot-swap barebones kit for a first custom).
- Whether the price is strong for the specs given, and what it competes against at that price point.
- Which specific spec(s) actually matter for that use case.

Take a direct, analytical, enthusiast tone. Do not use hype phrases like "insane deal" or "act now." Never state a spec, switch feel, or competitor price that wasn't explicitly given to you — if you don't have enough information to say something specific and true, keep it simple rather than inventing detail.

Output ONLY the analysis text — no preamble, no markdown, no quotation marks, no hashtags, no URL. Keep the entire output under 350 characters.
```

### Prompt 3 — `config.OPENROUTER_WEEKLY_DIGEST_SYSTEM_PROMPT` (config.py:116-118)
**Replace the entire string.** Same plain-text-only / no-markdown rules.

```
You write a weekly roundup for a deal-finding bot aimed at mechanical-keyboard enthusiasts. You'll be given a list of the week's posted deals (title, source, sale price, list price, and discount). Pick the top 3-5 most noteworthy and write a short, punchy summary of each: what it is, who it's for, and why the price stood out. Use a direct, analytical, enthusiast tone — no hype phrases like "insane" or "don't miss out." Never state a spec, switch feel, or price that isn't in the input.

Output plain text only — no markdown, no hashtags, no URL. Start with a one-line intro (e.g. "This week's best mechanical-keyboard deals:"). Keep each deal summary to 1-2 sentences. End with a one-line sign-off.
```

### Prompt 4 — `config.OPENROUTER_CLASSIFIER_SYSTEM_PROMPT` (config.py:120-124)
**Replace the entire string.** Same JSON shape `{"items": ["KEEP"|"DROP", ...]}`, same KEEP/DROP vocabulary, same length-must-match rule.

```
You screen deal listings for a bot that posts discounts to an audience of mechanical-keyboard enthusiasts. For each numbered item below, decide whether it is something that audience would genuinely want — not just topically related (e.g. "tech accessory"), but actually desirable: keyboards, keycap sets, switches, barebones kits, PCBs, cases, plates, stabilizers, deskmats, and recognized keeb-community brands (KBDfans, NovelKeys, CannonKeys, GMK, ePBT, DOMIKEY, Drop, Rama, Keychron, etc.). Reject generic peripherals (mice, headsets), non-keyboard PC parts, apparel, and off-brand or low-interest items even if they're topically in-category.

Respond with ONLY a JSON object in this exact shape, with EXACTLY one string per input item, in the same order:
{"items": ["KEEP", "DROP", ...]}
Each string must be exactly the word KEEP or the word DROP — nothing else. The number of items must exactly match the number of input lines.
```

### Prompt 5 — `config.OPENROUTER_QUALITY_SCORER_SYSTEM_PROMPT` (config.py:142-144)
**Replace the entire string.** Same one-integer-per-line / 1-10 / length-must-match rules.

```
You score deal listings for a bot that posts discounts to an audience of mechanical-keyboard enthusiasts. For each numbered item below, rate how genuinely desirable it is to that audience on a scale of 1 to 10, where 10 is a must-buy and 1 is generic/off-brand junk. Consider: recognizable brand in the keeb community, real spec-to-price value, and whether it is a genuine keyboard/switch/keycap product rather than something merely topically in-category (e.g. a no-name cable, a generic mousepad, an off-brand switch puller).

Respond with exactly one line per item, in the same order as the input. Each line must be a single integer from 1 to 10 — nothing else. No numbering, no explanation, no extra text. The number of output lines must exactly match the number of input items.
```

### Prompt 6 — `config.OPENROUTER_CATEGORIZER_SYSTEM_PROMPT` (config.py:153-162)
**Replace the entire string.** Categories match the new `DEAL_CATEGORIES` from contract (a).

```
You classify deal listings for a bot aimed at mechanical-keyboard enthusiasts. For each numbered item below, assign exactly one category from this list:

- board: full keyboards, barebones kits, keyboard cases, PCBs, plates (complete or near-complete keyboard products)
- switch: switches (linear, tactile, clicky), switch testers, switch films, switch openers
- keycaps: keycap sets, artisan keycaps, novelty caps
- accessory: deskmats, wrist rests, stabilizers, tools, cables, lube, switch pullers, keycap pullers, o-rings
- other: anything that doesn't fit the above

Respond with exactly one line per item, in the same order as the input, each line being a single category word from the list — nothing else. No numbering, no explanation, no extra text. The number of output lines must exactly match the number of input items.
```

### Prompt 7 — `config.SPEC_EXTRACTION_SYSTEM_PROMPT` (config.py:170-177)
**Replace the entire string.** Same `{"clean_title": string, "specs": [string, ...]}` schema, same 100/60-char limits, same anti-hallucination rule.

```
You clean up messy retail product titles for a deal-finding bot focused on mechanical-keyboard products. Given a raw title (and optional description), extract a clean, concise product name and up to 4 short technical specs.

Rules:
- Never invent a spec that isn't explicitly present or clearly implied in the input. If there is genuinely nothing worth calling out, return an empty specs list — do not pad it with anything invented.
- clean_title: the product name and model, stripped of SEO keyword clutter, under 100 characters.
- specs: 0 to 4 short strings (e.g. "Profile: Cherry", "Switches: Gateron Yellow", "Layout: 65%"), each under 60 characters.

Respond with only a JSON object in this exact shape: {"clean_title": string, "specs": [string, ...]}
```

### Prompt 8 — `_VERDICTS_SYSTEM_PROMPT` (verdicts.py:29-38)
**Replace the entire string.** Same `{"items": [{"caption": string, "analysis": string}, ...]}` schema, same per-item caption budget, same 350-char analysis limit.

```
You write two pieces of output for each deal in a numbered list, for a deal-finding bot aimed at mechanical-keyboard enthusiasts. You'll be given each deal's source, item, known specs, discount, price, and optional price-history/value-metric context, plus a per-item character limit for the caption.

For each item write:
- "caption": 1-2 concise sentences explaining *why* this deal is actually noteworthy — a real price-history signal, real value-for-money given the specs given, or a specific use case those specs support. End with 2 to 4 relevant, space-separated hashtags chosen specifically for this item. Stay under that item's stated caption character limit (the limit includes the hashtags; the link is added automatically, so never include a URL).
- "analysis": 2-3 concise sentences on what build or use case the item fits, whether the price is strong for the specs given, and which spec(s) matter for that use case. Under 350 characters.

Take a direct, analytical, enthusiast tone for both. Do not use hype phrases like "insane deal" or "act now." Never state a spec, switch feel, number, or price that wasn't explicitly given — if you don't have enough information to say something specific and true, keep it simple rather than inventing detail.

Respond with ONLY a JSON object in this exact shape, with EXACTLY one item per input line, in the same order:
{"items": [{"caption": string, "analysis": string}, ...]}
```

### Prompt 9 — `_BATCH_ANALYSIS_SYSTEM_PROMPT` (deal_analyst.py:17-22)
**Replace the entire string.** Same `{"items": ["analysis for item 1", ...]}` schema, same 350-char limit.

```
You write short expert analysis for a deal-finding bot aimed at mechanical-keyboard enthusiasts. You'll be given a numbered list of deals, each with its source, item, known specs, discount, price, and optional price-history context. For each, write 2-3 concise sentences explaining what makes it genuinely noteworthy: what build or use case it fits, whether the price is strong for the specs given, and which spec(s) actually matter for that use case.

Take a direct, analytical, enthusiast tone. Do not use hype phrases like "insane deal" or "act now." Never state a spec, switch feel, or competitor price that wasn't explicitly given. Keep each item's analysis under 350 characters.

Respond with ONLY a JSON object in this exact shape, with EXACTLY one string per input item, in the same order:
{"items": ["analysis for item 1", "analysis for item 2", ...]}
```

### Prompt 10 — `_BATCH_SPEC_SYSTEM_PROMPT` (spec_extraction.py:21-29)
**Replace the entire string.** Same `{"items": [{"clean_title": string, "specs": [string, ...]}, ...]}` schema, same 100/60-char limits.

```
You clean up messy retail product titles for a deal-finding bot focused on mechanical-keyboard products. You'll be given a numbered list of raw titles. For each, extract a clean, concise product name and up to 4 short technical specs.

Rules:
- Never invent a spec that isn't explicitly present or clearly implied in the input. If there is genuinely nothing worth calling out, use an empty specs list.
- clean_title: under 100 characters.
- specs: 0 to 4 short strings, each under 60 characters (e.g. "Profile: Cherry", "Switches: Gateron Yellow").

Respond with only a JSON object in this exact shape, with EXACTLY one item per input line, in the same order:
{"items": [{"clean_title": string, "specs": [string, ...]}, ...]}
```

---

## (f) Test contract — `tests/test_shopify.py`

Mirror `tests/test_sources.py` style: stdlib `unittest` + `Mock` + `@patch("deal_bot.sources.shopify.transport.request")`. File header:

```python
"""Tests for the Shopify source (sources/shopify.py) — URL construction,
response mapping, compare_at_price filtering, multi-variant selection,
and transport integration. Stdlib only; every HTTP call is mocked at the
shared transport boundary (same pattern as test_sources.py)."""
import json as _json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config
from deal_bot.sources import shopify


def _resp(status: int = 200, payload=None, text="") -> Mock:
    resp = Mock()
    resp.status_code = status
    resp.text = text or f"status {status}"
    resp.json.return_value = payload if payload is not None else {}
    return resp


def _product(pid=1, title="Test Keycaps", handle="test-keycaps", price="89.99",
             compare_at="134.99", available=True, image="https://img/x.jpg"):
    return {
        "id": pid, "title": title, "handle": handle,
        "images": [{"src": image}] if image else [],
        "variants": [{"price": price, "compare_at_price": compare_at, "available": available}],
    }


class NormalizeProductTests(unittest.TestCase):
    def _store(self):
        return {"name": "KBDfans", "base_url": "https://kbdfans.com", "collection_handles": []}

    def setUp(self):
        # _normalize_product reads config.SHOPIFY_STORE_BASE_URLS for the
        # URL construction — pin a known-good mapping for these tests.
        self._orig = getattr(config, "SHOPIFY_STORE_BASE_URLS", None)
        config.SHOPIFY_STORE_BASE_URLS = {"kbdfans": "https://kbdfans.com"}

    def tearDown(self):
        if self._orig is not None:
            config.SHOPIFY_STORE_BASE_URLS = self._orig

    def test_real_discount_maps_to_deal(self):
        deal = shopify._normalize_product(_product(), "KBDfans", "kbdfans")
        self.assertIsNotNone(deal)
        self.assertEqual(deal["id"], "shopify:kbdfans:1")
        self.assertEqual(deal["source"], "Shopify")
        self.assertEqual(deal["sale_price"], 89.99)
        self.assertEqual(deal["list_price"], 134.99)
        self.assertAlmostEqual(deal["discount_pct"], 33.3)
        self.assertEqual(deal["store"], "KBDfans")
        self.assertEqual(deal["title"], "Test Keycaps (KBDfans)")
        self.assertEqual(deal["url"], "https://kbdfans.com/products/test-keycaps")
        self.assertEqual(deal["image"], "https://img/x.jpg")

    def test_compare_at_zero_string_is_not_a_discount(self):
        # Shopify sentinel "0.00" means "no compare price" — must NOT
        # count as a free / 100%-off deal.
        deal = shopify._normalize_product(
            _product(compare_at="0.00"), "KBDfans", "kbdfans")
        self.assertIsNone(deal)

    def test_compare_at_empty_string_is_not_a_discount(self):
        deal = shopify._normalize_product(
            _product(compare_at=""), "KBDfans", "kbdfans")
        self.assertIsNone(deal)

    def test_compare_at_none_is_not_a_discount(self):
        deal = shopify._normalize_product(
            _product(compare_at=None), "KBDfans", "kbdfans")
        self.assertIsNone(deal)

    def test_compare_at_equal_to_price_is_not_a_discount(self):
        # No actual markdown — compare_at == price means "not on sale."
        deal = shopify._normalize_product(
            _product(price="89.99", compare_at="89.99"), "KBDfans", "kbdfans")
        self.assertIsNone(deal)

    def test_compare_at_below_price_is_not_a_discount(self):
        # Compare_at less than price is a Shopify data glitch, not a deal.
        deal = shopify._normalize_product(
            _product(price="89.99", compare_at="50.00"), "KBDfans", "kbdfans")
        self.assertIsNone(deal)

    def test_price_zero_is_dropped(self):
        # A free product is a glitch, not a deal.
        deal = shopify._normalize_product(
            _product(price="0.00", compare_at="100.00"), "KBDfans", "kbdfans")
        self.assertIsNone(deal)

    def test_sold_out_variant_is_dropped(self):
        deal = shopify._normalize_product(
            _product(available=False), "KBDfans", "kbdfans")
        self.assertIsNone(deal)

    def test_multi_variant_picks_cheapest_available_with_discount(self):
        # Two available variants, both discounted — cheaper one wins.
        product = _product()
        product["variants"] = [
            {"price": "120.00", "compare_at_price": "200.00", "available": True},
            {"price": "80.00",  "compare_at_price": "150.00", "available": True},
        ]
        deal = shopify._normalize_product(product, "KBDfans", "kbdfans")
        self.assertEqual(deal["sale_price"], 80.00)
        self.assertEqual(deal["list_price"], 150.00)

    def test_multi_variant_skips_unavailable_then_picks_cheapest(self):
        # Variant 1 is the cheapest but sold out — must skip to variant 2.
        product = _product()
        product["variants"] = [
            {"price": "50.00",  "compare_at_price": "100.00", "available": False},
            {"price": "90.00",  "compare_at_price": "180.00", "available": True},
        ]
        deal = shopify._normalize_product(product, "KBDfans", "kbdfans")
        self.assertEqual(deal["sale_price"], 90.00)

    def test_multi_variant_all_unavailable_drops_product(self):
        product = _product()
        product["variants"] = [
            {"price": "50.00", "compare_at_price": "100.00", "available": False},
            {"price": "90.00", "compare_at_price": "180.00", "available": False},
        ]
        self.assertIsNone(shopify._normalize_product(product, "KBDfans", "kbdfans"))

    def test_missing_id_returns_none(self):
        p = _product()
        p.pop("id")
        self.assertIsNone(shopify._normalize_product(p, "KBDfans", "kbdfans"))

    def test_missing_handle_returns_none(self):
        p = _product()
        p.pop("handle")
        self.assertIsNone(shopify._normalize_product(p, "KBDfans", "kbdfans"))

    def test_no_images_yields_none_image(self):
        deal = shopify._normalize_product(_product(image=None), "KBDfans", "kbdfans")
        self.assertIsNone(deal["image"])

    def test_no_variants_returns_none(self):
        p = _product()
        p["variants"] = []
        self.assertIsNone(shopify._normalize_product(p, "KBDfans", "kbdfans"))


class FetchShopifyStoreTests(unittest.TestCase):
    def setUp(self):
        self._orig = getattr(config, "SHOPIFY_STORE_BASE_URLS", None)
        config.SHOPIFY_STORE_BASE_URLS = {"kbdfans": "https://kbdfans.com"}
        self._orig_max = config.SHOPIFY_MAX_COLLECTIONS_PER_STORE
        config.SHOPIFY_MAX_COLLECTIONS_PER_STORE = 1

    def tearDown(self):
        if self._orig is not None:
            config.SHOPIFY_STORE_BASE_URLS = self._orig
        config.SHOPIFY_MAX_COLLECTIONS_PER_STORE = self._orig_max

    @patch("deal_bot.sources.shopify.transport.request")
    def test_store_root_products_json_when_no_handles(self, mock_req):
        mock_req.return_value = _resp(payload={"products": [_product()]})
        store = {"name": "KBDfans", "base_url": "https://kbdfans.com", "collection_handles": []}
        deals = shopify.fetch_shopify_store(store)
        url = mock_req.call_args.args[1]
        self.assertEqual(url, "https://kbdfans.com/products.json?limit=250")
        self.assertEqual(len(deals), 1)

    @patch("deal_bot.sources.shopify.transport.request")
    def test_collection_endpoint_when_handles_present(self, mock_req):
        mock_req.return_value = _resp(payload={"products": []})
        store = {"name": "KBDfans", "base_url": "https://kbdfans.com",
                 "collection_handles": ["keyboards", "switches"]}
        shopify.fetch_shopify_store(store)
        # SHOPIFY_MAX_COLLECTIONS_PER_STORE=1 caps to the first handle.
        url = mock_req.call_args.args[1]
        self.assertEqual(url, "https://kbdfans.com/collections/keyboards/products.json?limit=250")
        self.assertEqual(mock_req.call_count, 1)

    @patch("deal_bot.sources.shopify.transport.request")
    def test_dedup_within_store_when_product_in_two_collections(self, mock_req):
        # Same product returned by two collection fetches — must dedup.
        mock_req.side_effect = [
            _resp(payload={"products": [_product(pid=42)]}),
            _resp(payload={"products": [_product(pid=42)]}),
        ]
        config.SHOPIFY_MAX_COLLECTIONS_PER_STORE = 2
        store = {"name": "KBDfans", "base_url": "https://kbdfans.com",
                 "collection_handles": ["keyboards", "switches"]}
        deals = shopify.fetch_shopify_store(store)
        self.assertEqual(len(deals), 1)

    @patch("deal_bot.sources.shopify.transport.request")
    def test_transport_none_returns_empty(self, mock_req):
        mock_req.return_value = None
        store = {"name": "KBDfans", "base_url": "https://kbdfans.com", "collection_handles": []}
        self.assertEqual(shopify.fetch_shopify_store(store), [])

    @patch("deal_bot.sources.shopify.transport.request")
    def test_non_200_returns_empty(self, mock_req):
        mock_req.return_value = _resp(status=404, text="not found")
        store = {"name": "Kinetic", "base_url": "https://kineticlabs.com", "collection_handles": []}
        self.assertEqual(shopify.fetch_shopify_store(store), [])

    @patch("deal_bot.sources.shopify.transport.request")
    def test_non_json_body_returns_empty(self, mock_req):
        # A non-Shopify storefront returns HTML; resp.json() must not crash.
        resp = Mock()
        resp.status_code = 200
        resp.text = "<html>not shopify</html>"
        resp.json.side_effect = ValueError("not json")
        mock_req.return_value = resp
        store = {"name": "KBDfans", "base_url": "https://kbdfans.com", "collection_handles": []}
        self.assertEqual(shopify.fetch_shopify_store(store), [])

    @patch("deal_bot.sources.shopify.transport.request")
    def test_one_collection_failure_does_not_kill_others(self, mock_req):
        # First collection 404s; second returns a product.
        mock_req.side_effect = [
            _resp(status=404, text="nope"),
            _resp(payload={"products": [_product()]}),
        ]
        config.SHOPIFY_MAX_COLLECTIONS_PER_STORE = 2
        store = {"name": "KBDfans", "base_url": "https://kbdfans.com",
                 "collection_handles": ["badhandle", "keyboards"]}
        deals = shopify.fetch_shopify_store(store)
        self.assertEqual(len(deals), 1)


class FetchAllShopifyStoresTests(unittest.TestCase):
    def setUp(self):
        self._orig_stores = config.SHOPIFY_STORES
        self._orig_bases = getattr(config, "SHOPIFY_STORE_BASE_URLS", None)
        self._orig_range = config._SHOPIFY_THROTTLE_RANGE
        config._SHOPIFY_THROTTLE_RANGE = (0.0, 0.0)  # no sleeping in tests
        config.SHOPIFY_STORE_BASE_URLS = {"kbdfans": "https://kbdfans.com", "nk": "https://novelkeys.com"}

    def tearDown(self):
        config.SHOPIFY_STORES = self._orig_stores
        config._SHOPIFY_THROTTLE_RANGE = self._orig_range
        if self._orig_bases is not None:
            config.SHOPIFY_STORE_BASE_URLS = self._orig_bases

    @patch("deal_bot.sources.shopify.time.sleep")
    @patch("deal_bot.sources.shopify.fetch_shopify_store")
    def test_empty_store_list_returns_empty(self, mock_fetch, mock_sleep):
        config.SHOPIFY_STORES = []
        self.assertEqual(shopify.fetch_all_shopify_stores(), [])
        mock_fetch.assert_not_called()
        mock_sleep.assert_not_called()

    @patch("deal_bot.sources.shopify.time.sleep")
    @patch("deal_bot.sources.shopify.fetch_shopify_store")
    def test_throttles_between_stores_not_before_first(self, mock_fetch, mock_sleep):
        mock_fetch.return_value = []
        config.SHOPIFY_STORES = [
            {"name": "KBDfans", "base_url": "https://kbdfans.com", "collection_handles": []},
            {"name": "NovelKeys", "base_url": "https://novelkeys.com", "collection_handles": []},
        ]
        shopify.fetch_all_shopify_stores()
        self.assertEqual(mock_fetch.call_count, 2)
        # Sleep called exactly once (between the two stores, not before the first).
        self.assertEqual(mock_sleep.call_count, 1)

    @patch("deal_bot.sources.shopify.time.sleep")
    @patch("deal_bot.sources.shopify.fetch_shopify_store")
    def test_one_store_raising_does_not_abort_others(self, mock_fetch, mock_sleep):
        # Defensive: transport already returns None rather than raising, but
        # an unexpected exception in fetch_shopify_store must not propagate.
        mock_fetch.side_effect = [RuntimeError("boom"), [_product()]]
        config.SHOPIFY_STORES = [
            {"name": "KBDfans", "base_url": "https://kbdfans.com", "collection_handles": []},
            {"name": "NovelKeys", "base_url": "https://novelkeys.com", "collection_handles": []},
        ]
        deals = shopify.fetch_all_shopify_stores()
        self.assertEqual(len(deals), 1)


class ConfigParseTests(unittest.TestCase):
    def test_parse_shopify_stores_valid_json(self):
        raw = '[{"name":"X","base_url":"https://x.com","collection_handles":["a"]}]'
        result = config._parse_shopify_stores(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "X")
        self.assertEqual(result[0]["base_url"], "https://x.com")
        self.assertEqual(result[0]["collection_handles"], ["a"])

    def test_parse_shopify_stores_trailing_slash_stripped(self):
        raw = '[{"name":"X","base_url":"https://x.com/","collection_handles":[]}]'
        result = config._parse_shopify_stores(raw)
        self.assertEqual(result[0]["base_url"], "https://x.com")

    def test_parse_shopify_stores_empty_string_returns_empty(self):
        self.assertEqual(config._parse_shopify_stores(""), [])
        self.assertEqual(config._parse_shopify_stores("   "), [])

    def test_parse_shopify_stores_malformed_json_returns_empty(self):
        self.assertEqual(config._parse_shopify_stores("not json"), [])

    def test_parse_shopify_stores_skips_entries_missing_required_fields(self):
        raw = '[{"name":"X","base_url":"https://x.com"},{"name":"","base_url":"https://y.com"},{"name":"Z"}]'
        result = config._parse_shopify_stores(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "X")

    def test_parse_shopify_stores_non_list_returns_empty(self):
        self.assertEqual(config._parse_shopify_stores('{"not":"a list"}'), [])

    def test_parse_shopify_stores_handles_non_list_field(self):
        # collection_handles that isn't a list must be coerced to [], not crash.
        raw = '[{"name":"X","base_url":"https://x.com","collection_handles":"not-a-list"}]'
        result = config._parse_shopify_stores(raw)
        self.assertEqual(result[0]["collection_handles"], [])


if __name__ == "__main__":
    unittest.main()
```

### Existing tests that break (summary of corrections C1-C3)
- **`tests/test_pipeline.py`** — apply correction C1 (drop `fetch_steam_specials` patches).
- **`tests/test_spec_extraction.py`** — apply correction C2 (delete `ProcessDealsSteamSkipTests`, add `ProcessDealsSpecExtractionTests`).
- **`tests/test_categorizer.py`** — apply correction C3 (rewrite every fixture category token to the new `board/switch/keycaps/accessory/other` vocabulary).
- **`tests/test_sources.py`** — drop the `steam` import and `SteamTests` class (Phase 1).

### Tests that need a SWEEP but likely pass
- `tests/test_classifier.py`, `tests/test_deal_scorer.py`, `tests/test_deal_verdict.py`, `tests/test_deal_analyst.py`, `tests/test_verdicts.py` — these mock `_call_openrouter` and assert on parsed output, not on prompt text. **Action for the implementer:** grep each file for literal `"PC"`, `"gaming"`, `"storage"`, `"component"`, `"peripheral"`, `"display"` in fixture strings and assert-no-occurrence lines; rewrite any matches to the keeb vocabulary. The validation logic itself is unchanged.

---

# 3. Risks / additional contracts the original plan missed

### R1 — Pagination (MEDIUM)
Shopify caps `/products.json` at 30 default, 250 with `?limit=250`. The contract above uses `?limit=250` and explicitly does NOT paginate further. For KBDfans this likely under-fetches (their catalog is larger). **Mitigation:** add a future `Link` header traversal in `fetch_shopify_store` if a store's first-page response includes `rel="next"`. Flagging as known-coverage-gap; the contract makes the gap explicit (not silent truncation).

### R2 — `seen_deals` source column (LOW)
No Supabase schema migration needed — `seen_deals.source`, `posted_deals.source` are free-text and accept `"Shopify"` unchanged. The `upsert_seen_entry(deal["id"], deal["source"], ...)` call in `pipeline.py:300` works as-is.

### R3 — Cross-store duplicate products (LOW)
Two stores selling the same keycap set (e.g. KBDfans and CannonKeys both listing GMK Noah) produce different `id`s (`shopify:kbdfans:123` vs `shopify:cannonkeys:456`) — both will post. Acceptable for now (different stores, different listings, different shipping) but the user may later want a title-based fuzzy dedup. **Out of scope** for this refactor; flagged.

### R4 — `config.SHOPIFY_STORE_BASE_URLS` derived-global gotcha (LOW)
The Shopify adapter builds a `slug → base_url` lookup at import time. Tests that monkeypatch `config.SHOPIFY_STORES` after import must also reset `config.SHOPIFY_STORE_BASE_URLS` (the test contract above does this in `setUp`/`tearDown`). The implementer must NOT remove this lookup — `_normalize_product` needs it for URL construction and can't re-read `config.SHOPIFY_STORES` on every call (perf + the slug derivation is deterministic per store name).

### R5 — OpenRouter prompt re-theming could shift model output lengths (LOW)
The new prompts are slightly different lengths and emphasize different examples. Validation caps (140 / 350 / 100 / 60 chars, JSON shapes, line counts) are unchanged and enforced in code, so a longer model output still fails open to the fallback. **No action needed** beyond confirming the existing tests still pass after the prompt edits — they mock the LLM, so they will.

### R6 — WOOT_EXCLUDE_KEYWORDS is aggressive (MEDIUM, user-acknowledged)
The contract's `WOOT_EXCLUDE_KEYWORDS` adds `mouse`, `mice`, `combo`, `bundle`, `headset`, `webcam`, `monitor`, `laptop`, `gpu`, etc. — these are the entire PC-builds/gaming vocabulary the bot used to surface. Combined with `WOOT_INCLUDE_KEYWORDS` requiring a keeb term, Woot's effective throughput drops dramatically. **This is the user's stated intent** (avoid accessory flooding), but the implementer should flag in the PR description that Woot/BestBuy volume will be much lower post-refactor — the user might want to tune the lists after the first real run.

### R7 — `WOOT_EXCLUDE_CATEGORIES` (LOW, unchanged)
`config.WOOT_EXCLUDE_CATEGORIES` (lines 212-215) excludes `HOME`, `TOOLS`, `APPAREL`, etc. These are still valid for a keeb-focused bot (a `HOME` category Woot deal is never a keyboard). **No change needed** — the plan didn't mention this list and that's correct; leave it.

### R8 — `weekly_digest.py` seed names need keeb-themed replacements (Phase 4)
Replace the 7 seed names at `weekly_digest.py:116-119` with keeb-themed ones, e.g.:
```python
names = [
    "GMK Noah Keycap Set", "KBDfans Maja PBT Keycaps",
    "CannonKeys Brutalist v2 65% Barebones Kit", "Gateron Yellow Switches (110 pack)",
    "NovelKeys Cream Tactile Switches", "Divinikey PBT Dye-Sub Keycaps",
    "PrimeKB Plate-Mount Stabilizers",
]
```
The `source` field for seeded rows (`weekly_digest.py:128`) currently alternates `"Woot"` / `"Best Buy"` — add `"Shopify"` to the rotation or leave as-is (seeds are testing-only; either works).

### R9 — Secret introduction (CONFIRMED NONE)
No new secrets are introduced. `SHOPIFY_WEBHOOK_URL` is a Discord webhook URL (same shape as the existing `WOOT_WEBHOOK_URL` / `BESTBUY_WEBHOOK_URL` — a Discord secret). `SHOPIFY_STORES` is config (a GitHub Actions **Variable**, not a Secret). `SHOPIFY_THROTTLE_MIN/MAX` and `SHOPIFY_MAX_COLLECTIONS_PER_STORE` are tuning Variables. No Shopify API key is needed (the public `/products.json` endpoint requires no auth). `.env` stays untracked.

### R10 — `deal_bot.yml` `if: always()` interaction with the test step (LOW)
The workflow's `Run deal_bot` step has `if: always()` and runs even if tests fail. If a test regression slips through (e.g. the Phase 1 corrections aren't applied), the production run will still execute and may crash on the missing `fetch_steam_specials` import. **Mitigation:** the Phase 1 / C1 / C2 corrections must be in the SAME PR as the Steam deletion — never split them across commits.

---

# Verdict

**The plan is sound in structure and direction.** Eight concrete corrections are required before implementation (C1-C8 above); the most important are C1, C2, C3 (test breakages the plan's Phase 8 didn't enumerate), C5 (collection_handles vs store-root fetch design ambiguity), and C6 (pagination coverage gap). Apply those corrections, implement the contracts verbatim, and the refactor preserves every constraint: pipeline core merge/dedup/gate logic untouched, shadow mode stays non-gating and fail-open, no new secrets, no Supabase schema migration.
</task_result>
</task>