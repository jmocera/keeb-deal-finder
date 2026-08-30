"""Central configuration for the deal bot.

Every environment variable and tuning constant lives here, so the rest of
the package imports from `deal_bot.config` instead of reading `os.environ`
or hardcoding numbers in scattered places. Other modules reference values
at call time (e.g. `config.MIN_DISCOUNT_PERCENT`) rather than importing them
as names, so tests can monkeypatch a config attribute and have it take
effect without re-importing the module.

Values come from the `.env` file in the repo root locally, or from real
environment variables (GitHub Actions repo secrets/variables) on schedule.
"""

import json as _json
import os
from pathlib import Path

from dotenv import load_dotenv

# Loads variables from the repo-root `.env` file into the environment. In
# GitHub Actions there is no .env file — the same names are injected as
# real environment variables from repo secrets/variables instead, and this
# call is simply a no-op there.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ---------------------------------------------------------------------------
# API / service credentials
# ---------------------------------------------------------------------------
WOOT_API_KEY = os.environ.get("WOOT_API_KEY", "")
BESTBUY_API_KEY = os.environ.get("BESTBUY_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
# Preferred: new-format sb_secret_... server key (Supabase "Secret keys").
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "")
# Legacy: classic service_role JWT, still honored as a fallback. When both
# are set, SUPABASE_SECRET_KEY wins (see get_supabase_key below).
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def get_supabase_key() -> str:
    """Effective Supabase API key, resolved at call time: SUPABASE_SECRET_KEY
    (the preferred sb_secret_... server key) takes precedence;
    SUPABASE_SERVICE_KEY is the legacy service_role JWT fallback. Returns ""
    when neither is set. Reads module globals at CALL time (not import time)
    so tests and CI can set/blank the attributes without re-importing."""
    return SUPABASE_SECRET_KEY or SUPABASE_SERVICE_KEY

# ---------------------------------------------------------------------------
# Discord webhooks
# ---------------------------------------------------------------------------
WOOT_WEBHOOK_URL = os.environ.get("WOOT_WEBHOOK_URL", "")
BESTBUY_WEBHOOK_URL = os.environ.get("BESTBUY_WEBHOOK_URL", "")
# Shopify's public /products.json storefronts need no API key or auth — a
# Discord webhook URL (same shape as the Woot/Best Buy ones), fed by the
# default Shopify stores when none are configured.
SHOPIFY_WEBHOOK_URL = os.environ.get("SHOPIFY_WEBHOOK_URL", "")
# Optional: mirrors every deal that posts publicly into a private,
# owner-only channel — handy as a staging area for manually picking what
# to share elsewhere. Leave unset to skip this entirely.
PRIVATE_WEBHOOK_URL = os.environ.get("PRIVATE_WEBHOOK_URL", "")
# Dedicated channel for the end-of-run digest, separate from the per-deal
# source channels above.
DIGEST_WEBHOOK_URL = os.environ.get("DIGEST_WEBHOOK_URL", "")
# Dedicated channel that mirrors every run_log row (see pipeline.log_run) —
# posts every run, success or failure, so a crash is actually visible
# somewhere instead of only being a silent row in Supabase.
RUN_LOG_WEBHOOK_URL = os.environ.get("RUN_LOG_WEBHOOK_URL", "")
# SHADOW MODE: reports what the desirability classifier would have
# kept/dropped, without actually gating real posts on it yet.
SHADOW_CLASSIFIER_WEBHOOK_URL = os.environ.get("SHADOW_CLASSIFIER_WEBHOOK_URL", "")
# SHADOW MODE: reports the deal quality scorer's 1-10 ratings (and what it
# would have dropped below MIN_QUALITY_SCORE), without actually gating posts.
SHADOW_QUALITY_SCORER_WEBHOOK_URL = os.environ.get("SHADOW_QUALITY_SCORER_WEBHOOK_URL", "")
# SHADOW MODE: reports the category tagger's per-deal classification,
# without actually using it to gate or route posts yet.
SHADOW_CATEGORIZER_WEBHOOK_URL = os.environ.get("SHADOW_CATEGORIZER_WEBHOOK_URL", "")

