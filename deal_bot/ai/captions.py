"""AI caption "verdicts" for Bluesky and the private-channel mirror."""

import re

from deal_bot import config
from deal_bot.ai.client import _call_openrouter
from deal_bot.display import discount_str, price_str
from deal_bot.post_len import caption_budget

# A hashtag is '#' followed by 1-30 word characters containing at least one
# Unicode alphabetic character — numeric/underscore-only references (#420,
# ___) are product text, not discoverable tags. This shared pattern drives
# Bluesky facet generation (integrations/bluesky.py consumes capture group 1
# as the tag name and match.start()/end() for UTF-8 byte offsets — both
# unchanged by the lookahead) and the validator's prose scan.
_HASHTAG_PATTERN = re.compile(r"#(?=\w*[^\W\d_])(\w+)")
# A fully valid hashtag TOKEN: the same alphabetic-tag shape as
# _HASHTAG_PATTERN with the 1-30 length cap enforced on the whole token
# (trailing punctuation breaks the fullmatch and invalidates the tag).
_VALID_HASHTAG_TOKEN = re.compile(r"#(?=\w*[^\W\d_])\w{1,30}")

# Deterministic mechanical-hashtag suffixes, keyed by the repository's
# existing category vocabulary (config.DEAL_CATEGORIES as produced by the
# categorizer). "other" and unclassifiable items share the safe generic
# two-tag pair — an unknown item still gets keyboard-deals discoverability
# rather than no hashtags.
_MECHANICAL_TAG_SUFFIXES = {
    "board": "#KeebDeals #MechanicalKeyboards #KeyboardBuilds",
    "keycaps": "#KeebDeals #Keycaps #MechanicalKeyboards",
    "switch": "#KeebDeals #KeyboardSwitches #MechanicalKeyboards",
    "accessory": "#KeebDeals #KeebAccessories #MechanicalKeyboards",
    "other": "#KeebDeals #MechanicalKeyboards",
}

# Title-based classification, most specific item type first so generic
# "keyboard" wording never shadows keycaps/switches/accessories. Mirrors the
# categorizer prompt's definitions: stabilizers are switch mechanics, cases/
# plates/PCBs are board components, cables/mats/rests are accessories.
_MECHANICAL_TAG_RULES = [
    ("keycaps", re.compile(r"\bkey\s?caps?\b|\bkeysets?\b|\bartisan\b", re.IGNORECASE)),
    ("switch", re.compile(
        r"\bswitch(es)?\b|\bstabilizers?\b|\bstems?\b|\bsprings?\b|\blub(e|ing)\b",
        re.IGNORECASE,
    )),
    ("accessory", re.compile(
        r"\bcables?\b|\bcoiled\b|\baviator\b|\bdesk\s?mats?\b|\bwrist\s?rests?\b"
        r"|\bpullers?\b|\bcleaning\b",
        re.IGNORECASE,
    )),
    ("board", re.compile(
        r"\bkeyboards?\b|\bbarebones?\b|\bhot-?swaps?\b|\bpcbs?\b|\bplates?\b|\bcases?\b",
        re.IGNORECASE,
    )),
]


def build_x_caption(deal: dict) -> str:
    """Plain-text, X-ready caption. No markdown — X doesn't render it.
    Trim manually before posting if it runs long; titles vary in length
    so this can't guarantee staying under X's character limit."""
    return build_x_caption_body(deal) + "\n" + deal["url"]


def build_x_caption_body(deal: dict) -> str:
    """Mechanical template body (no URL appended) — the same discount/price
    line build_x_caption() has always produced, now always ending with a
    deterministic 2-3 tag suffix so the hashtag-free-fallback defect can't
    recur. Split from build_x_caption() so the Bluesky fitter can budget
    the URL separately."""
    discount = discount_str(deal["discount_pct"])
    price = price_str(deal["sale_price"], deal["list_price"])
    display_title = deal.get("clean_title") or deal["title"]
    body = f"{discount} — {display_title} — {price}"
    return f"{body} {_mechanical_hashtag_suffix(deal)}"


def _mechanical_hashtag_suffix(deal: dict) -> str:
    """Deterministic, AI-free hashtag suffix (2-3 short, space-separated
    tags) for the mechanical fallback caption.

    Selection order:
      1. deal["category"], but ONLY when it is one of the repository's
         existing category values (config.DEAL_CATEGORIES vocabulary);
         any other value is ignored rather than guessed at.
      2. Conservative title/clean_title matching, most specific item type
         first (keycaps -> switches -> accessories -> boards), so e.g. a
         "keyboard cable" classifies as an accessory, not a board.
      3. Safe generic keyboard-deals fallback for unknown items.
    """
    category = deal.get("category")
    if isinstance(category, str):
        suffix = _MECHANICAL_TAG_SUFFIXES.get(category.strip().lower())
        if suffix:
            return suffix
    text = f"{deal.get('clean_title') or ''} {deal.get('title') or ''}"
    for category, pattern in _MECHANICAL_TAG_RULES:
        if pattern.search(text):
            return _MECHANICAL_TAG_SUFFIXES[category]
    return _MECHANICAL_TAG_SUFFIXES["other"]


