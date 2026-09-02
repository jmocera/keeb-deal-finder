"""Tests for the "technical verdict" caption upgrade to build_ai_caption()
and its price-history/spec-context prompt cues.

Stdlib only (unittest + unittest.mock), same convention as
tests/test_spec_extraction.py. Runnable via either:
    python -m unittest discover -s tests -p "test_*.py"
    pytest tests/

Every requests.post / _call_openrouter call is mocked — these tests never
make a real network call or touch the real Discord/Bluesky endpoints.
"""
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import post_len
from deal_bot.ai import captions
from deal_bot.integrations import bluesky


def _make_deal(**overrides) -> dict:
    deal = {
        "id": "woot:test-123", "source": "Woot", "title": "Raw Messy SEO Title",
        "clean_title": "Clean Product Title", "specs": ["Capacity: 2TB"],
        "url": "https://example.com/deal", "image": None,
        "sale_price": 79.99, "list_price": 159.99, "discount_pct": 50.0,
        "lowest_price": 79.99, "lowest_price_date": None, "is_new_low": False,
    }
    deal.update(overrides)
    return deal


class BuildAiCaptionVerdictTests(unittest.TestCase):
    @patch("deal_bot.ai.captions._call_openrouter")
    def test_new_low_deal_gets_all_time_low_context_in_the_prompt(self, mock_call):
        mock_call.return_value = "Genuine all-time low for this drive. #PCBuild #SSDDeals"
        deal = _make_deal(is_new_low=True)

        result = captions.build_ai_caption(deal)

        # The point of this test: the *prompt actually sent to the model*
        # carries the historical-low signal, not just that some caption
        # came back — this is what makes the caption "data-backed"
        # instead of an ungrounded guess.
        sent_user_prompt = mock_call.call_args[0][2]  # (model, system_prompt, user_prompt, ...)
        self.assertIn("all-time low", sent_user_prompt.lower())
        self.assertTrue(result.startswith("Genuine all-time low"))

    @patch("deal_bot.ai.captions._call_openrouter")
    def test_not_new_low_but_known_floor_price_is_still_passed_as_context(self, mock_call):
        mock_call.return_value = "Solid price, though not its floor. #PCBuild #KeebDeals"
        deal = _make_deal(is_new_low=False, sale_price=79.99, lowest_price=59.99)

        captions.build_ai_caption(deal)

        sent_user_prompt = mock_call.call_args[0][2]
        self.assertIn("59.99", sent_user_prompt)

    @patch("deal_bot.ai.captions._call_openrouter")
    def test_specs_are_included_in_the_prompt_when_present(self, mock_call):
        mock_call.return_value = "Fast NVMe storage at a real floor price. #PCBuild #KeebDeals"
        deal = _make_deal(specs=["Capacity: 2TB", "Interface: PCIe Gen4"])

        captions.build_ai_caption(deal)

        sent_user_prompt = mock_call.call_args[0][2]
        self.assertIn("Capacity: 2TB", sent_user_prompt)
        self.assertIn("Interface: PCIe Gen4", sent_user_prompt)

    @patch("deal_bot.ai.captions._call_openrouter")
    def test_falls_back_to_template_when_both_models_return_none(self, mock_call):
        mock_call.return_value = None
        deal = _make_deal()

        result = captions.build_ai_caption(deal)

        self.assertEqual(result, captions.build_x_caption(deal))
        self.assertEqual(mock_call.call_count, 2)  # tried primary, then fallback model

    @patch("deal_bot.ai.captions._call_openrouter")
    def test_falls_back_when_response_exceeds_length_ceiling(self, mock_call):
        mock_call.return_value = "X" * 300  # over the 260-char sanity ceiling
        deal = _make_deal()

        result = captions.build_ai_caption(deal)

        self.assertEqual(result, captions.build_x_caption(deal))

    @patch("deal_bot.ai.captions._call_openrouter")
    def test_falls_back_when_hashtags_look_spammy(self, mock_call):
        spammy = "Good deal. " + " ".join(f"#tag{i}" for i in range(10))  # way over 4
        mock_call.return_value = spammy
        deal = _make_deal()

        result = captions.build_ai_caption(deal)

        self.assertEqual(result, captions.build_x_caption(deal))

    @patch("deal_bot.ai.captions._call_openrouter")
    def test_contextual_hashtags_are_kept_not_restricted_to_a_fixed_list(self, mock_call):
        # Deliberate: item-specific hashtags are preserved as-is rather
        # than filtered down to a fixed vocabulary — see the confirmed
        # design decision in this session over the alternative (a hard
        # #gaming/#pcgaming-only allowlist, which was rejected).
        mock_call.return_value = "Real all-time low for this SSD. #SSDDeals #PCBuild #TechDeals"
        deal = _make_deal(is_new_low=True)

        result = captions.build_ai_caption(deal)

        self.assertIn("#SSDDeals", result)
        self.assertIn("#PCBuild", result)
        self.assertIn("#TechDeals", result)