# ---------------------------------------------------------------------------
# Native Discord bot (always-on service). When guilds have /setup, the
# pipeline delivers via channel messages instead of source webhooks.
# ---------------------------------------------------------------------------
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_BOT_OWNER_ID = os.environ.get("DISCORD_BOT_OWNER_ID", "")
BOT_RUN_INTERVAL_SECONDS = int(os.environ.get("BOT_RUN_INTERVAL_SECONDS", "14400"))

# ---------------------------------------------------------------------------
# Bluesky — free API, no approval process. Only standout deals auto-post
# here (see BLUESKY_MIN_DISCOUNT_PERCENT below), and even among those,
# only the top BLUESKY_MAX_POSTS_PER_RUN by $ saved actually go out — to
# avoid looking like a spam firehose on a brand-new account.
# ---------------------------------------------------------------------------
BLUESKY_HANDLE = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD", "")
BLUESKY_MIN_DISCOUNT_PERCENT = int(os.environ.get("BLUESKY_MIN_DISCOUNT_PERCENT", "50"))
BLUESKY_MAX_POSTS_PER_RUN = int(os.environ.get("BLUESKY_MAX_POSTS_PER_RUN", "2"))
# Bluesky's server-side post cap is 300 grapheme clusters. Code points are
# always >= graphemes, so len()<=300 is strictly server-safe; BLUESKY_POST_MARGIN
# is belt-and-suspenders against off-by-ones in the fit/reassemble arithmetic.
BLUESKY_MAX_POST_LEN = int(os.environ.get("BLUESKY_MAX_POST_LEN", "300"))
BLUESKY_POST_MARGIN = int(os.environ.get("BLUESKY_POST_MARGIN", "2"))

# ---------------------------------------------------------------------------
# OpenRouter — AI-written captions for Bluesky and the private-channel
# copy-paste mirror, replacing the plain template. Tries the primary
# model, then the paid fallback model, then the plain template as a last
# resort — this must never be able to block a post from going out.
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# Primary + fallback for the caption/analysis/verdict chains. The fallback
# is a PAID model on purpose: the free Nemotron endpoint burned its whole
# token budget on reasoning traces (empty content → retry storms) and has
# been removed from the production chain.
OPENROUTER_PRIMARY_MODEL = os.environ.get("OPENROUTER_PRIMARY_MODEL", "deepseek/deepseek-v4-flash-0731")
OPENROUTER_FALLBACK_MODEL = os.environ.get("OPENROUTER_FALLBACK_MODEL", "google/gemini-2.5-flash-lite")
OPENROUTER_CAPTION_SYSTEM_PROMPT = """You write short, data-backed technical verdicts for a deal-finding bot aimed at mechanical-keyboard builders and enthusiasts — not marketing copy. You'll be given a product's clean title, its known specs (if any), current and list price, and price-history context (whether this is a new all-time low, or what the lowest tracked price has been).

Output ONLY the verdict text — no preamble, no explanation, no quotation marks, no markdown formatting, no code fences.

Write exactly 1-2 concise sentences explaining *why* this deal is actually noteworthy — a real price-history signal (e.g. a genuine all-time low), real value-for-money given the specs you were given, or a specific use case those specs support. Take a direct, analytical, enthusiast tone. Do not use hype phrases like "insane deal," "don't miss out," or "act now." Never state a spec, benchmark number, or feature that wasn't explicitly given to you — if you don't have enough information to say something specific and true, keep it simple rather than inventing detail.

Keep the entire output under 140 characters (including hashtags). End with 2 to 4 relevant, space-separated hashtags chosen specifically for this item — vary them based on what the deal actually is, don't reuse the same generic tags every time. Never include a URL or link.

Example, given a GMK keycap set at a new all-time low of $79.99 (was $159.99):
This is the lowest we've tracked this GMK set — a genuine all-time low, not just a markdown. Great doubleshot ABS at a real floor price. #Keycaps #KeebDeals #MKDeals"""

