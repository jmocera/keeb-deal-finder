"""Category tagger — SHADOW MODE (not gating or routing posts yet).

One batched OpenRouter call per run tagging each deal into a fine-grained
category (board/switch/keycaps/accessory/other), which could later drive
per-category Discord channels or better hashtag/analysis targeting.
"""

import json

from deal_bot import config
from deal_bot.ai.client import _call_openrouter


def _parse_categories_json(response: str | None, n: int, valid: set[str]) -> list[str] | None:
    """Strict JSON parse of the model's category response. Returns a list
    of exactly `n` lowercased category strings (each a member of `valid`),
    or None on any of these edge cases:

      - `response` is None/empty/whitespace-only.
      - `response` is not valid JSON, even after stripping a surrounding
        markdown code fence and extracting the first balanced {...} block.
      - the parsed JSON is not an object (e.g. a bare array or string).
      - the object has no "categories" key, or its value is not a list.
      - the list's length is not exactly `n` — no partial salvage.
      - any element is not a string.
      - any string, stripped and lowercased, is not in `valid`.

    The markdown-fence strip mirrors client.py's own normalization but is
    applied here too for defense-in-depth in the mocked test path."""
    if not response:
        return None
    text = response.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    parsed = None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
            except (ValueError, TypeError):
                pass
    if not isinstance(parsed, dict):
        return None
    categories = parsed.get("categories")
    if not isinstance(categories, list) or len(categories) != n:
        return None
    out: list[str] = []
    for c in categories:
        if not isinstance(c, str):
            return None
        token = c.strip().lower()
        if token not in valid:
            return None
        out.append(token)
    return out


def categorize_deals(deals: list[dict]) -> tuple[dict[str, str], str | None]:
    """Returns ({deal_id: category}, model_used). One batched call, fail-open.

    Fails OPEN: on any failure (missing key, both models erroring, or a
    response that cannot be parsed to exactly len(deals) valid category
    tokens) an empty map and None are returned — the caller treats that
    as "no categories this run," never as a reason to drop a deal.
    Partial salvage is intentionally removed: a response that doesn't
    yield exactly one valid category per deal for every deal falls
    through to the fallback model.
    """
    if not deals:
        return {}, None
    if not config.OPENROUTER_API_KEY:
        return {}, None

    lines = [
        f"{i}. [{d['source']}] {d['title']} — ${d['sale_price']:.2f}"
        for i, d in enumerate(deals, start=1)
    ]
    user_prompt = "\n".join(lines)
    max_tokens = min(1000, 200 + len(deals) * 15)

    valid = set(config.DEAL_CATEGORIES)
    for model in (config.OPENROUTER_CATEGORIZER_MODEL, config.OPENROUTER_CATEGORIZER_FALLBACK_MODEL):
        response = _call_openrouter(
            model, config.OPENROUTER_CATEGORIZER_SYSTEM_PROMPT, user_prompt,
            temperature=0.1, max_tokens=max_tokens,
            response_format={"type": "json_object"},
            # reasoning omitted: Gemma burns its token budget on reasoning
            # when any effort is set (see ai/deal_scorer.py).
        )
        if not response:
            continue
        categories = _parse_categories_json(response, len(deals), valid)
        if categories is not None:
            return {d["id"]: c for d, c in zip(deals, categories)}, model
        print(
            f"[openrouter] categorizer response from {model} didn't parse cleanly "
            f"for {len(deals)} deals — trying next"
        )

    print("[openrouter] categorizer unavailable from both models this run — no shadow report")
    return {}, None