class HashtagSanityCheckTests(unittest.TestCase):
    """The strict trailing-block contract: every accepted AI caption must
    END with 2-4 valid, whitespace-separated hashtag tokens — zero/one tags
    are the defect this fixes, five is spam, duplicates and mid-caption
    tags break the 'ends with hashtags' guarantee."""

    def test_reasonable_hashtag_count_passes(self):
        self.assertTrue(captions._hashtags_look_reasonable("Good deal. #PCBuild #SSDDeals #TechDeals"))

    def test_zero_hashtags_rejected(self):
        self.assertFalse(captions._hashtags_look_reasonable("Good deal, no tags here."))

    def test_one_hashtag_rejected(self):
        self.assertFalse(captions._hashtags_look_reasonable("Great find. #KeebDeals"))

    def test_two_hashtags_accepted(self):
        self.assertTrue(captions._hashtags_look_reasonable("Great find. #KeebDeals #Keycaps"))

    def test_four_hashtags_accepted(self):
        self.assertTrue(captions._hashtags_look_reasonable(
            "Great find. #KeebDeals #Keycaps #MKDeals #GMK"))

    def test_five_hashtags_rejected(self):
        self.assertFalse(captions._hashtags_look_reasonable(
            "Great find. #KeebDeals #Keycaps #MKDeals #GMK #MechanicalKeyboards"))

    def test_duplicate_hashtags_rejected_case_insensitively(self):
        self.assertFalse(captions._hashtags_look_reasonable("Deal. #Keycaps #KEYCAPS #KeebDeals"))

    def test_hashtag_outside_final_block_rejected(self):
        # A mid-sentence tag is prose, not the trailing tag block.
        self.assertFalse(captions._hashtags_look_reasonable(
            "This #MidTag is great. #KeebDeals #Keycaps"))
        # A tag with attached punctuation is not a valid trailing token.
        self.assertFalse(captions._hashtags_look_reasonable("Deal. #KeebDeals #Keycaps."))

    def test_model_injected_url_rejected(self):
        self.assertFalse(captions._hashtags_look_reasonable(
            "Good deal. https://spam.example #KeebDeals #Keycaps"))

    def test_numeric_only_tags_do_not_satisfy_contract(self):
        # '#420'/'#123' are product references, not discoverable tags: they
        # neither count toward the 2-4 contract nor rescue a caption.
        self.assertFalse(captions._hashtags_look_reasonable("Deal. #420 #123"))
        self.assertFalse(captions._hashtags_look_reasonable("Deal. #420 #KeebDeals"))
        self.assertFalse(captions._hashtags_look_reasonable("Deal. #___ #KeebDeals"))

    def test_alphanumeric_tags_remain_valid(self):
        self.assertTrue(captions._hashtags_look_reasonable("Deal. #KeychronQ1 #3DPrinting"))


