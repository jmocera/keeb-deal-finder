-- VoltDrop deal-bot — authoritative Supabase schema
--
-- Paste this ENTIRE file into the Supabase SQL editor for the project the
-- bot's SUPABASE_URL points at, once, as the one-time setup. Everything is
-- idempotent (create table/index if not exists), so re-running it is safe.
--
-- The four tables below are what deal_bot expects (see deal_bot/storage/*,
-- deal_bot/pipeline.py:log_run, and deal_bot/weekly_digest.py). Keeping the
-- DDL in the repo (rather than only in scattered docstrings) means the
-- schema is version-controlled and reviewable, and provisioning is a single
-- paste instead of a reconstruction.
--
-- NOTE: the service-role key used by the bot talks to these tables via the
-- PostgREST REST API; per the project's design, PostgREST manages row-level
-- auth for the service role. No RLS policies are created here — they are a
-- project-level concern, not schema the bot needs.

-- ==========================================================================
-- seen_deals — dedupe state, keyed by deal id ("source:<sku/offerid/appid>").
-- Written by upsert_seen_entry after each successful post; loaded by
-- load_seen at the start of every run; pruned by prune_seen (SEEN_TTL_DAYS).
-- ==========================================================================
create table if not exists seen_deals (
  id text primary key,
  source text,
  last_seen timestamptz,
  sale_price numeric,
  lowest_price numeric,
  lowest_price_date timestamptz
);

-- prune_seen issues DELETE WHERE last_seen < cutoff every run — index it.
create index if not exists seen_deals_last_seen_idx on seen_deals (last_seen);

-- ==========================================================================
-- price_history — one row per deal per calendar day, whether or not the
-- deal cleared the posting threshold. Backs the historical-low quality gate
-- and the price-trend context fed to the AI prompts. The unique constraint
-- on (deal_id, observed_date) is required by the PostgREST upsert
-- (?on_conflict=deal_id,observed_date) in record_price_observations.
-- ==========================================================================
create table if not exists price_history (
  id bigserial primary key,
  deal_id text,
  source text,
  observed_at timestamptz default now(),
  sale_price numeric,
  list_price numeric,
  discount_pct numeric,
  observed_date date
);
create unique index if not exists price_history_deal_id_observed_date_key
  on price_history (deal_id, observed_date);

-- ==========================================================================
-- run_log — one row per run_once() call, written whether the run succeeds
-- or raises, and mirrored to RUN_LOG_WEBHOOK_URL. Also the source table for
-- the hourly watchdog's freshness check.
--
-- skipped_not_desirable + shadow_sent are the columns added by the
-- AI-differentiator pass (CLASSIFIER_MODE gate + shadow-report tracking).
-- ==========================================================================
create table if not exists run_log (
  id bigserial primary key,
  ran_at timestamptz default now(),
  deals_checked integer,
  posted integer,
  skipped_already_seen integer,
  skipped_no_better_price integer,
  skipped_below_threshold integer,
  skipped_not_near_historical_low integer,
  skipped_not_desirable integer not null default 0,
  shadow_sent boolean not null default false,
  digest_sent boolean,
  error text
);

-- ==========================================================================
-- posted_deals — append-only log of every deal that actually posted, backing
-- the weekly digest. Pruned by weekly_digest.prune_posted_deals (90-day TTL).
-- ==========================================================================
create table if not exists posted_deals (
  id text primary key,
  source text,
  title text,
  url text,
  sale_price numeric,
  list_price numeric,
  posted_at timestamptz default now()
);