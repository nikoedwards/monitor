"""SQLite data layer: brand-rooted schema, migrations, and seed data."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator

from .config import DB_PATH
from .util import new_id, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  tier INTEGER NOT NULL DEFAULT 1,
  vendor TEXT,
  sync_mode TEXT NOT NULL DEFAULT 'scheduled',
  status TEXT NOT NULL DEFAULT 'ready',
  needs_credentials INTEGER NOT NULL DEFAULT 0,
  credential_key TEXT,
  cadence TEXT NOT NULL DEFAULT 'daily',
  last_collect_at TEXT,
  last_status TEXT,
  last_error TEXT,
  item_count INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_brand_runs (
  source_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  last_collect_at TEXT NOT NULL,
  last_status TEXT,
  last_error TEXT,
  item_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (source_id, brand_id)
);

CREATE TABLE IF NOT EXISTS brands (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT,
  is_competitor INTEGER NOT NULL DEFAULT 0,
  is_primary INTEGER NOT NULL DEFAULT 0,
  official_website TEXT,
  amazon_url TEXT,
  marketplace TEXT,
  asin TEXT,
  category TEXT,
  description TEXT,
  logo_url TEXT,
  social_links_json TEXT,
  ecommerce_links_json TEXT,
  monitoring_keywords_json TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  name TEXT NOT NULL,
  sku TEXT,
  category TEXT,
  notes TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Unified monitored links for sales channels and marketing accounts.
CREATE TABLE IF NOT EXISTS links (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  product_id TEXT,
  dimension TEXT NOT NULL,            -- sales | marketing
  channel TEXT NOT NULL,             -- amazon|dtc|other_ecom|offline|media|social|ads|creators|community
  platform TEXT,
  url TEXT,
  canonical_url TEXT,
  label TEXT,
  region TEXT,
  source_id TEXT,
  cadence TEXT NOT NULL DEFAULT 'daily',
  status TEXT NOT NULL DEFAULT 'active',
  last_collect_at TEXT,
  last_status TEXT,
  last_error TEXT,
  config_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Per-listing registry (the "Listing List"): one row per individual product
-- listing discovered from a configured storefront/shop link. Supports per-listing
-- monitor toggles, product mapping, and change detection.
CREATE TABLE IF NOT EXISTS sales_listings (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  product_id TEXT,
  link_id TEXT,                       -- parent storefront/shop link in `links`
  channel TEXT NOT NULL,             -- amazon|dtc|other_ecom
  platform TEXT,
  asin TEXT,
  url TEXT,
  canonical_url TEXT,
  marketplace TEXT,
  title TEXT,
  sku TEXT,
  image_url TEXT,
  status TEXT NOT NULL DEFAULT 'active',   -- active | paused
  monitor INTEGER NOT NULL DEFAULT 1,      -- user opt-in to daily snapshots
  first_seen TEXT,
  last_seen TEXT,
  last_hash TEXT,
  last_change_at TEXT,
  last_status TEXT,
  last_error TEXT,
  config_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Time-series sales metrics (online collected or offline manual entry).
CREATE TABLE IF NOT EXISTS sales_metrics (
  id TEXT PRIMARY KEY,
  link_id TEXT,
  brand_id TEXT NOT NULL,
  product_id TEXT,
  snapshot_date TEXT NOT NULL,
  channel TEXT NOT NULL,
  platform TEXT,
  price REAL,
  currency TEXT,
  review_count INTEGER,
  rating REAL,
  rank INTEGER,
  units_est INTEGER,
  revenue_est REAL,
  in_stock INTEGER,
  asin TEXT,
  bsr INTEGER,
  title TEXT,
  image_url TEXT,
  change_score REAL,
  changes_json TEXT,
  source TEXT NOT NULL DEFAULT 'manual',
  raw_json TEXT,
  created_at TEXT NOT NULL
);

-- Unified content records (voc, media, social, ads, creators, community).
CREATE TABLE IF NOT EXISTS records (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  brand_id TEXT,
  product_id TEXT,
  link_id TEXT,
  external_id TEXT,
  data_type TEXT NOT NULL,
  dimension TEXT,
  channel TEXT,
  platform TEXT,
  title TEXT,
  author TEXT,
  body TEXT NOT NULL,
  url TEXT,
  region TEXT,
  language TEXT,
  occurred_at TEXT,
  sentiment TEXT,
  sentiment_score REAL,
  intent TEXT,
  topics_json TEXT,
  metrics_json TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL
);

-- Daily market-share inputs. Store raw App signals rather than a cohort-bound
-- percentage so any selected brand set can be re-normalized historically.
CREATE TABLE IF NOT EXISTS market_share_snapshots (
  id TEXT PRIMARY KEY,
  snapshot_date TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  country TEXT NOT NULL DEFAULT 'all',
  app_downloads_est INTEGER NOT NULL DEFAULT 0,
  app_downloads_low INTEGER NOT NULL DEFAULT 0,
  app_downloads_high INTEGER NOT NULL DEFAULT 0,
  app_download_basis TEXT NOT NULL DEFAULT 'unavailable',
  app_reviews INTEGER NOT NULL DEFAULT 0,
  app_rating REAL,
  app_store_apps INTEGER NOT NULL DEFAULT 0,
  source_updated_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (snapshot_date, brand_id, country)
);

-- Materialized creator/influencer roster, re-aggregated from creator records.
CREATE TABLE IF NOT EXISTS creators (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  handle TEXT,
  name TEXT,
  url TEXT,
  avatar_url TEXT,
  follower_count INTEGER NOT NULL DEFAULT 0,
  post_count INTEGER NOT NULL DEFAULT 0,
  collab_count INTEGER NOT NULL DEFAULT 0,
  sponsored_count INTEGER NOT NULL DEFAULT 0,
  total_views INTEGER NOT NULL DEFAULT 0,
  total_engagement INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT,
  last_seen TEXT,
  last_collab_at TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- A creator post can mention or feature more than one product.  Keep the
-- many-to-many attribution separate from records.product_id, which remains the
-- convenient "primary product" pointer used by older screens and imports.
CREATE TABLE IF NOT EXISTS record_product_matches (
  record_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  match_type TEXT NOT NULL DEFAULT 'auto',
  evidence_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (record_id, product_id)
);

-- Gladia-style editorial workflow: collected or manually seeded creators first
-- enter a candidate pool, then a reviewer promotes the representative set that
-- is allowed into the curated landscape/map.
CREATE TABLE IF NOT EXISTS creator_candidates (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  platform TEXT NOT NULL,
  identity_key TEXT NOT NULL,
  handle TEXT,
  name TEXT,
  url TEXT,
  avatar_url TEXT,
  review_status TEXT NOT NULL DEFAULT 'pending', -- pending|approved|priority|rejected
  relationship_status TEXT NOT NULL DEFAULT 'potential', -- potential|contacted|collaborating|past
  discovery_source TEXT NOT NULL DEFAULT 'collected', -- collected|manual|manual+collected
  relevance_score REAL NOT NULL DEFAULT 0,
  follower_count INTEGER NOT NULL DEFAULT 0,
  post_count INTEGER NOT NULL DEFAULT 0,
  collab_count INTEGER NOT NULL DEFAULT 0,
  sponsored_count INTEGER NOT NULL DEFAULT 0,
  total_views INTEGER NOT NULL DEFAULT 0,
  total_engagement INTEGER NOT NULL DEFAULT 0,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  first_seen TEXT,
  last_seen TEXT,
  last_collab_at TEXT,
  notes TEXT,
  reviewed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (brand_id, platform, identity_key)
);

CREATE TABLE IF NOT EXISTS creator_candidate_evidence (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  record_id TEXT,
  product_id TEXT NOT NULL DEFAULT '',
  evidence_type TEXT NOT NULL DEFAULT 'record',
  query TEXT,
  title TEXT,
  excerpt TEXT,
  url TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  occurred_at TEXT,
  evidence_json TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (candidate_id, record_id, product_id, evidence_type)
);

CREATE TABLE IF NOT EXISTS creator_map_snapshots (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  product_id TEXT NOT NULL DEFAULT '',
  platform TEXT NOT NULL DEFAULT 'all',
  snapshot_date TEXT NOT NULL,
  title TEXT,
  points_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (brand_id, product_id, platform, snapshot_date)
);

CREATE TABLE IF NOT EXISTS web_monitors (
  id TEXT PRIMARY KEY,
  brand_id TEXT,
  product_id TEXT,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'single_page',
  crawl_limit INTEGER NOT NULL DEFAULT 20,
  icon_url TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  cadence TEXT NOT NULL DEFAULT 'daily',
  check_interval_minutes INTEGER NOT NULL DEFAULT 1440,
  snapshot_interval_minutes INTEGER NOT NULL DEFAULT 1440,
  last_check_at TEXT,
  last_snapshot_at TEXT,
  snapshot_retry_count INTEGER NOT NULL DEFAULT 0,
  next_snapshot_retry_at TEXT,
  last_snapshot_attempt_at TEXT,
  last_change_score REAL NOT NULL DEFAULT 0,
  last_change_summary TEXT,
  last_status TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_snapshots (
  id TEXT PRIMARY KEY,
  monitor_id TEXT NOT NULL,
  brand_id TEXT,
  snapshot_date TEXT NOT NULL,
  url TEXT NOT NULL,
  page_key TEXT,
  final_url TEXT,
  title TEXT,
  screenshot_path TEXT,
  html_path TEXT,
  archive_size INTEGER,
  text_hash TEXT,
  text_excerpt TEXT,
  change_score REAL,
  visual_change_score REAL,
  visual_change_ratio REAL,
  visual_regions_json TEXT,
  summary TEXT,
  changes_json TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_snapshot_analyses (
  id TEXT PRIMARY KEY,
  brand_id TEXT,
  monitor_id TEXT,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  snapshot_ids_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  model TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS voc_actions (
  id TEXT PRIMARY KEY,
  record_id TEXT,
  brand_id TEXT,
  source_id TEXT,
  title TEXT NOT NULL,
  description TEXT,
  owner_team TEXT,
  priority TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'open',
  product TEXT,
  topic TEXT,
  due_at TEXT,
  closed_at TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publications (
  domain TEXT PRIMARY KEY,
  name TEXT,
  icon_url TEXT,
  est_monthly_traffic INTEGER NOT NULL DEFAULT 0,
  traffic_lower INTEGER NOT NULL DEFAULT 0,
  traffic_upper INTEGER NOT NULL DEFAULT 0,
  popularity_rank INTEGER,
  traffic_confidence TEXT NOT NULL DEFAULT 'low',
  traffic_as_of TEXT,
  authority INTEGER NOT NULL DEFAULT 0,
  tier TEXT,
  country TEXT,
  language TEXT,
  source TEXT NOT NULL DEFAULT 'heuristic',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capture_jobs (
  id TEXT PRIMARY KEY,
  monitor_id TEXT NOT NULL,
  brand_id TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  phase TEXT,
  message TEXT,
  current_url TEXT,
  total_pages INTEGER NOT NULL DEFAULT 0,
  completed_pages INTEGER NOT NULL DEFAULT 0,
  progress INTEGER NOT NULL DEFAULT 0,
  snapshot_ids_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);

-- Hiring intelligence: job posting registry (one row per discovered JD).
CREATE TABLE IF NOT EXISTS job_postings (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  link_id TEXT,                       -- parent hiring source in `links` (dimension='hiring')
  platform TEXT NOT NULL,            -- boss | linkedin
  external_id TEXT,                  -- platform job id when known
  url TEXT,
  canonical_url TEXT,
  title TEXT,
  department TEXT,
  city TEXT,
  jd_text TEXT,
  jd_hash TEXT,
  business_tags_json TEXT,           -- LLM-derived business direction tags
  status TEXT NOT NULL DEFAULT 'open',  -- open | closed
  posted_at TEXT,
  refreshed_at TEXT,
  first_seen TEXT,
  last_seen TEXT,
  closed_at TEXT,
  last_change_at TEXT,
  last_status TEXT,
  last_error TEXT,
  config_json TEXT,                  -- holds fingerprint for change detection
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Hiring time-series: one snapshot per posting per day (open/closed + JD change).
CREATE TABLE IF NOT EXISTS job_snapshots (
  id TEXT PRIMARY KEY,
  posting_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  link_id TEXT,
  snapshot_date TEXT NOT NULL,
  platform TEXT,
  status TEXT,                       -- open | closed
  is_open INTEGER,
  title TEXT,
  department TEXT,
  city TEXT,
  posted_at TEXT,
  refreshed_at TEXT,
  applicant_signal TEXT,
  change_score REAL,
  changes_json TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL
);

-- LinkedIn employee roster for a target company (brand).
CREATE TABLE IF NOT EXISTS linkedin_profiles (
  id TEXT PRIMARY KEY,
  brand_id TEXT NOT NULL,
  link_id TEXT,
  source_type TEXT NOT NULL DEFAULT 'company', -- company | manual
  external_id TEXT,
  name TEXT,
  headline TEXT,
  title TEXT,
  notes TEXT,
  profile_url TEXT,
  canonical_url TEXT,
  avatar_url TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  monitor INTEGER NOT NULL DEFAULT 1,
  last_activity_at TEXT,
  last_profile_change_at TEXT,
  first_seen TEXT,
  last_seen TEXT,
  last_status TEXT,
  last_error TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Daily LinkedIn profile snapshots for monitored/key people.
CREATE TABLE IF NOT EXISTS linkedin_profile_snapshots (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  snapshot_date TEXT NOT NULL,
  name TEXT,
  headline TEXT,
  title TEXT,
  status TEXT,
  changes_json TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL
);

-- LinkedIn employee activity feed (posts / comments / job changes).
CREATE TABLE IF NOT EXISTS linkedin_activities (
  id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  brand_id TEXT NOT NULL,
  external_id TEXT,
  activity_type TEXT NOT NULL DEFAULT 'post',  -- post | comment | reaction | job_change
  text TEXT,
  url TEXT,
  posted_at TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL
);

"""

