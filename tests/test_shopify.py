"""Tests for the Shopify deal source (deal_bot/sources/shopify.py) and the
config._parse_shopify_stores parsing.

Stdlib only (unittest + unittest.mock). Every transport.request call is
mocked — no real network traffic.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config
from deal_bot.sources import shopify


def _product(pid: int = 1, image="https://img/x.jpg", price="50.00",
             compare_at="100.00", available=True) -> dict:
    return {
        "id": pid,
        "handle": "gmk-noah",
        "title": "GMK Noah Keycap Set",
        "image": image,
        "variants": [
            {"price": price, "compare_at_price": compare_at, "available": available},
        ],
    }


def _resp(status: int = 200, payload=None, text="") -> Mock:
    resp = Mock()
    resp.status_code = status
    resp.text = text or f"status {status}"
    resp.json.return_value = payload if payload is not None else {}
    return resp


class NormalizeProductTests(unittest.TestCase):
    def test_valid_product_maps_to_deal(self):
        deal = shopify._normalize_product(_product(), "KBDfans", "kbdfans")
        self.assertEqual(deal["id"], "shopify:kbdfans:1")
        self.assertEqual(deal["source"], "Shopify")
        self.assertEqual(deal["store"], "KBDfans")
        self.assertEqual(deal["title"], "GMK Noah Keycap Set (KBDfans)")
        self.assertEqual(deal["sale_price"], 50.0)
        self.assertEqual(deal["list_price"], 100.0)
        self.assertAlmostEqual(deal["discount_pct"], 50.0)

    def test_compare_at_zero_string_is_not_a_discount(self):
        # Shopify sentinel "0.00" means "no compare price" — must NOT count
        # as a free / 100%-off deal.
        self.assertIsNone(shopify._normalize_product(
            _product(compare_at="0.00"), "KBDfans", "kbdfans"))

    def test_compare_at_empty_string_is_not_a_discount(self):
        self.assertIsNone(shopify._normalize_product(
            _product(compare_at=""), "KBDfans", "kbdfans"))

    def test_compare_at_none_is_not_a_discount(self):
        self.assertIsNone(shopify._normalize_product(
            _product(compare_at=None), "KBDfans", "kbdfans"))

    def test_compare_at_equal_to_price_is_not_a_discount(self):
        # No actual markdown — compare_at == price means "not on sale."
        self.assertIsNone(shopify._normalize_product(
            _product(price="50.00", compare_at="50.00"), "KBDfans", "kbdfans"))

    def test_compare_at_below_price_is_not_a_discount(self):
        # compare_at less than price is a Shopify data glitch, not a deal.
        self.assertIsNone(shopify._normalize_product(
            _product(price="50.00", compare_at="30.00"), "KBDfans", "kbdfans"))

    def test_price_zero_is_dropped(self):
        # A free product is a glitch, not a deal.
        self.assertIsNone(shopify._normalize_product(
            _product(price="0.00", compare_at="100.00"), "KBDfans", "kbdfans"))

    def test_variant_available_missing_is_treated_as_available(self):
        # Shopify may omit "available"; contract treats absent as available.
        p = _product()
        p["variants"] = [{"price": "50.00", "compare_at_price": "100.00"}]
        deal = shopify._normalize_product(p, "KBDfans", "kbdfans")
        self.assertEqual(deal["sale_price"], 50.0)

    def test_multi_variant_prefers_available_lowest_price(self):
        product = _product()
        product["variants"] = [
            {"price": "50.00", "compare_at_price": "100.00", "available": False},
            {"price": "90.00", "compare_at_price": "180.00", "available": True},
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

    def test_image_dict_form_is_extracted(self):
        # Canonical Shopify JSON nests image as {"src": "..."}.
        p = _product(image=None)
        p["image"] = {"src": "https://img/dict.jpg"}
        deal = shopify._normalize_product(p, "KBDfans", "kbdfans")
        self.assertEqual(deal["image"], "https://img/dict.jpg")

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