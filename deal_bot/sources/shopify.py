"""Shopify deal source.

Pulls deals from public Shopify storefronts via their JSON product feed —
no API key or auth required (the ``/products.json`` endpoint is public).
The default store list (``config.DEFAULT_SHOPIFY_STORES``) favors the
custom-keyboard community; an operator can override the whole list via the
``SHOPIFY_STORES`` JSON Variable. Every fetch is a read-only GET, so it
goes through the shared transport (transient blips are retried instead of
losing a whole store for this run).

Coverage note (R1): each store is fetched up to
``SHOPIFY_MAX_COLLECTIONS_PER_STORE`` collection pages (or its full
products.json when no collections are configured), at ``?limit=250``.
Pagination is intentionally NOT traversed — a store with more products than
one page under-fetches. Known gap, flagged rather than silent.

``--dry-run`` prints what a fetch would return without touching Discord;
``--store`` limits the run to one store by name.
"""

import re
import time
from random import uniform

from deal_bot import config, transport
from deal_bot.sources.base import discount_percent

_SLUGIFY_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Lowercase, hyphen-joined slug from a store name (e.g. "Kinetic Labs"
    -> "kinetic-labs")."""
    return _SLUGIFY_RE.sub("-", name.lower()).strip("-")


def _rebuild_store_base_urls() -> dict[str, str]:
    """Build the slug -> base_url lookup at import time from
    config.SHOPIFY_STORES. Deterministic per store name, which is exactly
    why it can be derived once: _normalize_product needs it for URL
    construction and shouldn't re-read config on every product (perf), and
    the slug is a pure function of the store name.

    R4 gotcha: tests that monkeypatch config.SHOPIFY_STORES after import
    must also reset config.SHOPIFY_STORE_BASE_URLS (they do)."""
    return {
        _slugify(s["name"]): s["base_url"]
        for s in config.SHOPIFY_STORES
        if s.get("name") and s.get("base_url")
    }


# Derived global, built once at import from the parsed store list. NOT
# removed or made lazy — _normalize_product needs it and R4 forbids dropping
# it (tests depend on it being present to reset).
config.SHOPIFY_STORE_BASE_URLS = _rebuild_store_base_urls()


def _to_float(raw) -> float | None:
    """Shopify returns prices as STRINGS (e.g. "79.99"). Some storefronts
    send "0.00" or "" as a sentinel for "no compare_at_price"; treat those
    the same as None. Returns None for anything that isn't a positive float —
    a price of 0 is a freebie/glitch, not a sale price."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _product_image(product: dict) -> str | None:
    """Shopify exposes both product['image'] (object) and product['images']
    (array of {src}); tolerate a flat string too. Some storefronts and tests
    use one or the other."""
    img = product.get("image")
    if isinstance(img, dict):
        return img.get("src")
    if isinstance(img, str):
        return img
    images = product.get("images")
    if isinstance(images, list) and images and isinstance(images[0], dict):
        return images[0].get("src")
    return None


def _normalize_product(product: dict, store_name: str, slug: str) -> dict | None:
    """Map a Shopify product to the shared Deal contract, applying the
    MANDATORY real-deal filter: a product only counts when at least one
    AVAILABLE variant has compare_at_price strictly greater than its price
    (i.e. a genuine markdown). Among qualifying variants the lowest-priced
    available one wins. Returns None when there is no real discount (missing
    compare_at, "0.00"/""/None sentinel, compare_at <= price), so no-deal
    products never enter the pipeline as noise."""
    if not product.get("id") or not product.get("handle"):
        return None
    title = product.get("title")
    if not title:
        return None
    variants = product.get("variants") or []
    if not variants:
        return None

    best: dict | None = None
    for v in variants:
        if not isinstance(v, dict) or v.get("available") is False:
            continue
        sale = _to_float(v.get("price"))
        if sale is None:
            continue
        list_price = _to_float(v.get("compare_at_price"))
        # Real-deal filter: no compare_at (or a 0/""/None sentinel) or
        # compare_at <= price means this is NOT a discount — skip it.
        if list_price is None or list_price <= sale:
            continue
        if best is None or sale < best["sale_price"]:
            best = {"sale_price": sale, "list_price": list_price}
    if best is None:
        return None

    base = config.SHOPIFY_STORE_BASE_URLS.get(slug)
    return {
        "id": f"shopify:{slug}:{product['id']}",
        "source": "Shopify",
        "store": store_name,
        "title": f"{title} ({store_name})",
        "url": f"{base}/products/{product['handle']}" if base else None,
        "image": _product_image(product),
        "list_price": best["list_price"],
        "sale_price": best["sale_price"],
        "discount_pct": discount_percent(best["list_price"], best["sale_price"]),
    }


