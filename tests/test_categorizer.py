"""Tests for the shadow-mode category tagger (ai/categorizer.py).

Stdlib only (unittest + unittest.mock). Every _call_openrouter call is
mocked — no real network calls. The model now emits a JSON object
({"categories": [...]}) and the parser is a strict JSON parse with a
markdown-fence / prose-extraction fallback; line-anchored extraction
is gone.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot import config
from deal_bot.ai import categorizer


def _make_deal(i: int) -> dict:
    return {
        "id": f"woot:test-{i}", "source": "Woot", "title": f"Deal {i}",
        "url": "https://example.com/deal", "sale_price": 10.0 * i,
        "list_price": 20.0 * i, "discount_pct": 50.0,
    }


class CategorizeDealsTests(unittest.TestCase):
    def setUp(self):
        self._orig_key = config.OPENROUTER_API_KEY
        config.OPENROUTER_API_KEY = "test-key"

    def tearDown(self):
        config.OPENROUTER_API_KEY = self._orig_key

    def test_missing_api_key_skips_network_call(self):
        config.OPENROUTER_API_KEY = ""
        with patch("deal_bot.ai.categorizer._call_openrouter") as mock_call:
            categories, model = categorizer.categorize_deals([_make_deal(1)])
            mock_call.assert_not_called()
        self.assertEqual(categories, {})
        self.assertIsNone(model)

    def test_empty_deals_returns_empty(self):
        self.assertEqual(categorizer.categorize_deals([]), ({}, None))

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_valid_json_categories_are_parsed(self, mock_call):
        mock_call.return_value = '{"categories": ["switch", "board", "keycaps"]}'
        deals = [_make_deal(i) for i in (1, 2, 3)]

        categories, model = categorizer.categorize_deals(deals)

        self.assertEqual(model, config.OPENROUTER_CATEGORIZER_MODEL)
        self.assertEqual(categories, {
            "woot:test-1": "switch", "woot:test-2": "board", "woot:test-3": "keycaps",
        })

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_markdown_fence_wrapped_json_parses(self, mock_call):
        mock_call.return_value = '```json\n{"categories": ["switch", "board", "keycaps"]}\n```'
        deals = [_make_deal(i) for i in (1, 2, 3)]

        categories, model = categorizer.categorize_deals(deals)

        self.assertEqual(model, config.OPENROUTER_CATEGORIZER_MODEL)
        self.assertEqual(categories, {
            "woot:test-1": "switch", "woot:test-2": "board", "woot:test-3": "keycaps",
        })

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_prose_wrapped_json_parses(self, mock_call):
        mock_call.return_value = 'Here are the categories:\n{"categories": ["switch", "board"]}\nDone.'
        deals = [_make_deal(i) for i in (1, 2)]

        categories, model = categorizer.categorize_deals(deals)

        self.assertEqual(model, config.OPENROUTER_CATEGORIZER_MODEL)
        self.assertEqual(categories, {"woot:test-1": "switch", "woot:test-2": "board"})

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_extra_keys_in_json_are_ignored(self, mock_call):
        mock_call.return_value = '{"categories": ["switch", "board"], "reasoning": "n/a", "model": "x"}'
        deals = [_make_deal(i) for i in (1, 2)]

        categories, _ = categorizer.categorize_deals(deals)

        self.assertEqual(categories, {"woot:test-1": "switch", "woot:test-2": "board"})

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_uppercase_category_normalized(self, mock_call):
        mock_call.return_value = '{"categories": ["SWITCH", "BOARD", "Keycaps"]}'
        deals = [_make_deal(i) for i in (1, 2, 3)]

        categories, _ = categorizer.categorize_deals(deals)

        self.assertEqual(categories, {
            "woot:test-1": "switch", "woot:test-2": "board", "woot:test-3": "keycaps",
        })

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_fails_open_when_both_models_return_none(self, mock_call):
        mock_call.return_value = None
        categories, model = categorizer.categorize_deals([_make_deal(1)])
        self.assertEqual(categories, {})
        self.assertIsNone(model)
        self.assertEqual(mock_call.call_count, 2)

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_wrong_array_length_fails_open(self, mock_call):
        mock_call.return_value = '{"categories": ["switch", "board"]}'
        categories, model = categorizer.categorize_deals([_make_deal(i) for i in (1, 2, 3)])
        self.assertEqual(categories, {})
        self.assertIsNone(model)
        self.assertEqual(mock_call.call_count, 2)

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_unknown_category_fails_open(self, mock_call):
        mock_call.return_value = '{"categories": ["switch", "toaster"]}'
        categories, model = categorizer.categorize_deals([_make_deal(i) for i in (1, 2)])
        self.assertEqual(categories, {})
        self.assertIsNone(model)
        self.assertEqual(mock_call.call_count, 2)

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_model_fallback_chain_uses_fallback(self, mock_call):
        mock_call.side_effect = [None, '{"categories": ["switch", "board"]}']
        deals = [_make_deal(i) for i in (1, 2)]

        categories, model = categorizer.categorize_deals(deals)

        self.assertEqual(model, config.OPENROUTER_CATEGORIZER_FALLBACK_MODEL)
        self.assertEqual(categories, {"woot:test-1": "switch", "woot:test-2": "board"})

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_prompt_carries_the_deals(self, mock_call):
        mock_call.return_value = '{"categories": ["switch", "board"]}'
        deals = [_make_deal(1), _make_deal(2)]

        categorizer.categorize_deals(deals)

        sent_user_prompt = mock_call.call_args[0][2]
        self.assertIn("Deal 1", sent_user_prompt)
        self.assertIn("Deal 2", sent_user_prompt)

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_response_format_is_json_object(self, mock_call):
        mock_call.return_value = '{"categories": ["switch"]}'
        categorizer.categorize_deals([_make_deal(1)])
        self.assertEqual(
            mock_call.call_args.kwargs.get("response_format"),
            {"type": "json_object"},
        )

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_reasoning_is_omitted(self, mock_call):
        mock_call.return_value = '{"categories": ["switch"]}'
        categorizer.categorize_deals([_make_deal(1)])
        self.assertNotIn("reasoning", mock_call.call_args.kwargs)

    @patch("deal_bot.ai.categorizer._call_openrouter")
    def test_system_prompt_requires_json_categories_shape(self, mock_call):
        mock_call.return_value = '{"categories": ["switch"]}'
        categorizer.categorize_deals([_make_deal(1)])
        sent_system_prompt = mock_call.call_args[0][1]
        self.assertIn('"categories"', sent_system_prompt)
        self.assertIn("JSON object", sent_system_prompt)
        self.assertIn("lowercased", sent_system_prompt)
        for token in ("board", "switch", "keycaps", "accessory", "other"):
            self.assertIn(token, sent_system_prompt)


class JsonParseTests(unittest.TestCase):
    """Direct unit tests for _parse_categories_json — the strict JSON contract
    the model is asked to emit, with a markdown-fence and prose-extraction
    fallback for models that ignore response_format."""

    _VALID = {"board", "switch", "keycaps", "accessory", "other"}

    def test_valid_json(self):
        self.assertEqual(
            categorizer._parse_categories_json('{"categories": ["switch", "board"]}', 2, self._VALID),
            ["switch", "board"],
        )

    def test_markdown_fence_wrapped(self):
        self.assertEqual(
            categorizer._parse_categories_json('```json\n{"categories": ["switch"]}\n```', 1, self._VALID),
            ["switch"],
        )

    def test_prose_wrapped_json_extracted(self):
        resp = 'Here are the categories:\n{"categories": ["board", "keycaps"]}\nDone.'
        self.assertEqual(
            categorizer._parse_categories_json(resp, 2, self._VALID),
            ["board", "keycaps"],
        )

    def test_extra_keys_ignored(self):
        self.assertEqual(
            categorizer._parse_categories_json(
                '{"categories": ["switch"], "reasoning": "x", "n": 1}', 1, self._VALID),
            ["switch"],
        )

    def test_uppercase_normalized(self):
        self.assertEqual(
            categorizer._parse_categories_json('{"categories": ["SWITCH", "Board"]}', 2, self._VALID),
            ["switch", "board"],
        )

    def test_whitespace_trimmed(self):
        self.assertEqual(
            categorizer._parse_categories_json('{"categories": [" switch ", "board  "]}', 2, self._VALID),
            ["switch", "board"],
        )

    def test_wrong_length_returns_none(self):
        self.assertIsNone(
            categorizer._parse_categories_json('{"categories": ["switch"]}', 2, self._VALID)
        )

    def test_unknown_token_returns_none(self):
        self.assertIsNone(
            categorizer._parse_categories_json('{"categories": ["toaster"]}', 1, self._VALID)
        )

    def test_missing_categories_key_returns_none(self):
        self.assertIsNone(
            categorizer._parse_categories_json('{"items": ["switch"]}', 1, self._VALID)
        )

    def test_non_list_categories_returns_none(self):
        self.assertIsNone(
            categorizer._parse_categories_json('{"categories": "switch"}', 1, self._VALID)
        )

    def test_non_string_element_returns_none(self):
        self.assertIsNone(
            categorizer._parse_categories_json('{"categories": [5]}', 1, self._VALID)
        )

    def test_non_object_json_returns_none(self):
        self.assertIsNone(
            categorizer._parse_categories_json('["switch", "board"]', 2, self._VALID)
        )

    def test_empty_list_returns_none_when_n_nonzero(self):
        self.assertIsNone(
            categorizer._parse_categories_json('{"categories": []}', 1, self._VALID)
        )

    def test_none_response_returns_none(self):
        self.assertIsNone(categorizer._parse_categories_json(None, 1, self._VALID))

    def test_empty_response_returns_none(self):
        self.assertIsNone(categorizer._parse_categories_json("", 1, self._VALID))
        self.assertIsNone(categorizer._parse_categories_json("   ", 1, self._VALID))

    def test_invalid_json_returns_none(self):
        self.assertIsNone(
            categorizer._parse_categories_json("not json at all", 1, self._VALID)
        )


if __name__ == "__main__":
    unittest.main()