class MechanicalHashtagTests(unittest.TestCase):
    """The deterministic mechanical fallback must ALWAYS end with 2-3
    relevant hashtags — selected from the deal's category/title data with
    no AI — and every mechanical caption must pass the strict validator."""

    @staticmethod
    def _deal(title: str, category: str | None = None) -> dict:
        deal = {
            "id": "woot:test-1", "source": "Woot", "title": title,
            "url": "https://example.com/deal", "image": None,
            "sale_price": 79.99, "list_price": 159.99, "discount_pct": 50.0,
        }
        if category is not None:
            deal["category"] = category
        return deal

    def test_keyboard_title_gets_board_suffix(self):
        body = captions.build_x_caption_body(
            self._deal("Keychron Q1 QMK Custom Mechanical Keyboard (barebones)"))
        self.assertTrue(body.endswith("#KeebDeals #MechanicalKeyboards #KeyboardBuilds"))

    def test_keycap_title_gets_keycap_suffix(self):
        body = captions.build_x_caption_body(self._deal("GMK Meow-achi Keycap Set — Cherry Profile"))
        self.assertTrue(body.endswith("#KeebDeals #Keycaps #MechanicalKeyboards"))

    def test_switch_title_gets_switch_suffix(self):
        body = captions.build_x_caption_body(self._deal("Gateron Oil King Linear Switches (110-pack)"))
        self.assertTrue(body.endswith("#KeebDeals #KeyboardSwitches #MechanicalKeyboards"))

    def test_stabilizers_are_switches_not_boards(self):
        # Specific item types win over generic plate/board wording —
        # stabilizers are core switch mechanics per the categorizer contract.
        body = captions.build_x_caption_body(self._deal("Durock Plate Mount Stabilizer Set (2u)"))
        self.assertTrue(body.endswith("#KeebDeals #KeyboardSwitches #MechanicalKeyboards"))

    def test_cable_title_gets_accessory_suffix(self):
        body = captions.build_x_caption_body(self._deal("KBDfans 1.5m Coiled Aviator Cable — White"))
        self.assertTrue(body.endswith("#KeebDeals #KeebAccessories #MechanicalKeyboards"))

    def test_keyboard_cable_is_accessory_not_board(self):
        # Specific accessory wording wins over generic "keyboard" wording.
        body = captions.build_x_caption_body(self._deal("Keyboard coiled cable, braided"))
        self.assertTrue(body.endswith("#KeebDeals #KeebAccessories #MechanicalKeyboards"))

    def test_unknown_title_gets_generic_fallback(self):
        body = captions.build_x_caption_body(self._deal("Mystery gadget 3000"))
        self.assertTrue(body.endswith("#KeebDeals #MechanicalKeyboards"))

    def test_recognized_category_value_is_used_when_present(self):
        # config.DEAL_CATEGORIES vocabulary: honored when a deal carries it.
        body = captions.build_x_caption_body(self._deal("Some item", category="keycaps"))
        self.assertTrue(body.endswith("#KeebDeals #Keycaps #MechanicalKeyboards"))

    def test_unknown_category_value_falls_through_to_title(self):
        # Only existing category values are recognized; anything else is
        # ignored rather than guessed at (title here is generic -> fallback).
        body = captions.build_x_caption_body(self._deal("Mystery gadget", category="toaster"))
        self.assertTrue(body.endswith("#KeebDeals #MechanicalKeyboards"))

    def test_mixed_case_category_value_recognized(self):
        # The category contract is case-insensitive: normalized via lower().
        body = captions.build_x_caption_body(self._deal("Some item", category="KeYcApS"))
        self.assertTrue(body.endswith("#KeebDeals #Keycaps #MechanicalKeyboards"))

    def test_title_only_classification_without_clean_title(self):
        # No clean_title key at all: classification comes from the raw title
        # and the display title falls back to it.
        deal = {
            "id": "woot:1", "source": "Woot",
            "title": "Keychron Q1 QMK Custom Mechanical Keyboard (barebones)",
            "url": "https://example.com/deal", "sale_price": 79.99,
            "list_price": 159.99, "discount_pct": 50.0,
        }
        body = captions.build_x_caption_body(deal)
        self.assertTrue(body.endswith("#KeebDeals #MechanicalKeyboards #KeyboardBuilds"))
        self.assertIn("Keychron Q1", body)

    def test_numeric_pseudo_tag_in_title_passes_validation(self):
        # '#420' inside a product title is prose, not a hashtag: the
        # mechanical caption must still pass the strict validator.
        body = captions.build_x_caption_body(self._deal("GMK #420 Keycap Set"))
        self.assertTrue(captions._hashtags_look_reasonable(body))
        self.assertTrue(body.endswith("#KeebDeals #Keycaps #MechanicalKeyboards"))

    def test_every_mechanical_caption_passes_validation(self):
        titles = [
            "Keychron Q1 QMK Custom Mechanical Keyboard (barebones)",
            "GMK Meow-achi Keycap Set — Cherry Profile",
            "Gateron Oil King Linear Switches (110-pack)",
            "KBDfans 1.5m Coiled Aviator Cable — White",
            "Mystery gadget 3000",
        ]
        for title in titles:
            with self.subTest(title=title):
                body = captions.build_x_caption_body(self._deal(title))
                self.assertTrue(captions._hashtags_look_reasonable(body), body)


