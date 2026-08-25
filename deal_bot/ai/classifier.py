"""Shadow/gate-mode desirability classifier.

Default CLASSIFIER_MODE is "shadow" (reports to a Discord channel, gates
nothing); setting the CLASSIFIER_MODE Variable to "gate" makes pipeline
Phase B remove DROP-verdict candidates before posting.
"""

import json
import re

from deal_bot import config
from deal_bot.ai.client import _call_openrouter

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


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
    # A generous floor, not just a per-item scale-up: reasoning overhead
    # doesn't shrink proportionally with a smaller item count, and a
    # too-tight budget reproduces the same null-content failure the
    # caption path hit before its fix — confirmed in testing.
    max_tokens = min(1500, 300 + len(deals) * 15)

    for model in (config.OPENROUTER_PRIMARY_MODEL, config.OPENROUTER_FALLBACK_MODEL):
        response = _call_openrouter(
            model, config.OPENROUTER_CLASSIFIER_SYSTEM_PROMPT, user_prompt,
            temperature=0.1, max_tokens=max_tokens, reasoning={"effort": "low"},
            response_format={"type": "json_object"},
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