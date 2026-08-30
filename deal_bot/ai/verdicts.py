"""Consolidated per-deal verdicts — ONE batched OpenRouter call producing
both the short social caption and the longer Discord "Analysis" text.

These were previously two separate call chains over the identical context
(captions.py per-deal, deal_analyst.py batched); merging them halves the
LLM round-trips per run and keeps tone consistent between Discord and
Bluesky. The per-deal caption budget depends on that deal's URL length
(see post_len.caption_budget), so each prompt line carries its own limit
and each item is validated independently against its own budget.

Strictly bounded call count: at most ONE call per model (primary, then
fallback — each validated inside the model loop for parse, shape, and item
cardinality). A bad caption/analysis within an otherwise-valid batch, or a
total batch failure, falls back to the deterministic mechanical template
plus empty analysis — it NEVER fans out into per-deal AI calls. Never
raises, never blocks a post.
"""

import json
import re

from deal_bot import config
from deal_bot.ai.captions import _hashtags_look_reasonable, build_x_caption_body
from deal_bot.ai.client import _call_openrouter, _strict_json_response_format
from deal_bot.ai.deal_analyst import _format_deal_lines
from deal_bot.post_len import caption_budget

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Strict structured-output schema for the batch verdicts: all fields
# required, no extras — matching the per-item validation rules.
_VERDICT_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "analysis": {"type": "string"},
    },
    "required": ["caption", "analysis"],
    "additionalProperties": False,
}
_VERDICTS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": _VERDICT_ITEM_SCHEMA},
    },
    "required": ["items"],
    "additionalProperties": False,
}

_VERDICTS_SYSTEM_PROMPT = """You write two pieces of output for each deal in a numbered list, for a deal-finding bot aimed at mechanical-keyboard builders and enthusiasts. You'll be given each deal's source, item, known specs, discount, price, and optional price-history/value-metric context, plus a per-item character limit for the caption.

For each item write:
- "caption": 1-2 concise sentences explaining *why* this deal is actually noteworthy — a real price-history signal, real value-for-money given the specs given, or a specific use case those specs support. End with 2 to 4 relevant, space-separated hashtags chosen specifically for this item. Stay under that item's stated caption character limit (the limit includes the hashtags; the link is added automatically, so never include a URL).
- "analysis": 2-3 concise sentences on what build or use case the item fits, whether the price is strong for the specs given, and which spec(s) matter for that use case. Under 350 characters.

Take a direct, analytical, enthusiast tone for both. Do not use hype phrases like "insane deal" or "act now." Never state a spec, benchmark, number, or price that wasn't explicitly given — if you don't have enough information to say something specific and true, keep it simple rather than inventing detail.

Respond with ONLY a JSON object in this exact shape, with EXACTLY one item per input line, in the same order:
{"items": [{"caption": string, "analysis": string}, ...]}"""


def _parse_verdicts(content: str | None) -> list[dict] | None:
    """Turn model output into a list of {caption, analysis} dicts, or None.
    Direct JSON parse first, then a lenient first-{...}-block parse for
    fallback models that wrap JSON in prose/markdown."""
    if not content:
        return None
    parsed = None
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        parsed = None
    if not isinstance(parsed, dict):
        match = _JSON_OBJECT_RE.search(content)
        if not match:
            return None
        try:
            parsed = json.loads(match.group())
        except (ValueError, TypeError):
            return None
    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not (isinstance(items, list) and all(isinstance(x, dict) for x in items)):
        return None
    return items


def _validate_item(deal: dict, item: dict | None) -> dict:
    """Per-item independent validation against that deal's own caption
    budget. A bad caption or analysis falls back to deterministic values
    (the mechanical template body; "") — never a per-deal AI call, so a
    batch can never fan out into unbounded LLM traffic."""
    budget = caption_budget(deal["url"])

    caption = None
    analysis = ""
    if isinstance(item, dict):
        raw_caption = item.get("caption")
        if (isinstance(raw_caption, str) and raw_caption.strip()
                and len(raw_caption.strip()) <= budget
                and _hashtags_look_reasonable(raw_caption.strip())):
            caption = raw_caption.strip()
        raw_analysis = item.get("analysis")
        if isinstance(raw_analysis, str) and 0 < len(raw_analysis.strip()) <= 380:
            analysis = raw_analysis.strip()

    if caption is None:
        print(f"[openrouter] verdict caption for {deal['id']} failed validation "
              f"(budget={budget}) — using the mechanical template")
        caption = build_x_caption_body(deal)

    return {"caption": caption, "analysis": analysis}


def build_verdicts_batch(deals: list[dict]) -> list[dict]:
    """One batched call per model for N deals → [{"caption": str,
    "analysis": str}]. At most two OpenRouter calls total (primary, then
    fallback — deduped when both slots name the same model), each validated
    inside the model loop for parse, shape, and item cardinality. A
    degraded batch produces deterministic mechanical captions + empty
    analysis with ZERO further OpenRouter calls. Aligned 1:1 with the input
    list, always the same length, never raises."""
    if not deals:
        return []
    if not config.OPENROUTER_API_KEY:
        return [_validate_item(d, None) for d in deals]

    lines = []
    for i, deal in enumerate(deals, start=1):
        budget = caption_budget(deal["url"])
        lines.append(f"{i}. " + " | ".join(_format_deal_lines(deal))
                     + f" (caption limit: {budget} characters)")
    user_prompt = "\n".join(lines)

    max_tokens = min(10000, 400 + len(deals) * 340)
    # Reasoning at "low" only when the output budget reserves enough room
    # for BOTH the reasoning trace and the final JSON: a reasoning-capable
    # model can burn ~1K+ tokens thinking, and a small batch's budget
    # (1 deal ≈ 740 tokens) would leave the JSON truncated/empty. Large
    # batches get proportionally larger budgets, so reasoning is safe there.
    reasoning = {"effort": "low"} if max_tokens >= 2048 else None

    # Dedupe while preserving order: an operator pointing both chain slots
    # at the same model must not get charged two identical calls.
    models = list(dict.fromkeys((
        config.OPENROUTER_PRIMARY_MODEL,
        config.OPENROUTER_FALLBACK_MODEL,
    )))
    items = None
    for model in models:
        content = _call_openrouter(
            model, _VERDICTS_SYSTEM_PROMPT, user_prompt,
            temperature=0.4, reasoning=reasoning, max_tokens=max_tokens,
            response_format=_strict_json_response_format("deal_verdicts", _VERDICTS_SCHEMA),
        )
        parsed = _parse_verdicts(content)
        if parsed is not None and len(parsed) == len(deals):
            items = parsed
            break
        print(f"[openrouter] batch verdicts from {model} unusable — trying next")

    # Whole-batch failure → deterministic mechanical captions + empty
    # analysis. NEVER a per-deal AI fallback: a 124-deal run with a broken
    # batch must cost exactly two model calls, not 124+.
    if items is None:
        print("[openrouter] batch verdicts unusable — deterministic mechanical captions")
        return [_validate_item(deal, None) for deal in deals]

    return [_validate_item(deal, item) for deal, item in zip(deals, items)]
