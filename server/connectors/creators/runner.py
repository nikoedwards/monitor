"""Creator collection runner + roster materialization.

Each platform's connector ``collect`` returns unified ``records`` payloads (so the
shared ``run_collector`` + scheduler persist + dedupe them like any other source).
``rebuild_roster`` then re-aggregates a brand's creator posts into the ``creators``
table so the dashboard and the LLM report can read a per-creator view.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ...util import clean_text, new_id, utc_now
from ..collectors import brand_queries
from . import pick_provider
from .base import CreatorPost, brand_signals, detect_collaboration
from .evidence import build_collection_evidence
from .products import creator_product_queries, load_product_signals, match_record_to_products

# platform -> connector source_id (registry).
PLATFORM_SOURCE = {
    "youtube": "youtube_search",
    "instagram": "instagram_listening",
    "tiktok": "tiktok_listening",
    "x": "x_search",
}


def _creator_search_terms(brand: dict, product_queries: list[str]) -> set[str]:
    """Return exact, configured terms that are safe search evidence.

    A short brand name such as ``Comu`` is too ambiguous to treat every public
    search hit as a brand mention. Configured monitoring keywords and product
    queries are explicit signals, so a result found through one of those terms
    remains useful when YouTube blocks the watch-page metadata.
    """
    values: list[str] = []
    keywords = brand.get("monitoring_keywords") or brand.get("monitoring_keywords_json")
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords or "[]")
        except (TypeError, ValueError):
            keywords = []
    values.extend(str(value).strip() for value in (keywords or []) if str(value).strip())
    values.extend(str(value).strip() for value in product_queries if str(value).strip())
    name = clean_text(brand.get("name")).casefold()
    # A bare brand name is not safe search evidence for a catalog brand: names
    # can also be people, places, or unrelated terms (for example, PLAUD is a
    # surname in several Spanish-language news videos). Only configured terms
    # that add information beyond the bare brand remain trusted here.
    return {
        value.casefold()
        for value in values
        if value and value.casefold() != name
    }


def _has_trusted_creator_search_hit(raw: dict | None, brand: dict, product_queries: list[str]) -> bool:
    if not isinstance(raw, dict):
        return False
    # This flag is set only when yt-dlp had to fall back to flat search. A
    # normal provider result still needs visible brand/product evidence; the
    # fallback flag records that the search engine itself supplied the signal.
    if not raw.get("search_query_evidence"):
        return False
    values = raw.get("matched_queries") or raw.get("query") or []
    if isinstance(values, str):
        values = [values]
    trusted = _creator_search_terms(brand, product_queries)
    trusted_variants = trusted | {
        re.sub(r"\s+", "", item.lstrip("#"))
        for item in trusted
    }
    return any(
        re.sub(r"\s+", "", str(value).strip().casefold().lstrip("#")) in trusted_variants
        for value in values
        if str(value).strip()
    )


def _creator_record_is_relevant(
    *,
    raw: dict | None,
    brand: dict,
    products: list[dict],
    product_matches: list[dict],
    transcript_hit: bool,
    brand_mentions: bool,
    product_queries: list[str],
) -> bool:
    """Apply the same evidence gate to new and already stored creator posts."""
    if product_matches or transcript_hit:
        return True
    trusted_search_hit = _has_trusted_creator_search_hit(raw, brand, product_queries)
    # Once a brand has a product catalog, a bare brand mention is ambiguous and
    # cannot pass review by itself. This is what prevents person-name collisions
    # such as “Félix Plaud” from entering the PLAUD hardware feed.
    if products:
        return trusted_search_hit
    return brand_mentions or trusted_search_hit


def _payload(brand: dict, post: CreatorPost, collab: dict, source_id: str, *, product_matches: list[dict] | None = None, signals: dict | None = None) -> dict:
    raw = dict(post.raw or {})
    evidence = build_collection_evidence(title=post.title, body=post.body, brand=brand, signals=signals, raw=raw, product_matches=product_matches)
    raw.update({"evidence_type": evidence["evidence_type"], "scope": evidence["scope"], "matched_in": evidence["matched_in"], "matched_text": evidence["matched_text"], "matched_queries": evidence["matched_queries"], "collection_evidence": evidence})
    return {
        "source_id": source_id,
        "brand_id": brand.get("id"),
        "external_id": f"{brand.get('id')}:{post.platform}:{post.external_id}",
        "data_type": "creator_post",
        "dimension": "marketing",
        "channel": "creators",
        "platform": post.platform,
        "title": post.title or post.author,
        "author": post.author or post.author_handle,
        "body": post.body or post.title or post.author or "(no caption)",
        "url": post.url,
        "occurred_at": post.occurred_at,
        "metrics": {
            "views": post.views,
            "likes": post.likes,
            "comments": post.comments,
            "shares": post.shares,
            "engagement": post.engagement(),
            "engagement_rate": post.engagement_rate(),
            "follower_count": post.follower_count,
            "author_handle": post.author_handle,
            "author_url": post.author_url,
            "avatar_url": post.avatar_url,
            "thumbnail_url": post.thumbnail_url,
            # Keep a stable alias for clients that call the post image a cover.
            "cover_url": post.thumbnail_url,
            "is_collab": collab["is_collab"],
            "is_sponsored": collab["is_sponsored"],
            "collab_type": collab["collab_type"],
            "mentions": collab["mentions"],
        },
        "raw": raw,
    }


def _collect_platform(conn: sqlite3.Connection, brand: dict, platform: str) -> list[dict]:
    provider = pick_provider(platform, conn)
    if provider is None:
        return []
    # YouTube search is quota-expensive. Reserve half the small query budget for
    # catalog products instead of letting a long brand-keyword list crowd them out.
    base_queries = brand_queries(brand)[:4]
    product_queries = creator_product_queries(conn, brand.get("id") or "", limit=4)
    base_queries.extend(query for query in product_queries if query.strip().lower() not in {item.strip().lower() for item in base_queries})
    queries: list[str] = []
    seen: set[str] = set()
    for query in base_queries:
        value = str(query).strip()
        if not value:
            continue
        variants = [value]
        # Instagram and TikTok keyword endpoints usually treat a hashtag as a
        # separate search mode. Query both the literal phrase and its compact
        # hashtag form so a brand keyword such as "NotePin" also finds
        # #NotePin posts. The provider still applies the platform's own result
        # and time-window limits.
        if platform in {"instagram", "tiktok"} and not value.startswith("#"):
            tag = re.sub(r"\s+", "", value)
            if tag and len(tag) >= 2:
                variants.append(f"#{tag}")
        for variant in variants:
            key = variant.casefold()
            if key in seen:
                continue
            seen.add(key)
            queries.append(variant)
            if len(queries) >= 12:
                break
        if len(queries) >= 12:
            break
    if not queries:
        return []
    posts = provider.collect(conn, brand, queries) or []
    signals = brand_signals(brand)
    products = load_product_signals(conn, brand.get("id") or "")
    source_id = PLATFORM_SOURCE.get(platform, platform)
    payloads: list[dict] = []
    for post in posts:
        trusted_search_hit = _has_trusted_creator_search_hit(post.raw, brand, product_queries)
        collab = detect_collaboration(f"{post.title} {post.body}", brand, signals)
        product_matches = match_record_to_products(
            {
                "title": post.title,
                "body": post.body,
                "url": post.url,
                "author": post.author,
                "raw": post.raw,
            },
            products,
        )
        transcript_matches = (post.raw or {}).get("transcript_matches") if isinstance(post.raw, dict) else None
        transcript_hit = isinstance(transcript_matches, list) and bool(transcript_matches)
        if transcript_hit and not collab["is_collab"]:
            mentions = {clean_text(item.get("matched_text") or item.get("matched_query")) for item in transcript_matches if isinstance(item, dict)}
            collab = {**collab, "is_collab": True, "collab_type": "transcript", "mentions": sorted(item for item in mentions if item)}
        if not _creator_record_is_relevant(
            raw=post.raw,
            brand=brand,
            products=products,
            product_matches=product_matches,
            transcript_hit=transcript_hit,
            brand_mentions=bool(collab["mentions"]),
            product_queries=product_queries,
        ):
            continue
        # TODO(multimodal): soft placements (product shown on-screen, no caption
        # mention) are invisible to text detection. A future vision pass over the
        # thumbnail/video frames should upgrade collab_type from "none" here.
        payloads.append(_payload(brand, post, collab, source_id, product_matches=product_matches, signals=signals))
    return payloads


# ----------------------------------------------------- per-platform collectors
def collect_youtube(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    return _collect_platform(conn, brand, "youtube")


def collect_instagram(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    return _collect_platform(conn, brand, "instagram")


def collect_tiktok(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    return _collect_platform(conn, brand, "tiktok")


def collect_x(conn: sqlite3.Connection, brand: dict) -> list[dict]:
    return _collect_platform(conn, brand, "x")


# ------------------------------------------------------------- roster rebuild
def _num(metrics: dict, key: str) -> int:
    value = metrics.get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def rebuild_roster(conn: sqlite3.Connection, brand_id: str) -> int:
    """Recompute the ``creators`` roster for one brand from its creator records.

    Idempotent: deletes the brand's roster rows and re-aggregates from records,
    so it always matches ``records`` regardless of how those were collected.
    """
    if not brand_id:
        return 0
    rows = conn.execute(
        "SELECT * FROM records WHERE brand_id = ? AND channel = 'creators'",
        (brand_id,),
    ).fetchall()

    agg: dict[tuple[str, str], dict] = {}
    for row in rows:
        try:
            metrics = json.loads(row["metrics_json"] or "{}")
        except (TypeError, ValueError):
            metrics = {}
        platform = row["platform"] or "unknown"
        handle = clean_text(metrics.get("author_handle")) or clean_text(row["author"]) or "unknown"
        key = (platform, handle.lower())
        entry = agg.setdefault(key, {
            "platform": platform,
            "handle": handle,
            "name": clean_text(row["author"]) or handle,
            "url": metrics.get("author_url") or "",
            "avatar_url": metrics.get("avatar_url") or "",
            "follower_count": 0,
            "post_count": 0,
            "collab_count": 0,
            "sponsored_count": 0,
            "total_views": 0,
            "total_engagement": 0,
            "first_seen": row["occurred_at"],
            "last_seen": row["occurred_at"],
            "last_collab_at": None,
        })
        entry["post_count"] += 1
        if metrics.get("is_collab"):
            entry["collab_count"] += 1
        if metrics.get("is_sponsored"):
            entry["sponsored_count"] += 1
        entry["total_views"] += _num(metrics, "views")
        entry["total_engagement"] += _num(metrics, "engagement")
        entry["follower_count"] = max(entry["follower_count"], _num(metrics, "follower_count"))
        occurred = row["occurred_at"] or ""
        if occurred and (not entry["first_seen"] or occurred < entry["first_seen"]):
            entry["first_seen"] = occurred
        if occurred and (not entry["last_seen"] or occurred > entry["last_seen"]):
            entry["last_seen"] = occurred
        if metrics.get("is_collab") and occurred and (not entry["last_collab_at"] or occurred > entry["last_collab_at"]):
            entry["last_collab_at"] = occurred

    now = utc_now()
    conn.execute("DELETE FROM creators WHERE brand_id = ?", (brand_id,))
    for entry in agg.values():
        conn.execute(
            """
            INSERT INTO creators (id, brand_id, platform, handle, name, url, avatar_url,
                follower_count, post_count, collab_count, sponsored_count, total_views,
                total_engagement, first_seen, last_seen, last_collab_at, raw_json,
                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                new_id(), brand_id, entry["platform"], entry["handle"], entry["name"],
                entry["url"], entry["avatar_url"], entry["follower_count"], entry["post_count"],
                entry["collab_count"], entry["sponsored_count"], entry["total_views"],
                entry["total_engagement"], entry["first_seen"], entry["last_seen"],
                entry["last_collab_at"], now, now,
            ),
        )
    return len(agg)
