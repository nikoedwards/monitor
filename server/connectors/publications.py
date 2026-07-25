"""Media/publication enrichment backed by a cached traffic-estimate library.

Monthly traffic is never inferred from a publication name alone.  Known seed
values and optional paid/provider data take priority; other domains use their
public Tranco popularity rank to produce a deliberately broad traffic range.
The midpoint remains available for sorting and legacy calculations, while the
range/source/confidence fields make the uncertainty visible to callers.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from ..fetchers import FetchError, fetch_json
from ..util import clean_text, compact_key, utc_now

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

# Tier defaults now supply authority only.  They are not used as fake traffic.
TIER_DEFAULTS: dict[str, tuple[int, int]] = {
    "tier_1": (0, 88),
    "tier_2": (0, 80),
    "wire": (0, 70),
    "tier_3": (0, 55),
    "tier_4": (0, 40),
    "unknown": (0, 25),
}

_AVE_CPM = 25.0
_AVE_PLACEMENT_WEIGHT = {"earned": 1.0, "paid_pr": 0.4}
_TRANCO_URL = "https://tranco-list.eu/api/ranks/domain/{domain}"
_TRANCO_TTL = timedelta(days=30)
_UNAVAILABLE_TTL = timedelta(days=1)

# Calibrated order-of-magnitude anchors: Tranco rank -> monthly visits midpoint.
# These are intentionally broad proxies, not claimed measured traffic.
_TRAFFIC_ANCHORS = (
    (1, 2_000_000_000),
    (100, 400_000_000),
    (1_000, 60_000_000),
    (10_000, 8_000_000),
    (100_000, 1_000_000),
    (1_000_000, 120_000),
    (5_000_000, 25_000),
)

_COMMON_SECOND_LEVEL_SUFFIXES = {
    "ac.uk", "co.uk", "gov.uk", "org.uk",
    "asn.au", "com.au", "net.au", "org.au",
    "co.jp", "ne.jp", "or.jp",
    "com.cn", "net.cn", "org.cn",
    "co.nz", "com.br", "com.mx", "co.in", "co.kr", "com.sg", "com.hk", "com.tw",
}

_PUBLICATION_NAME_ALIASES = {
    compact_key(seed_name): domain
    for domain, (seed_name, *_rest) in SEED_PUBLICATIONS.items()
}
_PUBLICATION_NAME_ALIASES.update({
    "cnet": "cnet.com",
    "gizmodo": "gizmodo.com",
    "mashable": "mashable.com",
    "kotaku": "kotaku.com",
    "pcworld": "pcworld.com",
    "macworld": "macworld.com",
    "techradar": "techradar.com",
    "9to5toys": "9to5toys.com",
    "9to5google": "9to5google.com",
    "9to5mac": "9to5mac.com",
    "androidauthority": "androidauthority.com",
    "androidpolice": "androidpolice.com",
    "pcmag": "pcmag.com",
    "soundguys": "soundguys.com",
    "tomsguide": "tomsguide.com",
    "zdnet": "zdnet.com",
    "xda": "xda-developers.com",
})


def _heuristic_tier(name: str, domain: str) -> str:
    key = f"{name} {domain}".lower()
    if any(t in key for t in ("times", "post", "journal", "tribune", "news", "daily", "business")):
        return "tier_3"
    if domain:
        return "tier_4"
    return "unknown"


def _normalize_domain(value: str) -> str:
    domain = clean_text(value).lower().strip().strip(".")
    if "://" in domain:
        domain = domain.split("://", 1)[1].split("/", 1)[0]
    else:
        domain = domain.split("/", 1)[0]
    domain = domain.split("@")[-1].split(":", 1)[0].strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain


def _registrable_domain(domain: str) -> str:
    parts = [part for part in domain.split(".") if part]
    if len(parts) <= 2:
        return domain
    suffix = ".".join(parts[-2:])
    if suffix in _COMMON_SECOND_LEVEL_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _domain_candidates(domain: str) -> list[str]:
    normalized = _normalize_domain(domain)
    if not normalized:
        return []
    root = _registrable_domain(normalized)
    return [normalized] if root == normalized else [normalized, root]


def publication_domain_hint(name: str) -> str:
    """Resolve well-known publisher names when legacy records lack a domain."""
    return _PUBLICATION_NAME_ALIASES.get(compact_key(name), "")


def _round_estimate(value: float) -> int:
    value = max(0, value)
    if value < 100:
        return int(round(value))
    magnitude = 10 ** max(0, len(str(int(value))) - 2)
    return int(round(value / magnitude) * magnitude)


def _traffic_range_for_rank(rank: int) -> tuple[int, int, int]:
    """Return (lower, midpoint, upper) monthly visits for a Tranco rank."""
    rank = max(1, int(rank))
    anchors = _TRAFFIC_ANCHORS
    if rank <= anchors[0][0]:
        midpoint = float(anchors[0][1])
    elif rank >= anchors[-1][0]:
        midpoint = float(anchors[-1][1]) * anchors[-1][0] / rank
    else:
        midpoint = float(anchors[-1][1])
        for (left_rank, left_value), (right_rank, right_value) in zip(anchors, anchors[1:]):
            if left_rank <= rank <= right_rank:
                progress = (math.log10(rank) - math.log10(left_rank)) / (
                    math.log10(right_rank) - math.log10(left_rank)
                )
                midpoint = 10 ** (
                    math.log10(left_value) + progress * (math.log10(right_value) - math.log10(left_value))
                )
                break
    lower = _round_estimate(midpoint * 0.45)
    middle = _round_estimate(midpoint)
    upper = _round_estimate(midpoint * 2.2)
    return lower, middle, upper


def _rank_dimensions(rank: int) -> tuple[str, int]:
    if rank <= 1_000:
        return "tier_1", 90
    if rank <= 25_000:
        return "tier_2", 80
    if rank <= 500_000:
        return "tier_3", 65
    return "tier_4", 45


def _fetch_tranco_rank(domain: str) -> dict | None:
    had_response = False
    for candidate in _domain_candidates(domain):
        try:
            payload = fetch_json(_TRANCO_URL.format(domain=quote(candidate, safe="")), timeout=4)
        except (FetchError, TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        had_response = True
        ranks = payload.get("ranks")
        if not isinstance(ranks, list):
            continue
        valid = [item for item in ranks if isinstance(item, dict) and item.get("rank")]
        if not valid:
            continue
        latest = max(valid, key=lambda item: clean_text(item.get("date")))
        try:
            rank = int(latest["rank"])
        except (TypeError, ValueError):
            continue
        return {"rank": rank, "as_of": clean_text(latest.get("date")), "ranked_domain": candidate}
    if had_response:
        return {"rank": None, "as_of": utc_now()[:10], "ranked_domain": "", "status": "unranked"}
    return None


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cache_is_fresh(row: sqlite3.Row) -> bool:
    source = row["source"] or ""
    if source == "manual":
        return True
    if source == "seed":
        return bool(row["traffic_lower"] or row["traffic_upper"])
    updated_at = _parse_timestamp(row["updated_at"])
    if not updated_at:
        return False
    age = datetime.now(timezone.utc) - updated_at
    if source == "tranco_model":
        return bool(row["popularity_rank"]) and age < _TRANCO_TTL
    if source == "tranco_no_rank":
        return age < _TRANCO_TTL
    if source == "unavailable":
        return age < _UNAVAILABLE_TTL
    # Old heuristic/api rows (including the repeated 80K/220K values) refresh now.
    return False


def publication_needs_network(conn: sqlite3.Connection, domain: str) -> bool:
    """Whether refreshing this domain may require an outbound provider call."""
    domain = _normalize_domain(domain)
    if not domain or domain in SEED_PUBLICATIONS:
        return False
    row = conn.execute("SELECT * FROM publications WHERE domain = ?", (domain,)).fetchone()
    return row is None or not _cache_is_fresh(row)


def estimate_ave(monthly_traffic: int, coverage_type: str) -> int:
    weight = _AVE_PLACEMENT_WEIGHT.get(coverage_type, 1.0)
    return round((monthly_traffic / 1000.0) * _AVE_CPM * weight)


def enrich_publication(
    conn: sqlite3.Connection,
    name: str,
    domain: str,
    *,
    allow_network: bool = True,
) -> dict:
    """Return current publication metrics and cache them in ``publications``."""
    domain = _normalize_domain(domain)
    name = clean_text(name)
    row = None

    if domain:
        row = conn.execute("SELECT * FROM publications WHERE domain = ?", (domain,)).fetchone()
        if row is not None and _cache_is_fresh(row):
            return _row_to_dict(row)

    if domain in SEED_PUBLICATIONS:
        seed_name, traffic, authority, tier, country, language = SEED_PUBLICATIONS[domain]
        record = {
            "domain": domain,
            "name": name or seed_name,
            "icon_url": _favicon(domain),
            "est_monthly_traffic": traffic,
            "traffic_lower": traffic,
            "traffic_upper": traffic,
            "popularity_rank": None,
            "traffic_confidence": "medium",
            "traffic_as_of": "",
            "authority": authority,
            "tier": tier,
            "country": country,
            "language": language,
            "source": "seed",
        }
    else:
        heuristic_tier = _heuristic_tier(name, domain)
        _, heuristic_authority = TIER_DEFAULTS.get(heuristic_tier, TIER_DEFAULTS["unknown"])
        if not allow_network:
            if row is not None and row["source"] == "tranco_model":
                return _row_to_dict(row)
            return {
                "domain": domain,
                "name": name or domain or "Unknown publication",
                "icon_url": _favicon(domain),
                "est_monthly_traffic": 0,
                "traffic_lower": 0,
                "traffic_upper": 0,
                "popularity_rank": None,
                "traffic_confidence": "low",
                "traffic_as_of": "",
                "authority": heuristic_authority,
                "tier": heuristic_tier,
                "country": "",
                "language": "",
                "source": "unavailable",
            }
        rank_data = _fetch_tranco_rank(domain) if domain else None
        if rank_data and rank_data.get("rank"):
            lower, midpoint, upper = _traffic_range_for_rank(rank_data["rank"])
            tier, authority = _rank_dimensions(rank_data["rank"])
            record = {
                "domain": domain,
                "name": name or domain,
                "icon_url": _favicon(domain),
                "est_monthly_traffic": midpoint,
                "traffic_lower": lower,
                "traffic_upper": upper,
                "popularity_rank": rank_data["rank"],
                "traffic_confidence": "medium",
                "traffic_as_of": rank_data["as_of"],
                "authority": authority,
                "tier": tier,
                "country": "",
                "language": "",
                "source": "tranco_model",
            }
        else:
            record = {
                "domain": domain,
                "name": name or domain or "Unknown publication",
                "icon_url": _favicon(domain),
                "est_monthly_traffic": 0,
                "traffic_lower": 0,
                "traffic_upper": 0,
                "popularity_rank": None,
                "traffic_confidence": "low",
                "traffic_as_of": utc_now()[:10],
                "authority": heuristic_authority,
                "tier": heuristic_tier,
                "country": "",
                "language": "",
                "source": "tranco_no_rank" if rank_data else "unavailable",
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
        "traffic_lower": row["traffic_lower"],
        "traffic_upper": row["traffic_upper"],
        "popularity_rank": row["popularity_rank"],
        "traffic_confidence": row["traffic_confidence"],
        "traffic_as_of": row["traffic_as_of"],
        "authority": row["authority"],
        "tier": row["tier"],
        "country": row["country"],
        "language": row["language"],
        "source": row["source"],
    }


def _upsert(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        """
        INSERT INTO publications (domain, name, icon_url, est_monthly_traffic,
            traffic_lower, traffic_upper, popularity_rank, traffic_confidence, traffic_as_of,
            authority, tier, country, language, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            name = excluded.name,
            icon_url = excluded.icon_url,
            est_monthly_traffic = excluded.est_monthly_traffic,
            traffic_lower = excluded.traffic_lower,
            traffic_upper = excluded.traffic_upper,
            popularity_rank = excluded.popularity_rank,
            traffic_confidence = excluded.traffic_confidence,
            traffic_as_of = excluded.traffic_as_of,
            authority = excluded.authority,
            tier = excluded.tier,
            country = excluded.country,
            language = excluded.language,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (
            record["domain"], record["name"], record["icon_url"], record["est_monthly_traffic"],
            record.get("traffic_lower", 0), record.get("traffic_upper", 0), record.get("popularity_rank"),
            record.get("traffic_confidence", "low"), record.get("traffic_as_of"), record["authority"],
            record["tier"], record["country"], record["language"], record["source"], utc_now(),
        ),
    )
