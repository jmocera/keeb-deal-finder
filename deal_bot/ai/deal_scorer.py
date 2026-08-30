"""Deal quality scorer — SHADOW MODE (not gating real posts yet).

One batched OpenRouter call per run rating each deal 1-10 for a
mechanical-keyboard building/enjoying audience, complementing the
keyword/discount filters with a judgment of whether the item is genuinely
*desirable* (recognizable brand, real spec-to-price value) rather than
merely topically in-category.

Bounded batch, same pattern as spec_extraction/verdicts/classifier: strict
json_schema structured output ({"items": [int, ...]}), at most ONE call
per model (primary, then fallback — deduped when both slots name the same
model), each response validated inside the model loop for parse, shape,
and EXACT item cardinality. A degraded batch never fans out into
per-item calls; total failure returns an empty score map and no shadow
report (fail-open — everything passes).

Token budget: sized from the schema's realistic per-item maximum (an
integer 1-10 with JSON syntax ≈ 4 output tokens; 8/item with 2x margin)
and a fixed ~200-token JSON-scaffolding overhead — with NO artificial
cap. The old min(1500, ...) cap truncated a 73-deal run to 10 scores.
Chunking is deliberately unnecessary: the worst realistic batch (124
items) needs ≈ 1.2K output tokens, far below provider output ceilings.
"""

import json
import re

from deal_bot import config
from deal_bot.ai.client import _call_openrouter, _strict_json_response_format

# Strict structured-output schema: exactly n integers, each 1-10.
_SCORER_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1, "maximum": 10},
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

# Per-item output-token derivation: '"9",' + newline ≈ 4 tokens; 2x margin.
_SCORER_TOKENS_PER_ITEM = 8
_SCORER_TOKEN_OVERHEAD = 200

# The bullet branch ([-–—*•]\s+) deliberately also accepts a negative-looking
# "- 5" as score 5 — the same shape as the "- 9" markdown bullet; scores are
# never negative in the 1-10 rubric, so this is a no-op in practice.
_SCORE_LINE = re.compile(
    r"(?m)^\s*(?:[-–—*•]\s+|\d+[.):]\s+)?(10|[1-9])(?:\s*/\s*10)?\s*$"
)


def _parse_scores_json(response: str | None, n: int) -> list[int] | None:
    """Strict JSON path: {"items": [int, ...]} with EXACTLY n integers,
    each 1-10. Returns None on anything else — the lenient line-anchored
    regex (_extract_scores) remains as defense-in-depth for models that
    ignore response_format, mirroring the classifier/categorizer pattern."""
    if not response:
        return None
    try:
        parsed = json.loads(response)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    items = parsed.get("items")
    if not (isinstance(items, list) and len(items) == n):
        return None
    # bool is an int subclass — exclude it explicitly.
    if all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 10 for v in items):
        return items
    return None


def _extract_scores(response: str) -> list[int]:
    """Line-anchored 1-10 score extraction. Each non-empty line must
    contain *only* a score (with optional bullet/numbered prefix and an
    optional `/10` suffix); any other content on the line rejects it.

    Replaces the previous greedy `findall` over the whole response, which
    would silently lift scores out of contaminated narration
    ("WD 10TB ... $159.99. 9"). The caller now requires
    `len(extracted) == len(deals)` — partial salvage is intentionally gone.
    """
    return [int(m) for m in _SCORE_LINE.findall(response)]


def score_deals(deals: list[dict]) -> tuple[dict[str, int], str | None]:
    """Returns ({deal_id: score}, model_used). One batched call, fail-open.

    Fails OPEN: if both models error or the response cannot be parsed to
    exactly len(deals) line-anchored scores, an empty score map and None
    are returned — which the caller treats as "everything passes," since
    a wrong DROP would be an invisible lost deal while a wrong KEEP is
    just a visible, ignorable post. Partial salvage is intentionally
    removed: a response that doesn't yield exactly one score per deal
    for every deal falls through to the fallback model.
    """
    if not deals:
        return {}, None
    if not config.OPENROUTER_API_KEY:
        return {}, None

    lines = [
        f"{i}. [{d['source']}] {d['title']} — {d['discount_pct']}% off, ${d['sale_price']:.2f}"
        for i, d in enumerate(deals, start=1)
    ]
    user_prompt = "\n".join(lines)
    max_tokens = _SCORER_TOKEN_OVERHEAD + len(deals) * _SCORER_TOKENS_PER_ITEM

    # Dedupe while preserving order: an operator pointing both chain slots
    # at the same model must not get charged two identical calls.
    models = list(dict.fromkeys((
        config.OPENROUTER_QUALITY_SCORER_MODEL,
        config.OPENROUTER_QUALITY_SCORER_FALLBACK_MODEL,
    )))
    for model in models:
        response = _call_openrouter(
            model, config.OPENROUTER_QUALITY_SCORER_SYSTEM_PROMPT, user_prompt,
            temperature=0.1, max_tokens=max_tokens,
            # Explicitly disabled (not merely omitted): reasoning-capable
            # models burn their token budget on the reasoning trace and
            # truncate the JSON — the observed 10-of-73 failure.
            reasoning={"enabled": False},
            response_format=_strict_json_response_format(
                "deal_quality_scorer", _SCORER_SCHEMA),
        )
        if not response:
            continue

        # Strict path first: schema-shaped JSON, exactly len(deals) scores.
        scores_list = _parse_scores_json(response, len(deals))

        # Lenient path: line-anchored regex extraction. Accepts bullets,
        # numbered prefixes, and `/10` suffixes. Requires the extracted
        # token count to EXACTLY equal len(deals) — no partial salvage.
        if scores_list is None:
            extracted = _extract_scores(response)
            if len(extracted) == len(deals):
                scores_list = extracted

        if scores_list is not None:
            return {d["id"]: score for d, score in zip(deals, scores_list)}, model
        print(
            f"[openrouter] quality scorer response from {model} didn't yield "
            f"exactly {len(deals)} clean scores — trying next"
        )

    print("[openrouter] quality scorer unavailable from both models this run — no shadow report")
    return {}, None