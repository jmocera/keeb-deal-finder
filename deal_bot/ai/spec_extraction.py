"""Clean title + spec extraction via OpenRouter.

Turns a messy retail title into a concise product name plus up to 4 short
technical specs, feeding both the Discord embed and the caption prompt.

Batching (`extract_clean_specs_batch`) is strictly bounded: ONE call per
model (primary, then fallback — each validated inside the model loop for
parse, shape, and item cardinality), and on total failure deterministic
defaults (raw title + empty specs). It NEVER fans out to per-item calls —
a degraded batch must not become an unbounded retry storm. The standalone
`extract_clean_specs()` keeps its own two-model chain for deliberate
single-item callers. Reasoning is explicitly disabled for extraction —
reasoning-capable models burn their entire token budget on the reasoning
trace and return empty content otherwise.
"""

import json
import re

from deal_bot import config
from deal_bot.ai.client import _call_openrouter, _strict_json_response_format

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

# Strict structured-output schema for the batch extraction: all fields
# required, no extras, specs capped at 4 — matching the validation rules.
_SPEC_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "clean_title": {"type": "string"},
        "specs": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    },
    "required": ["clean_title", "specs"],
    "additionalProperties": False,
}
_SPEC_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": _SPEC_ITEM_SCHEMA},
    },
    "required": ["items"],
    "additionalProperties": False,
}

# Batched analog of SPEC_EXTRACTION_SYSTEM_PROMPT (in config): same rules,
# but for a numbered list, returned as a single JSON object of items.
_BATCH_SPEC_SYSTEM_PROMPT = """You clean up messy retail product titles for a deal-finding bot focused on mechanical keyboards and keyboard hardware. You'll be given a numbered list of raw titles. For each, extract a clean, concise product name and up to 4 short technical specs.

Rules:
- Never invent a spec that isn't explicitly present or clearly implied in the input. If there is genuinely nothing worth calling out, use an empty specs list.
- clean_title: under 100 characters.
- specs: 0 to 4 short strings, each under 60 characters (e.g. "Switch type: linear").

Respond with only a JSON object in this exact shape, with EXACTLY one item per input line, in the same order:
{"items": [{"clean_title": string, "specs": [string, ...]}, ...]}"""


def _parse_json_object(content: str | None) -> dict | None:
    """Try to turn model output into a JSON object, twice: a direct parse,
    then a lenient parse (find the first `{...}` block) for fallback models
    that return JSON wrapped in prose or markdown."""
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    match = _JSON_OBJECT_RE.search(content)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _validate_result(title: str, parsed: dict) -> dict:
    """Per-field validation identical to the historical extract_clean_specs —
    a bad clean_title falls back to the raw title, bad specs fall back to
    empty, independently of each other."""
    clean_title = parsed.get("clean_title")
    if isinstance(clean_title, str) and clean_title.strip() and len(clean_title.strip()) <= 100:
        clean_title = clean_title.strip()
    else:
        print(f"[openrouter] spec extraction clean_title failed validation: {clean_title!r}")
        clean_title = title

    specs = parsed.get("specs")
    if (isinstance(specs, list) and len(specs) <= 4
            and all(isinstance(s, str) and s.strip() and len(s.strip()) <= 60 for s in specs)):
        specs = [s.strip() for s in specs]
    else:
        print(f"[openrouter] spec extraction specs failed validation: {specs!r}")
        specs = []

    return {"clean_title": clean_title, "specs": specs}


def extract_clean_specs(title: str, description: str = "") -> dict:
    """Cleans up a messy retail title into a concise product name plus up
    to 4 short technical specs, via OpenRouter. Fails open at every stage:
    no API key, network errors, malformed JSON, an unusable top-level
    response, or a single bad field all fall back to safe defaults without
    ever blocking a post. Must never be able to block a post."""
    fallback = {"clean_title": title, "specs": []}

    user_prompt = f"Title: {title}"
    if description:
        user_prompt += f"\nDescription: {description}"

    # Try the primary model, then the fallback model. Both support
    # structured output, so request JSON directly from each; the lenient
    # parse in _parse_json_object covers any model that ignores it.
    content = None
    for model in (config.OPENROUTER_SPEC_EXTRACTION_MODEL, config.OPENROUTER_SPEC_FALLBACK_MODEL):
        content = _call_openrouter(
            model, config.SPEC_EXTRACTION_SYSTEM_PROMPT, user_prompt,
            temperature=0.0, max_tokens=200, timeout=5,
            response_format={"type": "json_object"},
            # Deliberately omitted: reasoning. Historically the Gemini model
            # burned its entire token budget on internal reasoning when any
            # effort level was set (returning truncated garbage instead of
            # JSON); omitting the parameter entirely is what made it
            # reliable. Qwen 3.7 Flash works either way, so we keep it
            # omitted — simplest and cheapest. Re-verify empirically before
            # setting it for a new model.
        )
        if content:
            break

    parsed = _parse_json_object(content)
    if parsed is None:
        print(f"[openrouter] spec extraction response didn't parse as a JSON object: {content!r}")
        return fallback

    return _validate_result(title, parsed)


def extract_clean_specs_batch(titles: list[str]) -> list[dict]:
    """Batched version of extract_clean_specs — ONE call per model for N
    titles (at most two OpenRouter calls total, never per-item). Each
    model's response is validated inside the model loop: unparseable,
    structurally invalid, or wrong-item-count responses fall through to the
    next model. If both models fail, returns deterministic defaults
    ([{"clean_title": original_title, "specs": []}, ...]) — a degraded batch
    never fans out into an unbounded per-item retry storm."""
    if not titles:
        return []
    if not config.OPENROUTER_API_KEY:
        return [{"clean_title": t, "specs": []} for t in titles]

    user_prompt = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    max_tokens = min(4000, 400 + len(titles) * 60)
    # Dedupe while preserving order: an operator pointing both chain slots
    # at the same model must not get charged two identical calls.
    models = list(dict.fromkeys((
        config.OPENROUTER_SPEC_EXTRACTION_MODEL,
        config.OPENROUTER_SPEC_FALLBACK_MODEL,
    )))
    for model in models:
        content = _call_openrouter(
            model, _BATCH_SPEC_SYSTEM_PROMPT, user_prompt,
            temperature=0.0, max_tokens=max_tokens, timeout=30,
            response_format=_strict_json_response_format(
                "spec_extraction_batch", _SPEC_BATCH_SCHEMA),
            # Deliberately disabled (not merely omitted): reasoning-capable
            # models burn their entire token budget on the reasoning trace
            # and return empty content. Re-verify empirically before
            # re-enabling for a new model.
            reasoning={"enabled": False},
        )
        parsed = _parse_json_object(content) or {}
        items = parsed.get("items")
        if (isinstance(items, list) and len(items) == len(titles)
                and all(isinstance(x, dict) for x in items)):
            return [_validate_result(t, item) for t, item in zip(titles, items)]
        print(f"[openrouter] batch spec extraction from {model} unusable — trying next")

    print("[openrouter] batch spec extraction unusable — deterministic raw-title defaults")
    return [{"clean_title": t, "specs": []} for t in titles]