class BlueskyTagFacetTests(unittest.TestCase):
    """End-to-end hashtag behavior on the final fitted Bluesky post:
    realistic Woot/Shopify URLs preserved, >=2 trailing hashtags surviving
    the fit, a tag facet for every retained hashtag, and facet byte ranges
    that decode (UTF-8) to exactly those hashtags — including emoji-shifted
    offsets."""

    WOOT_URL = "https://www.woot.com/offer/detail/gmk-meow-achi-keycaps?utm_source=deals"
    SHOPIFY_URL = "https://kbdfans.com/products/gmk-noah-keycap-set"

    @staticmethod
    def _retained_trailing_tags(text: str, url: str) -> list[str]:
        # The fitted post ends with the URL on its own line — scan the
        # caption body ABOVE it for the surviving trailing tag run.
        body = text[: -len(url)].rstrip() if url and text.endswith(url) else text
        tokens = body.split()
        tags = []
        for token in reversed(tokens):
            if token.startswith("#"):
                tags.append(token)
            else:
                break
        tags.reverse()
        return tags

    def _assert_facets_cover_tags(self, text: str, url: str):
        raw = text.encode("utf-8")
        tag_facets = [
            f for f in bluesky._build_tag_facets(text)
            if f["features"][0]["$type"] == "app.bsky.richtext.facet#tag"
        ]
        decoded = {
            raw[f["index"]["byteStart"]:f["index"]["byteEnd"]].decode("utf-8")
            for f in tag_facets
        }
        for tag in self._retained_trailing_tags(text, url):
            self.assertIn(tag, decoded, f"missing facet for {tag}")

    def test_woot_post_keeps_url_and_two_tags_with_facets(self):
        body = captions.build_x_caption_body({
            "id": "woot:1", "source": "Woot", "title": "GMK Meow-achi Keycap Set",
            "clean_title": "GMK Meow-achi Keycap Set", "specs": [],
            "url": self.WOOT_URL, "sale_price": 79.99, "list_price": 159.99,
            "discount_pct": 50.0,
        })
        out = bluesky.fit_deal_post(body, self.WOOT_URL)
        self.assertLessEqual(len(out), post_len.hard_target())
        self.assertTrue(out.endswith(self.WOOT_URL))
        self.assertGreaterEqual(len(self._retained_trailing_tags(out, self.WOOT_URL)), 2)
        self._assert_facets_cover_tags(out, self.WOOT_URL)

    def test_shopify_post_keeps_url_and_two_tags_with_facets(self):
        body = captions.build_x_caption_body({
            "id": "shopify:1", "source": "Shopify", "title": "KBDfans Maja PBT Keycaps",
            "clean_title": "KBDfans Maja PBT Keycaps", "specs": [],
            "url": self.SHOPIFY_URL, "sale_price": 59.99, "list_price": 119.99,
            "discount_pct": 50.0,
        })
        out = bluesky.fit_deal_post(body, self.SHOPIFY_URL)
        self.assertLessEqual(len(out), post_len.hard_target())
        self.assertTrue(out.endswith(self.SHOPIFY_URL))
        self.assertGreaterEqual(len(self._retained_trailing_tags(out, self.SHOPIFY_URL)), 2)
        self._assert_facets_cover_tags(out, self.SHOPIFY_URL)

    def test_emoji_prefix_byte_offsets_decode_exactly(self):
        # Multi-byte characters BEFORE the tags shift byte offsets — the
        # facets must still decode to the exact hashtag text.
        body = "🔥 Genuine all-time low for this GMK set. #Keycaps #KeebDeals"
        url = "https://example.com/deal"
        out = bluesky.fit_deal_post(body, url)
        self.assertEqual(out, body + "\n" + url)  # no trimming needed
        self._assert_facets_cover_tags(out, url)
        self.assertEqual(
            [f["index"]["byteStart"] for f in bluesky._build_tag_facets(out)],
            [len(out[:m.start()].encode("utf-8"))
             for m in re.finditer(r"#\w+", out)],
        )

    def test_numeric_pseudo_tag_gets_no_facet(self):
        # '#420' in the product title must never become a Bluesky tag facet;
        # the intended deterministic trailing tags all get exactly one.
        body = captions.build_x_caption_body({
            "id": "shopify:2", "source": "Shopify", "title": "GMK #420 Keycap Set",
            "clean_title": "GMK #420 Keycap Set", "specs": [],
            "url": self.SHOPIFY_URL, "sale_price": 59.99, "list_price": 119.99,
            "discount_pct": 50.0,
        })
        out = bluesky.fit_deal_post(body, self.SHOPIFY_URL)
        self.assertTrue(out.endswith(self.SHOPIFY_URL))
        self.assertGreaterEqual(len(self._retained_trailing_tags(out, self.SHOPIFY_URL)), 2)
        self._assert_facets_cover_tags(out, self.SHOPIFY_URL)
        facet_tags = {
            f["features"][0]["tag"]
            for f in bluesky._build_tag_facets(out)
            if f["features"][0]["$type"] == "app.bsky.richtext.facet#tag"
        }
        self.assertNotIn("420", facet_tags)
        self.assertIn("Keycaps", facet_tags)