# Longer expert "analysis" for the Discord embed (complements the short
# Bluesky caption verdict above — same models, richer output, an optional
# enhancement that fails open to an empty string).
OPENROUTER_ANALYSIS_SYSTEM_PROMPT = """You write short expert analysis for a deal-finding bot aimed at mechanical-keyboard builders and enthusiasts. Given a product's clean title, known specs (if any), current and list price, and price-history context, write 2-3 concise sentences explaining what makes this deal genuinely noteworthy:

- What kind of build or use case it fits (e.g. a linear switch for a gaming board, a thick PBT keycap set for a daily driver, a 65% barebones kit for a first build).
- Whether the price is strong for the specs given, and what it competes against at that price point.
- Which specific spec(s) actually matter for that use case.

Take a direct, analytical, enthusiast tone. Do not use hype phrases like "insane deal" or "act now." Never state a spec, benchmark, or competitor price that wasn't explicitly given to you — if you don't have enough information to say something specific and true, keep it simple rather than inventing detail.

Output ONLY the analysis text — no preamble, no markdown, no quotation marks, no hashtags, no URL. Keep the entire output under 350 characters."""

# Weekly digest — a once-a-week curated roundup written by AI from the
# week's posted deals (stored in the `posted_deals` Supabase table).
# GPT-5.6 Luna primary, Gemini Flash Lite paid fallback (the free Gemma
# endpoint was removed — :free endpoints have hard rate limits and left
# the digest unposted). See weekly_digest.py.
OPENROUTER_WEEKLY_DIGEST_MODEL = os.environ.get("OPENROUTER_WEEKLY_DIGEST_MODEL", "openai/gpt-5.6-luna")
OPENROUTER_WEEKLY_DIGEST_FALLBACK_MODEL = os.environ.get("OPENROUTER_WEEKLY_DIGEST_FALLBACK_MODEL", "google/gemini-2.5-flash-lite")
OPENROUTER_WEEKLY_DIGEST_SYSTEM_PROMPT = """You write a weekly roundup for a deal-finding bot aimed at mechanical-keyboard builders and enthusiasts. You'll be given a list of the week's posted deals (title, source, sale price, list price, and discount). Pick the top 3-5 most noteworthy and write a short, punchy summary of each: what it is, who it's for, and why the price stood out. Use a direct, analytical, enthusiast tone — no hype phrases like "insane" or "don't miss out." Never state a spec, benchmark, or price that isn't in the input.

Output plain text only — no markdown, no hashtags, no URL. Start with a one-line intro (e.g. "This week's best keyboard deals:"). Keep each deal summary to 1-2 sentences. End with a one-line sign-off."""

OPENROUTER_CLASSIFIER_SYSTEM_PROMPT = """You screen deal listings for a bot that posts discounts to an audience of mechanical-keyboard builders and enthusiasts. For each numbered item below, decide whether it is something that audience would genuinely want — not just topically related (e.g. "electronics"), but actually desirable: recognizable keyboard brands, real boards, switches, keycaps, and keyboard accessories. Reject generic, off-brand, or low-interest items even if they're topically in-category.

Respond with ONLY a JSON object in this exact shape, with EXACTLY one string per input item, in the same order:
{"items": ["KEEP", "DROP", ...]}
Each string must be exactly the word KEEP or the word DROP — nothing else. The number of items must exactly match the number of input lines."""

