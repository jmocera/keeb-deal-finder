-- VoltDrop deal-bot — authoritative Supabase schema
--
-- Paste this ENTIRE file into the Supabase SQL editor for the project the
-- bot's SUPABASE_URL points at, once, as the one-time setup. Everything is
-- idempotent (create table/index if not exists), so re-running it is safe.
--
-- The six tables below are what deal_bot expects (see deal_bot/storage/*,
-- deal_bot/pipeline.py:log_run, and deal_bot/weekly_digest.py). Keeping the
-- DDL in the repo (rather than only in scattered docstrings) means the
-- schema is version-controlled and reviewable, and provisioning is a single
-- paste instead of a reconstruction.
--
-- NOTE: the bot talks to these tables via the PostgREST REST API using the
-- privileged server key (SUPABASE_SECRET_KEY, preferred, or the legacy
-- SUPABASE_SERVICE_KEY). The hardening block at the bottom enables Row Level
-- Security on every table with NO permissive policies and revokes grants
-- from the public API roles — the bot is unaffected because its privileged
-- key maps to the service_role context, which bypasses RLS and holds
-- explicit grants.

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

-- ==========================================================================
-- guild_destinations — per-guild delivery target configured by /setup.
-- One row per Discord guild. enabled=false after /disable. The first
-- pipeline run after /setup seeds current candidates into guild_deal_posts
-- and sets initial_sync_complete=true without posting (no flood).
-- ==========================================================================
create table if not exists guild_destinations (
  guild_id text primary key,
  channel_id text not null,
  enabled boolean not null default true,
  initial_sync_complete boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on column guild_destinations.guild_id is
  'Discord guild snowflake, stored as text to avoid JSON integer precision loss.';
comment on column guild_destinations.channel_id is
  'Discord text-channel snowflake that receives native bot deal messages.';
comment on column guild_destinations.enabled is
  'true after /setup; false after /disable. load_guild_destinations returns only enabled rows.';
comment on column guild_destinations.initial_sync_complete is
  'false until the first pipeline run after /setup seeds current candidates as already-posted. New deals start on the NEXT run.';
comment on column guild_destinations.created_at is
  'Row insert time (UTC).';
comment on column guild_destinations.updated_at is
  'Last /setup, /disable, or initial-sync mark (UTC).';

-- ==========================================================================
-- guild_deal_posts — per-guild delivery dedupe. Composite PK so each
-- guild receives each deal_id at most once. Independent of seen_deals
-- (quality gates) and posted_deals (weekly digest).
-- ==========================================================================
create table if not exists guild_deal_posts (
  guild_id text not null,
  deal_id text not null,
  sale_price numeric,
  posted_at timestamptz not null default now(),
  primary key (guild_id, deal_id)
);

comment on column guild_deal_posts.guild_id is
  'Discord guild snowflake (text). Part of composite PK with deal_id.';
comment on column guild_deal_posts.deal_id is
  'Deal id from sources (e.g. woot:sku). Same identifier as seen_deals.id.';
comment on column guild_deal_posts.sale_price is
  'Sale price at the moment of successful delivery (or initial-sync seed).';
comment on column guild_deal_posts.posted_at is
  'UTC timestamp of the successful native-bot delivery or baseline seed.';

create index if not exists guild_deal_posts_guild_id_idx
  on guild_deal_posts (guild_id);

-- ==========================================================================
-- Hardening (idempotent — safe to re-run on every paste):
--   1. Row Level Security is enabled on every table with NO permissive
--      policies, so anon/authenticated are denied by default. The bot only
--      ever connects with the privileged server key, which maps to the
--      service_role context and BYPASSES RLS.
--   2. Privileges are revoked from PUBLIC, anon, and authenticated so the
--      public API surface has no table or sequence access even if a
--      permissive policy were added later by mistake.
--   3. Explicit grants give service_role the DML + sequence access the bot
--      needs, without depending on Supabase default privileges.
--   4. Do NOT add permissive policies for anon/authenticated — this schema
--      intentionally exposes nothing through the public API roles.
-- ==========================================================================
alter table public.seen_deals enable row level security;
revoke all on public.seen_deals from public, anon, authenticated;
grant select, insert, update, delete on public.seen_deals to service_role;

alter table public.price_history enable row level security;
revoke all on public.price_history from public, anon, authenticated;
grant select, insert, update, delete on public.price_history to service_role;
revoke all on sequence public.price_history_id_seq from public, anon, authenticated;
grant usage, select on sequence public.price_history_id_seq to service_role;

alter table public.run_log enable row level security;
revoke all on public.run_log from public, anon, authenticated;
grant select, insert, update, delete on public.run_log to service_role;
revoke all on sequence public.run_log_id_seq from public, anon, authenticated;
grant usage, select on sequence public.run_log_id_seq to service_role;

alter table public.posted_deals enable row level security;
revoke all on public.posted_deals from public, anon, authenticated;
grant select, insert, update, delete on public.posted_deals to service_role;

alter table public.guild_destinations enable row level security;
revoke all on public.guild_destinations from public, anon, authenticated;
grant select, insert, update, delete on public.guild_destinations to service_role;

alter table public.guild_deal_posts enable row level security;
revoke all on public.guild_deal_posts from public, anon, authenticated;
grant select, insert, update, delete on public.guild_deal_posts to service_role;