class BlueskyLengthLimitTests(unittest.TestCase):
    """Confirms the 300-grapheme budget holds end-to-end for the new,
    potentially longer/differently-shaped verdict-style captions, and that
    the URL line is never lost or clipped."""

    @patch("deal_bot.integrations.bluesky.requests.post")
    @patch("deal_bot.integrations.bluesky._build_bluesky_embed", return_value=None)
    @patch("deal_bot.integrations.bluesky._bluesky_login")
    @patch("deal_bot.integrations.bluesky.build_ai_caption_body")
    def test_post_text_never_exceeds_300_chars_even_with_an_oversized_caption(
        self, mock_caption, mock_login, mock_embed, mock_post
    ):
        mock_login.return_value = {"accessJwt": "test-jwt", "did": "did:plc:test"}
        # Deliberately oversized, as if a verdict caption ran long — the
        # body is patched; fit_deal_post appends and fits the URL itself.
        mock_caption.return_value = "A" * 290
        mock_post.return_value = Mock(status_code=200)

        deal = _make_deal(url="https://example.com/deal")
        ok = bluesky.post_to_bluesky(deal)

        self.assertTrue(ok)
        sent_record = mock_post.call_args.kwargs["json"]["record"]
        self.assertLessEqual(len(sent_record["text"]), 300)
        self.assertTrue(sent_record["text"].endswith(deal["url"]))