# Deal quality scorer — SHADOW MODE (not gating anything yet). One batched
# call per run rating each deal 1-10 for a mechanical-keyboard building/
# enthusiast audience, complementing the keyword/discount filters with an
# AI judgment of whether the item is genuinely *desirable* (recognizable
# brand, real value) rather than merely in-category. See ai.deal_scorer.py.
# DeepSeek primary + Gemini Flash Lite paid fallback: the free Gemma
# endpoint was removed after 429s/empty content left shadow stages
# unreported (and :free endpoints have hard rate limits on OpenRouter).
OPENROUTER_QUALITY_SCORER_MODEL = os.environ.get("OPENROUTER_QUALITY_SCORER_MODEL", "deepseek/deepseek-v4-flash-0731")
OPENROUTER_QUALITY_SCORER_FALLBACK_MODEL = os.environ.get("OPENROUTER_QUALITY_SCORER_FALLBACK_MODEL", "google/gemini-2.5-flash-lite")
MIN_QUALITY_SCORE = int(os.environ.get("MIN_QUALITY_SCORE", "6"))
# Desirability classifier operating mode: "shadow" (report only — the
# default; the classifier's judgment is posted to the shadow channel but
# gates nothing) or "gate" (DROP verdicts actually remove candidates before
# posting). Promotion from shadow to gate is a one-line Variable change and
# must only happen after the shadow channel's judgment has been reviewed
# over enough real runs — a wrong DROP is an invisible lost deal, so the
# gate fails OPEN: any model/parse failure keeps every candidate.
CLASSIFIER_MODE = os.environ.get("CLASSIFIER_MODE", "shadow")
OPENROUTER_QUALITY_SCORER_SYSTEM_PROMPT = """You score deal listings for a bot that posts discounts to an audience of mechanical-keyboard builders and enthusiasts. For each numbered item below, rate how genuinely desirable it is to that audience on a scale of 1 to 10, where 10 is a must-buy and 1 is generic/off-brand junk. Consider: recognizable brand in the keyboard space, real spec-to-price value, and whether it is a genuine keyboard product rather than something merely topically in-category (e.g. a no-name cable, an off-brand desk mat).

Respond with ONLY a JSON object in this exact shape, with EXACTLY one integer per input item, in the same order:
{"items": [9, 8, ...]}
Each integer must be a whole number from 1 to 10 — nothing else. The number of items must exactly match the number of input items."""

# Category tagger — SHADOW MODE (not gating or routing yet). One batched
# call per run tagging each deal into a fine-grained category, which could
# later drive per-category Discord channels or better hashtag/analysis
# targeting. See ai.categorizer.categorize_deals(). DeepSeek primary +
# Gemini Flash Lite paid fallback (free Gemma endpoints removed — 429s and
# reasoning-budget burn left shadow stages unreported).
OPENROUTER_CATEGORIZER_MODEL = os.environ.get("OPENROUTER_CATEGORIZER_MODEL", "deepseek/deepseek-v4-flash-0731")
OPENROUTER_CATEGORIZER_FALLBACK_MODEL = os.environ.get("OPENROUTER_CATEGORIZER_FALLBACK_MODEL", "google/gemini-2.5-flash-lite")
DEAL_CATEGORIES = ["board", "switch", "keycaps", "accessory", "other"]
OPENROUTER_CATEGORIZER_SYSTEM_PROMPT = """You classify deal listings for a bot aimed at mechanical-keyboard builders and enthusiasts. For each numbered item below, assign exactly one category from this fixed list:

- board: Mechanical keyboards, hot-swappable PCBs, aluminum/plastic cases, plates, and barebones kits. Example: "Keychron Q Pro".
- switch: Individual switches, stems, springs, stabilizers, and lubing supplies. Stabilizers are CORE switch mechanics — not accessories. Example: "Gateron Yellow Pro".
- keycaps: Keycap sets (ABS/PBT), individual artisan keycaps, or cap pullers/tools designed specifically for keycaps. Example: "GMK Alice Set".
- accessory: Everything else — desk mats, coiled cables (if not part of a bundle), wrist rests, tools (multi-tools/keycap/puller combos), and cleaning supplies. ONLY for items that are genuinely mechanical-keyboard accessories, not generic electronics. Example: "Purple Pudding Desk Mat".
- other: Only if the item genuinely doesn't fit any other category. Generic electronics (mice, chargers, stands), non-keyboard peripherals, and items where "keyboard" is incidental (e.g. "iPad keyboard stand") belong here.

Few-shot examples (input -> expected output):
1. [Woot] GMK Meow-achi Keycap Set — $79.99 (keycaps)
2. [Shopify] Gateron Oil Red Linear Switches (110-pack) — $24.50 (switch)
3. [Best Buy] Keychron Q1 QMK Custom Keyboard (barebones) — $149.99 (board)
4. [Woot] Epomaker x F99 Barebones 75% Hot-Swap — $89.99 (board)
5. [Shopify] KBDfans 1.5m Coiled Aviator Cable — $19.00 (accessory)
6. [Woot] Deskey 3D Desk Mat — $14.99 (accessory)
7. [Best Buy] Logitech G Pro Superlight Mouse — $69.99 (other)
8. [Shopify] GMK Striker Single Keycap (1u Esc) — $8.00 (keycaps)
9. [Shopify] Durock Plate Mount Stabilizer Set (2u) — $12.00 (switch)
10. [Best Buy] iPad Keyboard Stand — Adjustable Aluminum — $35.00 (other)

Respond with ONLY a JSON object in this exact shape, with EXACTLY one category string per input item, in the same order:
{"categories": ["board", "switch", ...]}
Each category string must be one of the five words above, lowercased. The number of strings must exactly match the number of input items."""