def _store_urls(store: dict) -> list[str]:
    """The list of URLs to fetch for one store: either the collection
    endpoints (capped by SHOPIFY_MAX_COLLECTIONS_PER_STORE) or the full
    storefront products.json when no collections are configured."""
    base = (store.get("base_url") or "").rstrip("/")
    handles = store.get("collection_handles") or []
    limit = config.SHOPIFY_MAX_COLLECTIONS_PER_STORE
    if handles:
        return [
            f"{base}/collections/{handle}/products.json?limit=250"
            for handle in handles[:limit]
        ]
    return [f"{base}/products.json?limit=250"]


def fetch_shopify_store(store: dict) -> list[dict]:
    """Fetch one storefront, dedup by product id, returning deal dicts.
    Any single collection's failure (transport None, non-200, non-JSON)
    yields [] for that URL without aborting the others — defensive, matching
    transport's own fail-open return-None contract."""
    deals: list[dict] = []
    seen: set[int] = set()
    store_name = store.get("name", "Shopify")
    slug = _slugify(store_name)

    for url in _store_urls(store):
        resp = transport.request("GET", url, timeout=15)
        if resp is None:
            print(f"[shopify] {store_name} request failed after retries: {url}")
            continue
        if resp.status_code != 200:
            print(f"[shopify] {store_name} returned {resp.status_code}: {url}")
            continue
        try:
            payload = resp.json()
        except ValueError:
            print(f"[shopify] {store_name} returned non-JSON body: {url}")
            continue

        for product in payload.get("products", []):
            pid = product.get("id")
            if pid is None or pid in seen:
                continue
            seen.add(pid)
            deal = _normalize_product(product, store_name, slug)
            if deal:
                deals.append(deal)

    return deals


def fetch_all_shopify_stores() -> list[dict]:
    """Fetch every configured store, throttling BETWEEN stores (not before
    the first). One store raising must not abort the rest — defensive,
    though transport already returns None rather than raising."""
    stores = config.SHOPIFY_STORES
    deals: list[dict] = []
    for i, store in enumerate(stores):
        if i > 0:
            lo, hi = config._SHOPIFY_THROTTLE_RANGE
            time.sleep(uniform(lo, hi))
        try:
            deals.extend(fetch_shopify_store(store))
        except Exception as e:
            print(f"[shopify] store {store.get('name')} raised: {e}")
    return deals


def main():
    import argparse
    import sys

    # Some storefront titles carry non-ASCII punctuation (e.g. fullwidth
    # parens); don't crash the CLI on a legacy console codepage (cp1252).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Shopify deal source — dry-run / targeted fetch")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be fetched without posting anything")
    parser.add_argument("--store", default=None,
                        help="fetch only the store whose name matches (else all configured stores)")
    args = parser.parse_args()

    if args.store:
        stores = [s for s in config.SHOPIFY_STORES if s.get("name") == args.store]
        if not stores:
            print(f"[shopify] no configured store named '{args.store}'")
            return
    else:
        stores = config.SHOPIFY_STORES

    for store in stores:
        print(f"[shopify] fetching {store['name']} ({store['base_url']}) ...")
        deals = fetch_shopify_store(store)
        if args.dry_run:
            for d in deals:
                print(f"  {d['id']} — {d['title']} — ${d['sale_price']:.2f} (list {d['list_price']})")
            print(f"[shopify] {len(deals)} deals for {store['name']}")
        else:
            print(f"[shopify] {len(deals)} deals for {store['name']} (no posting — use the pipeline)")


if __name__ == "__main__":
    main()