def _hashtags_look_reasonable(text: str) -> bool:
    """Strict trailing-block validation for any accepted AI caption:
      - 2 to 4 hashtags, and they must be the FINAL whitespace-separated
        tokens of the caption (both the Bluesky tag facets and the post
        fitter treat the trailing run as the tag block);
      - each tag is '#' plus 1-30 word characters containing at least one
        Unicode alphabetic character (so #KeychronQ1 and #3DPrinting count
        but #420 and #___ do not), with nothing attached — a tag with
        trailing punctuation is invalid, not salvageable;
      - no hashtag may appear anywhere before that final block — a
        mid-sentence tag is prose, not a discoverable tail, and would
        silently break the ends-with-hashtags contract;
      - no case-insensitive duplicate hashtags;
      - still rejects any model-injected URL (the link is appended in
        code, and a sneaked URL would consume budget and mis-target the
        link facet).
    Item-specific tags are deliberately NOT restricted to an allowlist —
    the model is trusted to pick contextually relevant tags per item."""
    if re.search(r"https?://\S+", text):
        return False
    tokens = text.rstrip().split()
    trailing = []
    for token in reversed(tokens):
        if _VALID_HASHTAG_TOKEN.fullmatch(token):
            trailing.append(token)
        else:
            break
    if not 2 <= len(trailing) <= 4:
        return False
    if any(_HASHTAG_PATTERN.search(tok) for tok in tokens[: len(tokens) - len(trailing)]):
        return False
    names = [token[1:] for token in trailing]
    lowered = {name.casefold() for name in names}
    return len(lowered) == len(names)


def build_ai_caption(deal: dict) -> str:
    """Caption body plus the URL line — the public contract used by the
    private-channel mirror and callers that want the URL appended."""
    return build_ai_caption_body(deal) + "\n" + deal["url"]


def build_ai_caption_body(deal: dict) -> str:
    """Tries OPENROUTER_PRIMARY_MODEL, then OPENROUTER_FALLBACK_MODEL, then
    the plain build_x_caption_body() template if both fail or
    OPENROUTER_API_KEY isn't set — this must never be able to block a post
    from going out. Returns the caption WITHOUT the URL; the caller appends
    it (via fit_deal_post) so the LLM can't alter it and break the Bluesky
    link facet.

    Feeds the model concrete, already-verified signals (clean title,
    specs, and price-history context from Supabase) so it acts as an
    analytical synthesizer of real data rather than an ungrounded
    copywriter — see config.OPENROUTER_CAPTION_SYSTEM_PROMPT."""
    discount = discount_str(deal["discount_pct"])
    price = price_str(deal["sale_price"], deal["list_price"])
    display_title = deal.get("clean_title") or deal["title"]
    specs = deal.get("specs") or []

    prompt_lines = [
        f"Deal source: {deal['source']}",
        f"Item: {display_title}",
    ]
    if specs:
        prompt_lines.append(f"Known specs: {', '.join(specs)}")
    prompt_lines.append(f"Discount: {discount}")
    prompt_lines.append(f"Price: {price}")
    # Price-history context from Supabase (see pipeline._process_deals) —
    # only ever a fact the model is told, never something it has to infer.
    if deal.get("is_new_low"):
        prompt_lines.append("Price history: this is a new all-time low for this exact item.")
    elif deal.get("lowest_price") is not None and deal["lowest_price"] < deal["sale_price"]:
        prompt_lines.append(f"Price history: the lowest ever tracked for this item was ${deal['lowest_price']:.2f}.")
    prompt_lines.append("")
    prompt_lines.append("Write the verdict.")
    user_prompt = "\n".join(prompt_lines)

    budget = caption_budget(deal["url"])  # 297 - len(url); body must fit with URL line
    system_prompt = config.OPENROUTER_CAPTION_SYSTEM_PROMPT
    system_prompt += f"\n\nThe link is added automatically. Keep your entire output (including hashtags) under {budget} characters total."

    for model in (config.OPENROUTER_PRIMARY_MODEL, config.OPENROUTER_FALLBACK_MODEL):
        caption = _call_openrouter(
            model, system_prompt, user_prompt,
            # "Explain why this is actually noteworthy" is a more
            # demanding ask than the old "write an engaging caption" —
            # confirmed in testing this needs more headroom than 350
            # tokens even at "low" reasoning effort, or it truncates
            # mid-sentence before finishing (still fractions of a cent).
            temperature=0.4, reasoning={"effort": "low"}, max_tokens=600,
        )
        if caption and len(caption) <= budget and _hashtags_look_reasonable(caption):
            return caption
        if caption:
            print(f"[openrouter] {model} caption failed validation (len={len(caption)}, budget={budget}), trying next")

    return build_x_caption_body(deal)  # last resort: mechanical template