"""Consolidated per-deal verdicts — ONE batched OpenRouter call producing
both the short social caption and the longer Discord "Analysis" text.

These were previously two separate call chains over the identical context
(captions.py per-deal, deal_analyst.py batched); merging them halves the
LLM round-trips per run and keeps tone consistent between Discord and
Bluesky. The per-deal caption budget depends on that deal's URL length
(see post_len.caption_budget), so each prompt line carries its own limit
and each item is validated independently against its own budget.

Fallback chain, never worse than the previous behavior: batch response →
per-item validation (bad caption → the existing per-deal LLM caption chain
which ends in the mechanical template; bad analysis → "") → whole-batch
garbage → the per-deal functions for every item. Never raises, never
blocks a post.
"""

import json
import re

from deal_bot import config
from deal_bot.ai.captions import _hashtags_look_reasonable, build_ai_caption_body
from deal_bot.ai.client import _call_openrouter
from deal_bot.ai.deal_analyst import _format_deal_lines
from deal_bot.post_len import caption_budget

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

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
    budget. A bad caption falls back to the per-deal caption chain (which
    itself ends in the mechanical template); a bad analysis falls back to
    "" — independently of each other."""
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
              f"(budget={budget}) — falling back to the per-deal caption chain")
        caption = build_ai_caption_body(deal)

    return {"caption": caption, "analysis": analysis}


def build_verdicts_batch(deals: list[dict]) -> list[dict]:
    """One batched call for N deals → [{"caption": str, "analysis": str}].
    Aligned 1:1 with the input list, always the same length, never raises."""
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
    items = None
    for model in (config.OPENROUTER_PRIMARY_MODEL, config.OPENROUTER_FALLBACK_MODEL):
        content = _call_openrouter(
            model, _VERDICTS_SYSTEM_PROMPT, user_prompt,
            temperature=0.4, reasoning={"effort": "low"}, max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        parsed = _parse_verdicts(content)
        if parsed is not None and len(parsed) == len(deals):
            items = parsed
            break
        print(f"[openrouter] batch verdicts from {model} unusable — trying next")

    # Whole-batch failure → per-item fallback for every deal (the per-deal
    # caption chain + empty analysis), so a degraded batch never produces
    # worse output than the previous two-module behavior.
    if items is None:
        print("[openrouter] batch verdicts unusable — falling back to per-item")
        items = [None] * len(deals)

    return [_validate_item(deal, item) for deal, item in zip(deals, items)]
