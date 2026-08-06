"""Editorial candidate workflow for a Gladia-style creator landscape.

Discovery data remains evidence, not the final map.  Creators are first
materialized into a review queue, where a human can approve, prioritize or
reject them.  Only approved/priority creators appear in the curated map.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from urllib.parse import unquote, urlparse

from ...util import clean_text, new_id, utc_now

CURATION_PLATFORMS = ("youtube", "instagram", "tiktok")
REVIEW_STATUSES = ("pending", "approved", "priority", "rejected")
RELATIONSHIP_STATUSES = ("potential", "contacted", "collaborating", "past")


def _json(value: str | None, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _num(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def detect_platform(url: str | None, platform: str | None = None) -> str:
    value = clean_text(platform).lower()
    aliases = {"yt": "youtube", "youtube.com": "youtube", "ig": "instagram", "tt": "tiktok"}
    value = aliases.get(value, value)
    if value in CURATION_PLATFORMS:
        return value
    host = (urlparse(clean_text(url)).hostname or "").lower().rstrip(".")
    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        return "youtube"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "instagram"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    return ""


def handle_from_url(url: str | None, platform: str) -> str:
    try:
        parts = [unquote(part) for part in urlparse(clean_text(url)).path.split("/") if part]
    except ValueError:
        return ""
    if not parts:
        return ""
    if platform == "youtube" and parts[0].lower() in {"channel", "c", "user"} and len(parts) > 1:
        return parts[1].lstrip("@").strip()
    return parts[0].lstrip("@").strip()


def identity_key(platform: str, handle: str | None, url: str | None, name: str | None) -> str:
    handle_value = clean_text(handle).lstrip("@").casefold()
    if handle_value:
        return handle_value
    parsed_handle = handle_from_url(url, platform).casefold()
    if parsed_handle:
        return parsed_handle
    clean_url = clean_text(url).rstrip("/").casefold()
    if clean_url:
        return clean_url
    return re.sub(r"\s+", " ", clean_text(name)).casefold()


def _record_products(conn: sqlite3.Connection, brand_id: str) -> dict[str, list[dict]]:
    rows = conn.execute(
        """
        SELECT rpm.record_id, rpm.product_id, rpm.confidence, rpm.match_type,
               rpm.evidence_json, p.name AS product_name
        FROM record_product_matches rpm
        JOIN products p ON p.id = rpm.product_id
        WHERE rpm.brand_id = ?
        """,
        (brand_id,),
    ).fetchall()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        item["evidence"] = _json(item.pop("evidence_json", None), [])
        grouped[row["record_id"]].append(item)
    return grouped


def _relevance(item: dict) -> float:
    score = 10.0
    if item["collab_count"]:
        score += 24
    if item["sponsored_count"]:
        score += 10
    score += min(item["evidence_count"] * 6, 24)
    score += min(item["product_match_count"] * 5, 15)
    score += min(math.log1p(item["total_engagement"]) * 2.8, 12)
    score += min(math.log1p(item["total_views"]) * 1.2, 8)
    return round(min(score, 100), 1)


def rebuild_candidates(conn: sqlite3.Connection, brand_id: str) -> dict:
    """Upsert collected creator records into the candidate review pool."""
    if not brand_id:
        return {"records": 0, "candidates": 0, "evidence": 0}
    rows = conn.execute(
        "SELECT * FROM records WHERE brand_id = ? AND channel = 'creators'",
        (brand_id,),
    ).fetchall()
    product_matches = _record_products(conn, brand_id)
    aggregated: dict[tuple[str, str], dict] = {}
    record_payloads: list[tuple[tuple[str, str], sqlite3.Row, dict, dict, list[dict]]] = []
    for row in rows:
        platform = detect_platform(row["url"], row["platform"])
        if platform not in CURATION_PLATFORMS:
            continue
        metrics = _json(row["metrics_json"], {})
        raw = _json(row["raw_json"], {})
        handle = clean_text(metrics.get("author_handle")) or clean_text(row["author"])
        key = identity_key(platform, handle, metrics.get("author_url") or row["url"], row["author"])
        if not key:
            continue
        agg_key = (platform, key)
        item = aggregated.setdefault(agg_key, {
            "platform": platform,
            "identity_key": key,
            "handle": handle,
            "name": clean_text(row["author"]) or handle,
            "url": clean_text(metrics.get("author_url")) or clean_text(row["url"]),
            "avatar_url": clean_text(metrics.get("avatar_url")),
            "follower_count": 0,
            "post_count": 0,
            "collab_count": 0,
            "sponsored_count": 0,
            "total_views": 0,
            "total_engagement": 0,
            "evidence_count": 0,
            "product_match_count": 0,
            "first_seen": row["occurred_at"],
            "last_seen": row["occurred_at"],
            "last_collab_at": None,
        })
        item["post_count"] += 1
        item["evidence_count"] += 1
        item["product_match_count"] += len(product_matches.get(row["id"], []))
        if metrics.get("is_collab"):
            item["collab_count"] += 1
        if metrics.get("is_sponsored"):
            item["sponsored_count"] += 1
        item["follower_count"] = max(item["follower_count"], _num(metrics.get("follower_count")))
        item["total_views"] += _num(metrics.get("views"))
        item["total_engagement"] += _num(metrics.get("engagement"))
        occurred = row["occurred_at"] or ""
        if occurred and (not item["first_seen"] or occurred < item["first_seen"]):
            item["first_seen"] = occurred
        if occurred and (not item["last_seen"] or occurred > item["last_seen"]):
            item["last_seen"] = occurred
        if metrics.get("is_collab") and occurred and (
            not item["last_collab_at"] or occurred > item["last_collab_at"]
        ):
            item["last_collab_at"] = occurred
        record_payloads.append((agg_key, row, metrics, raw, product_matches.get(row["id"], [])))

    existing = {
        (row["platform"], row["identity_key"]): dict(row)
        for row in conn.execute(
            "SELECT * FROM creator_candidates WHERE brand_id = ?",
            (brand_id,),
        ).fetchall()
    }
    now = utc_now()
    candidate_ids: dict[tuple[str, str], str] = {}
    for agg_key, item in aggregated.items():
        current = existing.get(agg_key)
        candidate_id = current["id"] if current else new_id()
        candidate_ids[agg_key] = candidate_id
        relevance_score = _relevance(item)
        if current:
            source = "manual+collected" if current.get("discovery_source") in {"manual", "manual+collected"} else "collected"
            conn.execute(
                """
                UPDATE creator_candidates SET handle = ?, name = ?, url = ?, avatar_url = ?,
                    discovery_source = ?, relevance_score = ?, follower_count = ?, post_count = ?,
                    collab_count = ?, sponsored_count = ?, total_views = ?, total_engagement = ?,
                    evidence_count = ?, first_seen = ?, last_seen = ?, last_collab_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    item["handle"], item["name"], item["url"], item["avatar_url"], source,
                    relevance_score, item["follower_count"], item["post_count"], item["collab_count"],
                    item["sponsored_count"], item["total_views"], item["total_engagement"],
                    item["evidence_count"], item["first_seen"], item["last_seen"], item["last_collab_at"],
                    now, candidate_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO creator_candidates
                    (id, brand_id, platform, identity_key, handle, name, url, avatar_url,
                     discovery_source, relevance_score, follower_count, post_count, collab_count,
                     sponsored_count, total_views, total_engagement, evidence_count, first_seen,
                     last_seen, last_collab_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'collected', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id, brand_id, item["platform"], item["identity_key"], item["handle"],
                    item["name"], item["url"], item["avatar_url"], relevance_score,
                    item["follower_count"], item["post_count"], item["collab_count"],
                    item["sponsored_count"], item["total_views"], item["total_engagement"],
                    item["evidence_count"], item["first_seen"], item["last_seen"],
                    item["last_collab_at"], now, now,
                ),
            )

    conn.execute(
        "DELETE FROM creator_candidate_evidence WHERE evidence_type = 'record' "
        "AND candidate_id IN (SELECT id FROM creator_candidates WHERE brand_id = ?)",
        (brand_id,),
    )
    evidence_count = 0
    for agg_key, row, metrics, raw, matches in record_payloads:
        candidate_id = candidate_ids.get(agg_key)
        if not candidate_id:
            continue
        queries = raw.get("matched_queries") or ([raw.get("query")] if raw.get("query") else [])
        query_text = " · ".join(clean_text(value) for value in queries if clean_text(value))
        product_rows = matches or [{"product_id": "", "confidence": 0.72 if metrics.get("is_collab") else 0.45, "match_type": "brand", "evidence": []}]
        for match in product_rows:
            evidence = {
                "match_type": match.get("match_type"),
                "signals": match.get("evidence") or [],
                "mentions": metrics.get("mentions") or [],
                "collab_type": metrics.get("collab_type"),
            }
            conn.execute(
                """
                INSERT OR REPLACE INTO creator_candidate_evidence
                    (id, candidate_id, brand_id, record_id, product_id, evidence_type, query,
                     title, excerpt, url, confidence, occurred_at, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, 'record', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(), candidate_id, brand_id, row["id"], match.get("product_id") or "",
                    query_text, row["title"], clean_text(row["body"])[:500], row["url"],
                    float(match.get("confidence") or 0), row["occurred_at"],
                    json.dumps(evidence, ensure_ascii=False), now,
                ),
            )
            evidence_count += 1
    return {"records": len(rows), "candidates": len(aggregated), "evidence": evidence_count}


def ensure_candidates(conn: sqlite3.Connection, brand_id: str) -> None:
    record_count = conn.execute(
        "SELECT COUNT(*) n FROM records WHERE brand_id = ? AND channel = 'creators'",
        (brand_id,),
    ).fetchone()["n"]
    candidate_count = conn.execute(
        "SELECT COUNT(*) n FROM creator_candidates WHERE brand_id = ?",
        (brand_id,),
    ).fetchone()["n"]
    if record_count and not candidate_count:
        rebuild_candidates(conn, brand_id)


def import_candidates(conn: sqlite3.Connection, brand_id: str, rows: list[dict]) -> dict:
    now = utc_now()
    created = 0
    updated = 0
    ids: list[str] = []
    for raw in rows:
        url = clean_text(raw.get("url"))
        platform = detect_platform(url, raw.get("platform"))
        if platform not in CURATION_PLATFORMS:
            continue
        handle = clean_text(raw.get("handle")).lstrip("@") or handle_from_url(url, platform)
        name = clean_text(raw.get("name")) or handle
        key = identity_key(platform, handle, url, name)
        if not key:
            continue
        existing = conn.execute(
            "SELECT id, discovery_source FROM creator_candidates WHERE brand_id = ? AND platform = ? AND identity_key = ?",
            (brand_id, platform, key),
        ).fetchone()
        if existing:
            source = "manual+collected" if existing["discovery_source"] in {"collected", "manual+collected"} else "manual"
            conn.execute(
                """
                UPDATE creator_candidates SET handle = COALESCE(NULLIF(?, ''), handle),
                    name = COALESCE(NULLIF(?, ''), name), url = COALESCE(NULLIF(?, ''), url),
                    notes = COALESCE(NULLIF(?, ''), notes), discovery_source = ?,
                    follower_count = MAX(follower_count, ?), updated_at = ? WHERE id = ?
                """,
                (handle, name, url, clean_text(raw.get("notes")), source, _num(raw.get("follower_count")), now, existing["id"]),
            )
            candidate_id = existing["id"]
            updated += 1
        else:
            candidate_id = new_id()
            conn.execute(
                """
                INSERT INTO creator_candidates
                    (id, brand_id, platform, identity_key, handle, name, url, review_status,
                     relationship_status, discovery_source, relevance_score, follower_count,
                     notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 'potential', 'manual', 25, ?, ?, ?, ?)
                """,
                (candidate_id, brand_id, platform, key, handle, name, url, _num(raw.get("follower_count")), clean_text(raw.get("notes")), now, now),
            )
            created += 1
        ids.append(candidate_id)
    return {"created": created, "updated": updated, "ids": ids}


def update_candidate(conn: sqlite3.Connection, candidate_id: str, payload: dict) -> dict:
    row = conn.execute("SELECT * FROM creator_candidates WHERE id = ?", (candidate_id,)).fetchone()
    if not row:
        raise ValueError("Creator candidate not found")
    review_status = payload.get("review_status")
    relationship_status = payload.get("relationship_status")
    if review_status is not None and review_status not in REVIEW_STATUSES:
        raise ValueError("Invalid review status")
    if relationship_status is not None and relationship_status not in RELATIONSHIP_STATUSES:
        raise ValueError("Invalid relationship status")
    next_review = review_status if review_status is not None else row["review_status"]
    next_relationship = relationship_status if relationship_status is not None else row["relationship_status"]
    notes = payload.get("notes") if payload.get("notes") is not None else row["notes"]
    now = utc_now()
    reviewed_at = now if review_status is not None and review_status != "pending" else row["reviewed_at"]
    conn.execute(
        "UPDATE creator_candidates SET review_status = ?, relationship_status = ?, notes = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
        (next_review, next_relationship, notes, reviewed_at, now, candidate_id),
    )
    return candidate_to_dict(conn.execute("SELECT * FROM creator_candidates WHERE id = ?", (candidate_id,)).fetchone())


def candidate_to_dict(row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    post_count = item.get("post_count") or 0
    followers = item.get("follower_count") or 0
    item["avg_engagement"] = round((item.get("total_engagement") or 0) / post_count, 1) if post_count else 0
    item["engagement_rate"] = round((item.get("total_engagement") or 0) / followers, 6) if followers else None
    return item


def _scope_rows(
    conn: sqlite3.Connection,
    brand_id: str,
    product_id: str | None = None,
    platform: str | None = None,
    review_status: str | None = None,
    q: str | None = None,
) -> list[dict]:
    clauses = ["c.brand_id = ?"]
    params: list = [brand_id]
    if platform and platform != "all":
        clauses.append("c.platform = ?")
        params.append(platform)
    if review_status and review_status != "all":
        clauses.append("c.review_status = ?")
        params.append(review_status)
    if product_id:
        clauses.append(
            "EXISTS (SELECT 1 FROM creator_candidate_evidence e WHERE e.candidate_id = c.id AND e.product_id = ?)"
        )
        params.append(product_id)
    if q:
        clauses.append("(lower(c.name) LIKE ? OR lower(c.handle) LIKE ? OR lower(c.notes) LIKE ?)")
        term = f"%{q.casefold()}%"
        params.extend([term, term, term])
    rows = conn.execute(
        "SELECT c.* FROM creator_candidates c WHERE " + " AND ".join(clauses) +
        " ORDER BY CASE c.review_status WHEN 'priority' THEN 0 WHEN 'approved' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END, "
        "c.relevance_score DESC, c.total_engagement DESC, c.name",
        params,
    ).fetchall()
    candidates = [candidate_to_dict(row) for row in rows]
    if not candidates:
        return []
    ids = [item["id"] for item in candidates]
    placeholders = ",".join("?" for _ in ids)
    product_rows = conn.execute(
        f"""
        SELECT DISTINCT e.candidate_id, e.product_id, p.name
        FROM creator_candidate_evidence e JOIN products p ON p.id = e.product_id
        WHERE e.candidate_id IN ({placeholders}) AND e.product_id != ''
        ORDER BY p.name
        """,
        ids,
    ).fetchall()
    products: dict[str, list[dict]] = defaultdict(list)
    for row in product_rows:
        products[row["candidate_id"]].append({"id": row["product_id"], "name": row["name"]})
    for item in candidates:
        item["products"] = products.get(item["id"], [])
    return candidates


def _candidate_map(candidates: list[dict]) -> dict:
    rows = [item for item in candidates if item.get("review_status") in {"approved", "priority"}][:40]
    reaches = [math.log1p(max(item.get("total_views") or 0, item.get("follower_count") or 0, 1)) for item in rows]
    low = min(reaches) if reaches else 0
    high = max(reaches) if reaches else 0
    counts: Counter[str] = Counter()
    points = []
    for item, reach_log in zip(rows, reaches):
        depth = 10 + (item.get("collab_count") or 0) * 14 + (item.get("sponsored_count") or 0) * 8 + min(item.get("post_count") or 0, 10) * 2
        if item.get("relationship_status") == "collaborating":
            depth += 18
        elif item.get("relationship_status") == "past":
            depth += 10
        elif item.get("relationship_status") == "contacted":
            depth += 5
        x = round(min(depth, 92), 1)
        reach = max(item.get("total_views") or 0, item.get("follower_count") or 0, 1)
        rate = (item.get("total_engagement") or 0) / reach
        avg = item.get("avg_engagement") or 0
        y = round(min(10 + math.log1p(avg) * 9 + min(rate * 300, 24), 92), 1)
        if not item.get("post_count"):
            y = 10.0
        if x >= 50 and y >= 50:
            quadrant = "core"
        elif x < 50 and y >= 50:
            quadrant = "potential"
        elif x >= 50 and y < 50:
            quadrant = "scale"
        else:
            quadrant = "observe"
        counts[quadrant] += 1
        size_scale = 0.5 if math.isclose(low, high) else (reach_log - low) / (high - low)
        points.append({
            "id": item["id"], "name": item.get("name") or item.get("handle") or "Unknown",
            "handle": item.get("handle"), "platform": item["platform"], "url": item.get("url"),
            "x": x, "y": y, "size": round(13 + size_scale * 18, 1), "quadrant": quadrant,
            "collab_count": item.get("collab_count") or 0, "post_count": item.get("post_count") or 0,
            "total_views": item.get("total_views") or 0, "total_engagement": item.get("total_engagement") or 0,
            "engagement_rate": item.get("engagement_rate"), "review_status": item.get("review_status"),
        })
    return {
        "points": points,
        "quadrants": [
            {"key": "core", "label": "核心伙伴", "total": counts["core"]},
            {"key": "potential", "label": "潜力黑马", "total": counts["potential"]},
            {"key": "scale", "label": "铺量达人", "total": counts["scale"]},
            {"key": "observe", "label": "观察池", "total": counts["observe"]},
        ],
    }


def candidate_dashboard(
    conn: sqlite3.Connection,
    brand_id: str,
    product_id: str | None = None,
    platform: str | None = None,
    review_status: str | None = None,
    q: str | None = None,
) -> dict:
    ensure_candidates(conn, brand_id)
    scoped = _scope_rows(conn, brand_id, product_id, platform, review_status, q)
    all_scoped = _scope_rows(conn, brand_id, product_id, platform, None, None)
    counts = Counter(item["review_status"] for item in all_scoped)
    return {
        "candidates": scoped,
        "totals": {
            "all": len(all_scoped), "pending": counts["pending"], "approved": counts["approved"],
            "priority": counts["priority"], "rejected": counts["rejected"],
            "curated": counts["approved"] + counts["priority"],
        },
        "curated_map": _candidate_map(all_scoped),
    }


def candidate_evidence(conn: sqlite3.Connection, candidate_id: str, product_id: str | None = None) -> list[dict]:
    params: list = [candidate_id]
    product_clause = ""
    if product_id:
        product_clause = " AND e.product_id = ?"
        params.append(product_id)
    rows = conn.execute(
        """
        SELECT e.*, p.name AS product_name
        FROM creator_candidate_evidence e
        LEFT JOIN products p ON p.id = e.product_id
        WHERE e.candidate_id = ?
        """ + product_clause + " ORDER BY e.occurred_at DESC, e.created_at DESC",
        params,
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["evidence"] = _json(item.pop("evidence_json", None), {})
        out.append(item)
    return out


def save_map_snapshot(
    conn: sqlite3.Connection,
    brand_id: str,
    product_id: str | None = None,
    platform: str | None = None,
    title: str | None = None,
) -> dict:
    dashboard = candidate_dashboard(conn, brand_id, product_id, platform)
    map_data = dashboard["curated_map"]
    if not map_data["points"]:
        raise ValueError("No curated creators to snapshot")
    now = utc_now()
    snapshot_date = now[:10]
    product_key = product_id or ""
    platform_key = platform if platform and platform != "all" else "all"
    existing = conn.execute(
        "SELECT id FROM creator_map_snapshots WHERE brand_id = ? AND product_id = ? AND platform = ? AND snapshot_date = ?",
        (brand_id, product_key, platform_key, snapshot_date),
    ).fetchone()
    snapshot_id = existing["id"] if existing else new_id()
    conn.execute(
        """
        INSERT INTO creator_map_snapshots
            (id, brand_id, product_id, platform, snapshot_date, title, points_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(brand_id, product_id, platform, snapshot_date) DO UPDATE SET
            title = excluded.title, points_json = excluded.points_json, created_at = excluded.created_at
        """,
        (snapshot_id, brand_id, product_key, platform_key, snapshot_date, clean_text(title), json.dumps(map_data, ensure_ascii=False), now),
    )
    return {
        "id": snapshot_id,
        "brand_id": brand_id,
        "product_id": product_key,
        "platform": platform_key,
        "snapshot_date": snapshot_date,
        "title": clean_text(title),
        "created_at": now,
        "map": map_data,
    }


def list_map_snapshots(
    conn: sqlite3.Connection,
    brand_id: str,
    product_id: str | None = None,
    platform: str | None = None,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM creator_map_snapshots
        WHERE brand_id = ? AND product_id = ? AND platform = ?
        ORDER BY snapshot_date DESC, created_at DESC LIMIT 24
        """,
        (brand_id, product_id or "", platform if platform and platform != "all" else "all"),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["map"] = _json(item.pop("points_json", None), {"points": [], "quadrants": []})
        out.append(item)
    return out