# Spec extraction — cleans up messy retail titles (Woot/Best Buy/Shopify)
# into a concise product name plus a few short technical specs, for the
# Discord embed and captions. See ai.spec_extraction.extract_clean_specs().
# DeepSeek primary (same as the verdict chain; qwen/qwen3.7-flash was
# removed — its reasoning trace exhausted the output budget and returned
# empty content), Gemini Flash Lite paid fallback.
OPENROUTER_SPEC_EXTRACTION_MODEL = os.environ.get("OPENROUTER_SPEC_EXTRACTION_MODEL", "deepseek/deepseek-v4-flash-0731")
OPENROUTER_SPEC_FALLBACK_MODEL = os.environ.get("OPENROUTER_SPEC_FALLBACK_MODEL", "google/gemini-2.5-flash-lite")
SPEC_EXTRACTION_SYSTEM_PROMPT = """You clean up messy retail product titles for a deal-finding bot focused on mechanical keyboards and keyboard hardware. Given a raw title (and optional description), extract a clean, concise product name and up to 4 short technical specs.

Rules:
- Never invent a spec that isn't explicitly present or clearly implied in the input. If there is genuinely nothing worth calling out, return an empty specs list — do not pad it with anything invented.
- clean_title: the product name and model, stripped of SEO keyword clutter, under 100 characters.
- specs: 0 to 4 short strings (e.g. "Switch type: linear", "Size: 65%", "Material: PBT"), each under 60 characters.

Respond with only a JSON object in this exact shape: {"clean_title": string, "specs": [string, ...]}"""

# ---------------------------------------------------------------------------
# Woot feed selection and title/category filtering
# ---------------------------------------------------------------------------
# Woot feeds that map to your electronics focus. Note R6: this re-theme to
# mechanical keyboards dramatically lowers Woot's throughput — expect Woot
# to contribute few (if any) deals until the keyword lists are tuned after
# the first real run. Valid options: All, Clearance, Computers, Electronics,
# Featured, Home, Gourmet, Shirts, Sports, Tools, Wootoff
WOOT_FEEDS = ["Electronics", "Computers"]