class HashtagPreservationTests(unittest.TestCase):
    """The bug: posts ended with the Unicode ellipsis because the old safety net tail-sliced
    the caption body, chopping the trailing hashtags. fit_deal_post must
    trim the prose first and keep the hashtag run + URL intact."""

    @patch("deal_bot.integrations.bluesky.requests.post")
    @patch("deal_bot.integrations.bluesky._build_bluesky_embed", return_value=None)
    @patch("deal_bot.integrations.bluesky._bluesky_login")
    @patch("deal_bot.integrations.bluesky.build_ai_caption_body")
    def test_hashtags_survive_truncation(self, mock_caption, mock_login, mock_embed, mock_post):
        mock_login.return_value = {"accessJwt": "test-jwt", "did": "did:plc:test"}
        mock_caption.return_value = "A" * 290 + " #PCBuild #SSDDeals"
        mock_post.return_value = Mock(status_code=200)

        deal = _make_deal(url="https://example.com/deal")
        ok = bluesky.post_to_bluesky(deal)

        self.assertTrue(ok)
        sent_record = mock_post.call_args.kwargs["json"]["record"]
        self.assertLessEqual(len(sent_record["text"]), 300)
        self.assertTrue(sent_record["text"].endswith(deal["url"]))
        self.assertIn("#PCBuild #SSDDeals", sent_record["text"])

    @patch("deal_bot.integrations.bluesky.requests.post")
    @patch("deal_bot.integrations.bluesky._build_bluesky_embed", return_value=None)
    @patch("deal_bot.integrations.bluesky._bluesky_login")
    @patch("deal_bot.integrations.bluesky.build_ai_caption_body")
    def test_short_caption_posts_without_ellipsis(self, mock_caption, mock_login, mock_embed, mock_post):
        mock_login.return_value = {"accessJwt": "test-jwt", "did": "did:plc:test"}
        mock_caption.return_value = "Genuine floor price for this drive. #SSDDeals"
        mock_post.return_value = Mock(status_code=200)

        deal = _make_deal(url="https://example.com/deal")
        ok = bluesky.post_to_bluesky(deal)

        self.assertTrue(ok)
        sent_record = mock_post.call_args.kwargs["json"]["record"]
        self.assertEqual(sent_record["text"], mock_caption.return_value + "\n" + deal["url"])
        self.assertNotIn("\u2026", sent_record["text"])

    def test_embed_failure_never_breaks_the_text_post(self):
        # _build_bluesky_embed returning None must only drop the card — the
        # text + facets still go out. (Direct fit check, no network.)
        deal = _make_deal()
        body = "Solid deal for builders. #PCBuild"
        out = bluesky.fit_deal_post(body, deal["url"])
        self.assertIn("#PCBuild", out)
        self.assertTrue(out.endswith(deal["url"]))


class BuildAiCaptionBodyBudgetTests(unittest.TestCase):
    @patch("deal_bot.ai.captions._call_openrouter")
    def test_exactly_budget_caption_is_accepted(self, mock_call):
        url = "https://example.com/deal"
        budget = captions.caption_budget(url)
        # Accepted captions must END with 2-4 hashtags — land exactly on
        # budget WITH the trailing tag block included.
        tag_block = " #KeebDeals #Keycaps"
        caption = "D" * (budget - len(tag_block)) + tag_block
        self.assertEqual(len(caption), budget)
        mock_call.return_value = caption
        deal = _make_deal(url=url)

        result = captions.build_ai_caption_body(deal)

        self.assertEqual(result, caption)

    @patch("deal_bot.ai.captions._call_openrouter")
    def test_over_budget_caption_falls_through_to_next_model(self, mock_call):
        url = "https://example.com/deal"
        budget = captions.caption_budget(url)
        mock_call.side_effect = ["E" * (budget + 1), "E" * (budget + 1)]
        deal = _make_deal(url=url)

        result = captions.build_ai_caption_body(deal)

        self.assertEqual(result, captions.build_x_caption_body(deal))
        self.assertEqual(mock_call.call_count, 2)

    @patch("deal_bot.ai.captions._call_openrouter")
    def test_caption_with_a_url_is_rejected(self, mock_call):
        mock_call.side_effect = [
            "Good deal. https://evil.example.com #PCBuild #KeebDeals",
            "Good deal. #PCBuild #KeebDeals",
        ]
        deal = _make_deal()

        result = captions.build_ai_caption_body(deal)

        self.assertEqual(result, "Good deal. #PCBuild #KeebDeals")  # first model's URL was rejected


if __name__ == "__main__":
    unittest.main()