# Indexes are created after additive migrations so they can reference
# columns that may have only just been added to a legacy database.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_records_brand ON records(brand_id);
CREATE INDEX IF NOT EXISTS idx_records_dimension ON records(dimension, channel);
CREATE INDEX IF NOT EXISTS idx_records_occurred ON records(occurred_at);
CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_id);
CREATE INDEX IF NOT EXISTS idx_market_share_snapshots_scope ON market_share_snapshots(country, snapshot_date, brand_id);
CREATE INDEX IF NOT EXISTS idx_source_brand_runs_due ON source_brand_runs(source_id, last_collect_at);
CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand_id);
CREATE INDEX IF NOT EXISTS idx_links_brand ON links(brand_id, dimension, channel);
CREATE INDEX IF NOT EXISTS idx_sales_metrics_brand ON sales_metrics(brand_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_sales_metrics_link ON sales_metrics(link_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_sales_listings_brand ON sales_listings(brand_id, channel);
CREATE INDEX IF NOT EXISTS idx_sales_listings_link ON sales_listings(link_id);
CREATE INDEX IF NOT EXISTS idx_web_snapshots_monitor ON web_snapshots(monitor_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_web_snapshot_analyses_range ON web_snapshot_analyses(brand_id, monitor_id, start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_voc_actions_status ON voc_actions(status, brand_id);
CREATE INDEX IF NOT EXISTS idx_creators_brand ON creators(brand_id, platform);
CREATE INDEX IF NOT EXISTS idx_record_product_matches_brand ON record_product_matches(brand_id, product_id);
CREATE INDEX IF NOT EXISTS idx_record_product_matches_record ON record_product_matches(record_id);
CREATE INDEX IF NOT EXISTS idx_creator_candidates_brand ON creator_candidates(brand_id, review_status, platform);
CREATE INDEX IF NOT EXISTS idx_creator_candidate_evidence_candidate ON creator_candidate_evidence(candidate_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_creator_candidate_evidence_product ON creator_candidate_evidence(brand_id, product_id);
CREATE INDEX IF NOT EXISTS idx_creator_map_snapshots_scope ON creator_map_snapshots(brand_id, product_id, platform, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_job_postings_brand ON job_postings(brand_id, platform);
CREATE INDEX IF NOT EXISTS idx_job_postings_link ON job_postings(link_id);
CREATE INDEX IF NOT EXISTS idx_job_snapshots_brand ON job_snapshots(brand_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_job_snapshots_posting ON job_snapshots(posting_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_li_profiles_brand ON linkedin_profiles(brand_id);
CREATE INDEX IF NOT EXISTS idx_li_profile_snapshots_profile ON linkedin_profile_snapshots(profile_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_li_profile_snapshots_brand ON linkedin_profile_snapshots(brand_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_li_activities_brand ON linkedin_activities(brand_id, posted_at);
CREATE INDEX IF NOT EXISTS idx_li_activities_profile ON linkedin_activities(profile_id, posted_at);
"""


_DB_BUSY_TIMEOUT_MS = max(5_000, int(os.environ.get("MONITOR_DB_BUSY_TIMEOUT_MS", "30_000")))
_DB_LOCK_RETRIES = max(1, int(os.environ.get("MONITOR_DB_LOCK_RETRIES", "6")))


class ResilientConnection(sqlite3.Connection):
    """Retry transient SQLite writer races instead of failing the request."""

    @staticmethod
    def _is_busy(exc: sqlite3.OperationalError) -> bool:
        message = str(exc).lower()
        return any(text in message for text in ("database is locked", "database table is locked", "database is busy"))

    def _retry(self, operation, *args, **kwargs):
        delay = 0.05
        for attempt in range(_DB_LOCK_RETRIES):
            try:
                return operation(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if not self._is_busy(exc) or attempt >= _DB_LOCK_RETRIES - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 1.0)

    def execute(self, sql, parameters=()):
        return self._retry(super().execute, sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self._retry(super().executemany, sql, seq_of_parameters)

    def executescript(self, sql_script):
        return self._retry(super().executescript, sql_script)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI may run a sync dependency's setup and the
    # path operation on different threadpool workers, so a per-request connection
    # can legitimately move across threads (never used concurrently).
    # Keep write transactions short. Collectors perform network and browser
    # work while they hold a connection, so sqlite's default implicit
    # transaction can otherwise keep the database write lock for the entire
    # crawl. Autocommit makes each statement release its lock immediately;
    # callers that need an atomic operation can still use an explicit BEGIN.
    conn = sqlite3.connect(
        DB_PATH,
        timeout=_DB_BUSY_TIMEOUT_MS / 1000,
        isolation_level=None,
        factory=ResilientConnection,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={_DB_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# Additive migrations for databases created by earlier versions.
MIGRATIONS = [
    ("records", "brand_id", "TEXT"),
    ("records", "product_id", "TEXT"),
    ("records", "link_id", "TEXT"),
    ("records", "dimension", "TEXT"),
    ("records", "channel", "TEXT"),
    ("records", "metrics_json", "TEXT"),
    ("web_monitors", "brand_id", "TEXT"),
    ("web_monitors", "product_id", "TEXT"),
    ("web_monitors", "check_interval_minutes", "INTEGER NOT NULL DEFAULT 1440"),
    ("web_monitors", "snapshot_interval_minutes", "INTEGER NOT NULL DEFAULT 1440"),
    ("web_monitors", "last_check_at", "TEXT"),
    ("web_monitors", "snapshot_retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("web_monitors", "next_snapshot_retry_at", "TEXT"),
    ("web_monitors", "last_snapshot_attempt_at", "TEXT"),
    ("web_monitors", "last_change_score", "REAL NOT NULL DEFAULT 0"),
    ("web_monitors", "last_change_summary", "TEXT"),
    ("web_snapshots", "brand_id", "TEXT"),
    ("web_snapshots", "archive_size", "INTEGER"),
    ("web_snapshots", "visual_change_score", "REAL"),
    ("web_snapshots", "visual_change_ratio", "REAL"),
    ("web_snapshots", "visual_regions_json", "TEXT"),
    ("sources", "tier", "INTEGER NOT NULL DEFAULT 1"),
    ("sources", "needs_credentials", "INTEGER NOT NULL DEFAULT 0"),
    ("sources", "credential_key", "TEXT"),
    ("sources", "cadence", "TEXT NOT NULL DEFAULT 'daily'"),
    ("sources", "last_collect_at", "TEXT"),
    ("sources", "last_status", "TEXT"),
    ("sources", "last_error", "TEXT"),
    ("sources", "item_count", "INTEGER NOT NULL DEFAULT 0"),
    ("voc_actions", "brand_id", "TEXT"),
    ("voc_actions", "updated_at", "TEXT"),
    ("brands", "is_competitor", "INTEGER NOT NULL DEFAULT 0"),
    ("brands", "is_primary", "INTEGER NOT NULL DEFAULT 0"),
    ("brands", "slug", "TEXT"),
    ("brands", "updated_at", "TEXT"),
    ("sales_metrics", "asin", "TEXT"),
    ("sales_metrics", "bsr", "INTEGER"),
    ("sales_metrics", "title", "TEXT"),
    ("sales_metrics", "image_url", "TEXT"),
    ("sales_metrics", "change_score", "REAL"),
    ("sales_metrics", "changes_json", "TEXT"),
    ("publications", "traffic_lower", "INTEGER NOT NULL DEFAULT 0"),
    ("publications", "traffic_upper", "INTEGER NOT NULL DEFAULT 0"),
    ("publications", "popularity_rank", "INTEGER"),
    ("publications", "traffic_confidence", "TEXT NOT NULL DEFAULT 'low'"),
    ("publications", "traffic_as_of", "TEXT"),
    ("linkedin_profiles", "source_type", "TEXT NOT NULL DEFAULT 'company'"),
    ("linkedin_profiles", "notes", "TEXT"),
    ("linkedin_profiles", "last_profile_change_at", "TEXT"),
    ("market_share_snapshots", "app_rating", "REAL"),
    ("market_share_snapshots", "app_store_apps", "INTEGER NOT NULL DEFAULT 0"),
]


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            try:
                _ensure_column(conn, table, column, ddl)
            except sqlite3.OperationalError:
                pass
        conn.executescript(INDEXES)
        _backfill_brand_ids(conn)
        _cleanup_fake_boss_login_postings(conn)


def _cleanup_fake_boss_login_postings(conn: sqlite3.Connection) -> None:
    """Remove legacy login pages that older Boss fallback logic stored as jobs."""
    rows = conn.execute(
        """
        SELECT id FROM job_postings
        WHERE platform = 'boss'
          AND lower(COALESCE(url, '')) NOT LIKE '%/job_detail/%'
          AND (
            lower(COALESCE(title, '')) LIKE '%boss直聘注册登录%'
            OR lower(COALESCE(title, '')) LIKE '%boss直聘在线注册登录%'
            OR lower(COALESCE(url, '')) LIKE '%/web/user/%'
          )
        """
    ).fetchall()
    for row in rows:
        conn.execute("DELETE FROM job_snapshots WHERE posting_id = ?", (row["id"],))
        conn.execute("DELETE FROM job_postings WHERE id = ?", (row["id"],))


def _backfill_brand_ids(conn: sqlite3.Connection) -> None:
    """Link legacy text `brand` values on records to canonical brand rows."""
    has_legacy = {row["name"] for row in conn.execute("PRAGMA table_info(records)")}
    if "brand" not in has_legacy:
        return
    rows = conn.execute(
        "SELECT DISTINCT brand FROM records WHERE brand IS NOT NULL AND brand != '' "
        "AND (brand_id IS NULL OR brand_id = '')"
    ).fetchall()
    for row in rows:
        name = (row["brand"] or "").strip()
        if not name:
            continue
        existing = conn.execute(
            "SELECT id FROM brands WHERE lower(name) = lower(?)", (name,)
        ).fetchone()
        brand_id = existing["id"] if existing else new_id()
        if not existing:
            now = utc_now()
            conn.execute(
                "INSERT INTO brands (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (brand_id, name, now, now),
            )
        conn.execute(
            "UPDATE records SET brand_id = ? WHERE brand = ? AND (brand_id IS NULL OR brand_id = '')",
            (brand_id, name),
        )


def row_to_dict(row: sqlite3.Row | None) -> dict:
    return dict(row) if row is not None else {}