# Woot sometimes cross-lists a "featured" item across every feed regardless
# of category, so filtering by feed name alone isn't fully reliable. This
# catches anything with these words in the title and skips it. Add to this
# list as you spot more off-topic items sneaking through. NOTE (R6): the
# exclude list is deliberately aggressive here — it also removes the
# PC-builds/gaming vocabulary (mouse, headset, monitor, laptop, GPU, ...)
# the old bot used to surface, per the keeb-focused re-theme.
WOOT_EXCLUDE_KEYWORDS = [
    "squishmallow", "plush", "stuffed animal", "funko",
    "apparel", "shirt", "hoodie", "sneaker", "shoes",
    "cookware", "kitchen", "furniture", "decor", "bedding", "mattress",
    "mouse", "mice", "headset", "webcam", "microphone",
    "monitor", "laptop", "gpu", "graphics card", "video card",
    "motherboard", "cpu", "processor", "ram ", "ssd", "nvme", "hard drive",
    "power supply", "psu", "router", "console", "controller", "game",
    "combo", "bundle",
]

# Allow-list for the mechanical-keyboard niche. A Woot deal must match at
# least one of these (in addition to clearing WOOT_EXCLUDE_KEYWORDS above)
# to post.
WOOT_INCLUDE_KEYWORDS = [
    "keyboard", "keycap", "keycaps", "keyset", "keycap set",
    "switch", "switches", "mechanical switch",
    "artisan", "barebones", "hot-swap", "hotswap", "pcb", "stabilizer",
    "cable", "wrist rest", "desk mat", "keyboard case", "plate mount",
]

# Woot's feed items carry a "Categories" field — a list of hierarchical
# strings like ["HOME", "TOOLS", "HOME/Lighting & Fans"]. This rejects
# whole off-topic departments by their top-level category (the part before
# the first "/"). R7: unchanged — still valid for a keeb-focused bot.
WOOT_EXCLUDE_CATEGORIES = [
    "HOME", "TOOLS", "APPAREL", "TOYS", "SPORTS", "KITCHEN",
    "AUTOMOTIVE", "GOURMET", "BEAUTY", "PET",
]

# Best Buy keyword searches — narrowed to the mechanical-keyboard focus.
BESTBUY_SEARCH_TERMS = [
    "mechanical keyboard", "keycap set", "keycaps", "switches",
    "60% keyboard", "65% keyboard", "TKL keyboard", "barebones keyboard",
    "keyboard stabilizers", "wrist rest", "keyboard cable", "desk mat",
]

# ---------------------------------------------------------------------------
# Shopify — public storefronts' /products.json endpoints (no API key/auth
# needed). See deal_bot/sources/shopify.py. SHOPIFY_STORES is a JSON list
# parsed at import; DEFAULT_SHOPIFY_STORES is the fallback when the
# variable is unset/empty. THROTTLE_MIN/MAX and MAX_COLLECTIONS are tuning
# Variables (repo Variables, not Secrets). No new secrets: SHOPIFY_WEBHOOK_URL
# is a Discord webhook URL, same shape as WOOT/BESTBUY_WEBHOOK_URL.
# ---------------------------------------------------------------------------
SHOPIFY_WEBHOOK_URL = os.environ.get("SHOPIFY_WEBHOOK_URL", "")

DEFAULT_SHOPIFY_STORES = [
    {"name": "KBDfans", "base_url": "https://kbdfans.com", "collection_handles": ["keyboards", "keycaps", "switches"]},
    {"name": "CannonKeys", "base_url": "https://cannonkeys.com", "collection_handles": []},
    {"name": "NovelKeys", "base_url": "https://novelkeys.com", "collection_handles": []},
    {"name": "Divinikey", "base_url": "https://divinikey.com", "collection_handles": []},
    # Kinetic Labs is intentionally OMITTED: it does not serve Shopify's
    # /products.json (returns HTML/404). Add only if/when its storefront
    # exposes the Shopify JSON endpoint.
]

