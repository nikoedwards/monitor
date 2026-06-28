"""Media/publication enrichment: a domain-keyed media library with heuristic
traffic/authority estimates, DB-cached, plus a pluggable real-traffic seam.

The library upgrades the previously hardcoded `PUBLICATION_REACH_HINTS` list
into a persisted `publications` table so media records can be enriched with
Meltwater-style dimensions (monthly traffic / reach, authority, tier, country,
language, AVE).
"""
from __future__ import annotations

import sqlite3

from ..config import CREDENTIALS
from ..util import clean_text, utc_now

# domain -> (name, est_monthly_traffic, authority, tier, country, language)
SEED_PUBLICATIONS: dict[str, tuple[str, int, int, str, str, str]] = {
    "reuters.com": ("Reuters", 90_000_000, 95, "tier_1", "US", "en"),
    "apnews.com": ("AP News", 80_000_000, 94, "tier_1", "US", "en"),
    "bloomberg.com": ("Bloomberg", 75_000_000, 93, "tier_1", "US", "en"),
    "forbes.com": ("Forbes", 110_000_000, 92, "tier_1", "US", "en"),
    "wsj.com": ("The Wall Street Journal", 70_000_000, 93, "tier_1", "US", "en"),
    "nytimes.com": ("The New York Times", 380_000_000, 95, "tier_1", "US", "en"),
    "bbc.com": ("BBC", 350_000_000, 95, "tier_1", "GB", "en"),
    "bbc.co.uk": ("BBC", 300_000_000, 95, "tier_1", "GB", "en"),
    "cnn.com": ("CNN", 380_000_000, 93, "tier_1", "US", "en"),
    "theguardian.com": ("The Guardian", 350_000_000, 93, "tier_1", "GB", "en"),
    "techcrunch.com": ("TechCrunch", 18_000_000, 90, "tier_2", "US", "en"),
    "theverge.com": ("The Verge", 35_000_000, 90, "tier_2", "US", "en"),
    "wired.com": ("Wired", 22_000_000, 89, "tier_2", "US", "en"),
    "engadget.com": ("Engadget", 20_000_000, 87, "tier_2", "US", "en"),
    "fastcompany.com": ("Fast Company", 12_000_000, 86, "tier_2", "US", "en"),
    "adweek.com": ("Adweek", 4_500_000, 82, "tier_2", "US", "en"),
    "businesswire.com": ("Business Wire", 6_000_000, 78, "wire", "US", "en"),
    "prnewswire.com": ("PR Newswire", 7_000_000, 78, "wire", "US", "en"),
    "globenewswire.com": ("GlobeNewswire", 5_000_000, 75, "wire", "US", "en"),
    "einpresswire.com": ("EIN Presswire", 2_000_000, 60, "wire", "US", "en"),
    "yahoo.com": ("Yahoo", 400_000_000, 90, "tier_1", "US", "en"),
    "msn.com": ("MSN", 400_000_000, 88, "tier_1", "US", "en"),
}

# Tier -> (default monthly traffic, default authority) used as heuristic fallback.
TIER_DEFAULTS: dict[str, tuple[int, int]] = {
    "tier_1": (5_000_000, 88),
    "tier_2": (1_500_000, 80),
    "wire": (500_000, 70),
    "tier_3": (220_000, 55),
    "tier_4": (80_000, 40),
    "unknown": (25_000, 25),
}

# Rough CPM (USD per 1000 impressions) and placement weight for AVE estimation.
_AVE_CPM = 25.0
_AVE_PLACEMENT_WEIGHT = {"earned": 1.0, "paid_pr": 0.4}


def _heuristic_tier(name: str, domain: str) -> str:
    key = f"{name} {domain}".lower()
    if any(t in key for t in ("times", "post", "journal", "tribune", "news", "daily", "business")):
        return "tier_3"
    if domain:
        return "tier_4"
    return "unknown"


def _provider_traffic(domain: str) -> int | None:
    """Pluggable seam for a real traffic API (SimilarWeb/Tranco/etc.).

    Returns monthly traffic when a provider credential is configured; otherwise
    None so callers fall back to the heuristic library. Intentionally a no-op
    until a real provider is wired in.
    """
    if not CREDENTIALS.get("traffic_api_key"):
        return None
    return None  # seam reserved; no live provider implemented yet


def estimate_ave(monthly_traffic: int, coverage_type: str) -> int:
    weight = _AVE_PLACEMENT_WEIGHT.get(coverage_type, 1.0)
    return round((monthly_traffic / 1000.0) * _AVE_CPM * weight)


def enrich_publication(conn: sqlite3.Connection, name: str, domain: str) -> dict:
    """Return enriched publication metrics for a media outlet, caching to DB.

    Lookup order: persisted table -> seed library -> heuristic estimate. Results
    (except table hits) are upserted into `publications` so the library grows
    as new outlets are seen.
    """
    domain = clean_text(domain).lower()
    name = clean_text(name)

    if domain:
        row = conn.execute("SELECT * FROM publications WHERE domain = ?", (domain,)).fetchone()
        if row is not None:
            return _row_to_dict(row)

    if domain in SEED_PUBLICATIONS:
        seed_name, traffic, authority, tier, country, language = SEED_PUBLICATIONS[domain]
        record = {
            "domain": domain,
            "name": name or seed_name,
            "icon_url": _favicon(domain),
            "est_monthly_traffic": traffic,
            "authority": authority,
            "tier": tier,
            "country": country,
            "language": language,
            "source": "seed",
        }
    else:
        tier = _heuristic_tier(name, domain)
        provider_traffic = _provider_traffic(domain)
        traffic, authority = TIER_DEFAULTS.get(tier, TIER_DEFAULTS["unknown"])
        record = {
            "domain": domain,
            "name": name or domain,
            "icon_url": _favicon(domain),
            "est_monthly_traffic": provider_traffic or traffic,
            "authority": authority,
            "tier": tier,
            "country": "",
            "language": "",
            "source": "api" if provider_traffic else "heuristic",
        }

    if domain:
        _upsert(conn, record)
    return record


def _favicon(domain: str) -> str:
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64" if domain else ""


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "domain": row["domain"],
        "name": row["name"],
        "icon_url": row["icon_url"],
        "est_monthly_traffic": row["est_monthly_traffic"],
        "authority": row["authority"],
        "tier": row["tier"],
        "country": row["country"],
        "language": row["language"],
        "source": row["source"],
    }


def _upsert(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        """
        INSERT INTO publications (domain, name, icon_url, est_monthly_traffic, authority,
            tier, country, language, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            name = excluded.name,
            icon_url = excluded.icon_url,
            est_monthly_traffic = excluded.est_monthly_traffic,
            authority = excluded.authority,
            tier = excluded.tier,
            country = excluded.country,
            language = excluded.language,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (
            record["domain"], record["name"], record["icon_url"], record["est_monthly_traffic"],
            record["authority"], record["tier"], record["country"], record["language"],
            record["source"], utc_now(),
        ),
    )
