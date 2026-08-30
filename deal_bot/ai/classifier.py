"""Shadow/gate-mode desirability classifier.

Default CLASSIFIER_MODE is "shadow" (reports to a Discord channel, gates
nothing); setting the CLASSIFIER_MODE Variable to "gate" makes pipeline
Phase B remove DROP-verdict candidates before posting.

Bounded batch, same pattern as spec_extraction/verdicts: strict
json_schema structured output, at most ONE call per model (primary, then
fallback — deduped when both slots name the same model), each response
validated inside the model loop for parse, shape, and EXACT item
cardinality. A truncated/partial response is never accepted; a degraded
batch never fans out into per-item calls; total failure keeps every deal
(fail-open) and simply omits the shadow report / gating for the run.

Token budget: sized from the schema's realistic per-item maximum (a
KEEP/DROP enum token with JSON syntax ≈ 6 output tokens; 12/item with
2x margin) and a fixed ~200-token JSON-scaffolding overhead — with NO
artificial cap. The old min(1500, ...) cap plus low reasoning truncated a
73-deal run to 72 verdicts. Chunking is deliberately unnecessary: the
worst realistic batch (124 items) needs ≈ 1.7K output tokens, far below
provider output ceilings.
"""

import json
import re

from deal_bot import config
from deal_bot.ai.client import _call_openrouter, _strict_json_response_format

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Strict structured-output schema: every item must be exactly KEEP or DROP.
_CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {"type": "string", "enum": ["KEEP", "DROP"]},
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

# Per-item output-token derivation: '"DROP",' + newline ≈ 6 tokens; 2x
# margin. Fixed overhead covers the {"items": [...]} scaffolding.
_CLASSIFIER_TOKENS_PER_ITEM = 12
_CLASSIFIER_TOKEN_OVERHEAD = 200


def _parse_keep_drop(response: str | None) -> list[str] | None:
    """Strict JSON path first ({"items": ["KEEP", ...]}), then the lenient
    line-based parse for fallback models that ignore response_format.
    Returns None when neither yields only KEEP/DROP tokens."""
    if not response:
        return None
    try:
        parsed = json.loads(response)
    except (ValueError, TypeError):
        parsed = None
    if not isinstance(parsed, dict):
        match = _JSON_OBJECT_RE.search(response)
        if match:
            try:
                parsed = json.loads(match.group())
            except (ValueError, TypeError):
                parsed = None
    if isinstance(parsed, dict):
        items = parsed.get("items")
        if (isinstance(items, list) and items
                and all(isinstance(v, str) and v.strip().upper() in ("KEEP", "DROP") for v in items)):
            return [v.strip().upper() for v in items]
    # Lenient fallback: one KEEP/DROP word per line, in order.
    verdicts = [line.strip().upper() for line in response.strip().splitlines() if line.strip()]
    if verdicts and all(v in ("KEEP", "DROP") for v in verdicts):
        return verdicts
    return None


def classify_desirable_deals(deals: list[dict]) -> tuple[list[dict], list[dict], str | None]:
    """One batched OpenRouter call judging whether each deal is genuinely
    desirable to a mechanical-keyboard building/enjoying audience, beyond
    just having cleared the keyword/discount filters. Returns (keep, drop,
    model_used).

    Fails OPEN: if both models error or the response doesn't parse
    cleanly (wrong item count, anything other than KEEP/DROP), everything
    is kept. A wrong KEEP is a mediocre post; a wrong DROP would be an
    invisible lost deal — so whether shadow-reporting or actually gating,
    keeping everything is the safer failure direction. A failed call just
    means no report/no gating this run."""
    if not deals:
        return [], [], None
    if not config.OPENROUTER_API_KEY:
        return list(deals), [], None

    lines = [
        f"{i}. [{d['source']}] {d['title']} — {d['discount_pct']}% off, ${d['sale_price']:.2f}"
        for i, d in enumerate(deals, start=1)
    ]
    user_prompt = "\n".join(lines)
    max_tokens = _CLASSIFIER_TOKEN_OVERHEAD + len(deals) * _CLASSIFIER_TOKENS_PER_ITEM

    # Dedupe while preserving order: an operator pointing both chain slots
    # at the same model must not get charged two identical calls.
    models = list(dict.fromkeys((
        config.OPENROUTER_PRIMARY_MODEL,
        config.OPENROUTER_FALLBACK_MODEL,
    )))
    for model in models:
        response = _call_openrouter(
            model, config.OPENROUTER_CLASSIFIER_SYSTEM_PROMPT, user_prompt,
            temperature=0.1, max_tokens=max_tokens,
            # Explicitly disabled (not merely omitted): reasoning-capable
            # models burn their budget on the reasoning trace and truncate
            # the JSON — the observed 72-of-73 verdicts failure. Trivial
            # KEEP/DROP judgments need no reasoning.
            reasoning={"enabled": False},
            response_format=_strict_json_response_format(
                "desirability_classifier", _CLASSIFIER_SCHEMA),
        )
        verdicts = _parse_keep_drop(response)
        if verdicts is None or len(verdicts) != len(deals):
            print(
                f"[openrouter] classifier response from {model} didn't parse cleanly "
                f"({len(verdicts) if verdicts else 0} verdicts for {len(deals)} deals) — trying next"
            )
            continue
        keep = [d for d, v in zip(deals, verdicts) if v == "KEEP"]
        drop = [d for d, v in zip(deals, verdicts) if v == "DROP"]
        return keep, drop, model

    print("[openrouter] classifier unavailable from both models this run — failing open")
    return list(deals), [], None