SHOPIFY_MAX_COLLECTIONS_PER_STORE = int(os.environ.get("SHOPIFY_MAX_COLLECTIONS_PER_STORE", "1"))
# Per-store throttling so a burst of /products.json GETs stays polite to
# the storefronts (R1: each store may fire several requests). Values are
# seconds; random uniform pick between the two. Set both to 0 in tests to
# disable sleeping.
_SHOPIFY_THROTTLE_MIN = float(os.environ.get("SHOPIFY_THROTTLE_MIN", "2"))
_SHOPIFY_THROTTLE_MAX = float(os.environ.get("SHOPIFY_THROTTLE_MAX", "5"))
_SHOPIFY_THROTTLE_RANGE = (_SHOPIFY_THROTTLE_MIN, _SHOPIFY_THROTTLE_MAX)


def _parse_shopify_stores(raw: str) -> list[dict]:
    """Parse the SHOPIFY_STORES JSON Variable into a list of store dicts
    ({name, base_url, collection_handles}). Fails closed to DEFAULT_SHOPIFY_STORES
    on empty input, malformed JSON, or a non-list root, printing a warning so a
    typo'd Variable isn't silently ignored. Entries missing a non-blank name
    or base_url are skipped; a non-list collection_handles is coerced to [].
    A trailing '/' is stripped from base_url."""
    if not raw or not raw.strip():
        return []
    try:
        parsed = _json.loads(raw)
    except ValueError as e:
        print(f"[config] SHOPIFY_STORES JSON parse failed ({e}) — using defaults")
        return []
    if not isinstance(parsed, list):
        print("[config] SHOPIFY_STORES is not a JSON list — using defaults")
        return []
    stores = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        base_url = entry.get("base_url")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(base_url, str) or not base_url.strip():
            continue
        handles = entry.get("collection_handles")
        if not isinstance(handles, list):
            handles = []
        stores.append({
            "name": name.strip(),
            "base_url": base_url.strip().rstrip("/"),
            "collection_handles": [h.strip() for h in handles if isinstance(h, str) and h.strip()],
        })
    return stores


# Parse the SHOPIFY_STORES Variable if set; otherwise fall back to the
# curated default store list.
SHOPIFY_STORES = _parse_shopify_stores(os.environ.get("SHOPIFY_STORES", "")) or DEFAULT_SHOPIFY_STORES

# ---------------------------------------------------------------------------
# Deal-quality thresholds — tunable via .env without editing code.
# ---------------------------------------------------------------------------
MIN_DISCOUNT_PERCENT = int(os.environ.get("MIN_DISCOUNT_PERCENT", "20"))       # ignore anything below this % off
MIN_DOLLAR_SAVINGS = float(os.environ.get("MIN_DOLLAR_SAVINGS", "10"))         # AND ignore anything saving less than this in real dollars
SEEN_TTL_DAYS = 45              # forget deals older than this so the table doesn't grow forever

# Price-history quality gate. A deal needs at least this many DISTINCT
# CALENDAR DAYS of price_history observations before this gate applies at
# all — with no real history yet, everything falls back to the
# discount-vs-list-price check above.
PRICE_HISTORY_MIN_DAYS = int(os.environ.get("PRICE_HISTORY_MIN_DAYS", "3"))
# Once there's enough history, the sale price must be within this % of the
# lowest price ever recorded for that item to count as "near its floor."
PRICE_HISTORY_TOLERANCE_PERCENT = float(os.environ.get("PRICE_HISTORY_TOLERANCE_PERCENT", "5"))

# ---------------------------------------------------------------------------
# Derived / display constants
# ---------------------------------------------------------------------------
SOURCE_WEBHOOKS = {
    "Woot": WOOT_WEBHOOK_URL,
    "Best Buy": BESTBUY_WEBHOOK_URL,
    "Shopify": SHOPIFY_WEBHOOK_URL,
}

# Fixed display order for the digest's per-source fields — sources with
# nothing posted this run are simply left out.
DIGEST_SOURCE_ORDER = ["Woot", "Best Buy", "Shopify"]

# Set True to show a large image instead of a small thumbnail — bigger and
# more eye-catching, but takes up more vertical space per post.
EMBED_USE_LARGE_IMAGE = False