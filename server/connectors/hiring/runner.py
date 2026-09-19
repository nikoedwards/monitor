"""Hiring collection runner: expand sources -> postings -> daily snapshots.

Mirrors the sales runner. For each active hiring source link it expands the
company/search page into the ``job_postings`` registry, then captures a daily
``job_snapshots`` row for every monitored open posting, detecting JD content
changes and open/closed transitions. A posting that disappears from a source
that expanded successfully is marked ``closed`` (the "招到人 -> 岗位下线" signal).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from urllib.parse import urlparse

from ...util import canonical_url, clean_text, new_id, today, utc_now
from . import pick_people_provider, pick_provider
from .base import JobRef, JobSnapshot, ProfileRef, ProfileSnapshot


_BROWSER_CAPTURE_LABEL = {
    "boss": "BOSS 浏览器采集助手",
    "linkedin": "LinkedIn 浏览器采集助手",
}
_BOSS_JOB_ID_RE = re.compile(r"/job_detail/([^/.?]+)", re.I)
_LINKEDIN_JOB_ID_RE = re.compile(r"/jobs/view/(?:[^/?]*-)?(\d+)", re.I)


def _jd_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _link_platform(link: dict) -> str:
    return (link.get("platform") or link.get("channel") or "").lower()


def _uses_browser_worker(link: dict) -> bool:
    """Return whether a link is owned by the local browser worker.

    BOSS is always browser-managed. LinkedIn keeps its existing Cookie/provider
    path, including manually captured LinkedIn pages.
    """
    if _link_platform(link) == "boss":
        return True
    host = (urlparse(link.get("url") or "").hostname or "").lower()
    return host == "zhipin.com" or host.endswith(".zhipin.com")


def _link_config(link: dict) -> dict:
    try:
        value = json.loads(link.get("config_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _upsert_posting(conn: sqlite3.Connection, link: dict, ref: JobRef) -> str:
    now = utc_now()
    canon = canonical_url(ref.url)
    ext = (ref.external_id or "").strip()
    existing = conn.execute(
        """
        SELECT * FROM job_postings
        WHERE link_id = ? AND ((external_id != '' AND external_id = ?) OR canonical_url = ?)
        LIMIT 1
        """,
        (link["id"], ext, canon),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE job_postings
            SET url = ?, canonical_url = ?,
                external_id = COALESCE(NULLIF(?, ''), external_id),
                title = COALESCE(NULLIF(?, ''), title),
                department = COALESCE(NULLIF(?, ''), department),
                city = COALESCE(NULLIF(?, ''), city),
                status = CASE WHEN status = 'closed' THEN 'open' ELSE status END,
                closed_at = CASE WHEN status = 'closed' THEN NULL ELSE closed_at END,
                last_seen = ?, updated_at = ?
            WHERE id = ?
            """,
            (ref.url, canon, ext, clean_text(ref.title), clean_text(ref.department),
             clean_text(ref.city), now, now, existing["id"]),
        )
        return existing["id"]

    posting_id = new_id()
    conn.execute(
        """
        INSERT INTO job_postings (id, brand_id, link_id, platform, external_id, url, canonical_url,
            title, department, city, jd_text, jd_hash, status, posted_at, first_seen, last_seen,
            config_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, '{}', ?, ?)
        """,
        (
            posting_id, link["brand_id"], link["id"], _link_platform(link), ext, ref.url, canon,
            clean_text(ref.title), clean_text(ref.department), clean_text(ref.city),
            ref.jd_text or "", _jd_hash(ref.jd_text or ""), ref.posted_at or None, now, now, now, now,
        ),
    )
    return posting_id


def _diff_fingerprint(old_fp: dict, new_fp: dict) -> list[dict]:
    changes: list[dict] = []
    for key, new_val in new_fp.items():
        old_val = old_fp.get(key)
        if old_val == new_val:
            continue
        if old_val in (None, "") and new_val in (None, ""):
            continue
        changes.append({"field": key, "from": old_val, "to": new_val})
    return changes


def _record_snapshot(conn: sqlite3.Connection, posting: dict, snap: JobSnapshot) -> dict:
    now = utc_now()
    day = today()
    config = json.loads(posting.get("config_json") or "{}")
    old_fp = config.get("fingerprint") or {}
    new_fp = snap.fingerprint_fields()

    changes: list[dict] = []
    change_score = 0.0
    if old_fp:
        changes = _diff_fingerprint(old_fp, new_fp)
        change_score = round(len(changes) / max(1, len(new_fp)), 4)

    status = "closed" if snap.is_open is False else "open"
    conn.execute(
        "DELETE FROM job_snapshots WHERE posting_id = ? AND snapshot_date = ?",
        (posting["id"], day),
    )
    conn.execute(
        """
        INSERT INTO job_snapshots (id, posting_id, brand_id, link_id, snapshot_date, platform,
            status, is_open, title, department, city, posted_at, refreshed_at, applicant_signal,
            change_score, changes_json, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(), posting["id"], posting["brand_id"], posting.get("link_id"), day,
            posting.get("platform"), status,
            None if snap.is_open is None else int(snap.is_open),
            clean_text(snap.title) or posting.get("title"), clean_text(snap.department),
            clean_text(snap.city), snap.posted_at or None, snap.refreshed_at or None,
            snap.applicant_signal, change_score, json.dumps(changes, ensure_ascii=False),
            json.dumps(snap.raw, ensure_ascii=False), now,
        ),
    )

    config["fingerprint"] = new_fp
    last_change_at = now if changes else posting.get("last_change_at")
    closed_at = now if snap.is_open is False else posting.get("closed_at")
    conn.execute(
        """
        UPDATE job_postings
        SET title = COALESCE(NULLIF(?, ''), title),
            department = COALESCE(NULLIF(?, ''), department),
            city = COALESCE(NULLIF(?, ''), city),
            jd_text = COALESCE(NULLIF(?, ''), jd_text),
            jd_hash = CASE WHEN ? != '' THEN ? ELSE jd_hash END,
            status = ?, closed_at = ?, refreshed_at = ?, last_seen = ?,
            last_status = ?, last_error = ?, last_change_at = ?, config_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            clean_text(snap.title), clean_text(snap.department), clean_text(snap.city),
            snap.jd_text or "", snap.jd_text or "", _jd_hash(snap.jd_text or ""),
            status, closed_at, snap.refreshed_at or None, now, snap.status, snap.error,
            last_change_at, json.dumps(config, ensure_ascii=False), now, posting["id"],
        ),
    )
    return {"changed": bool(changes), "status": snap.status, "job_status": status}


def _browser_job_id(platform: str, url: str) -> str:
    matcher = _BOSS_JOB_ID_RE if platform == "boss" else _LINKEDIN_JOB_ID_RE
    match = matcher.search(url or "")
    return match.group(1) if match else ""


def _browser_capture_link(
    conn: sqlite3.Connection,
    brand_id: str,
    platform: str,
    source_url: str,
    source_title: str = "",
) -> dict:
    canon = canonical_url(source_url)
    platform_clause = "platform = ?"
    platform_params: tuple = (platform,)
    if platform == "boss":
        # Older rows may have a blank or legacy platform value even though the
        # URL is a BOSS source. Reuse that configured row instead of creating a
        # paused duplicate when the local worker falls back to direct DB mode.
        platform_clause = "(platform = ? OR lower(COALESCE(url, '')) LIKE '%zhipin.com%')"
        platform_params = (platform,)
    exact = conn.execute(
        f"""
        SELECT * FROM links
        WHERE brand_id = ? AND dimension = 'hiring' AND {platform_clause} AND canonical_url = ?
        LIMIT 1
        """,
        (brand_id, *platform_params, canon),
    ).fetchone()
    if not exact and platform == "boss":
        # Legacy rows sometimes stored the non-canonical URL in both URL
        # columns. Compare a normalized value in Python before creating a new
        # helper source so those rows are upgraded in place.
        candidates = conn.execute(
            """
            SELECT * FROM links
            WHERE brand_id = ? AND dimension = 'hiring'
              AND lower(COALESCE(url, '')) LIKE '%zhipin.com%'
            ORDER BY created_at
            """,
            (brand_id,),
        ).fetchall()
        for candidate in candidates:
            if canonical_url(candidate["url"] or "") == canon:
                exact = candidate
                break
    if exact:
        link = dict(exact)
        if platform == "boss":
            config = _link_config(link)
            config["browser_automation"] = True
            config.setdefault("browser_capture", True)
            conn.execute(
                """
                UPDATE links
                SET channel = 'boss', platform = 'boss', config_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(config, ensure_ascii=False), utc_now(), link["id"]),
            )
            link["config_json"] = json.dumps(config, ensure_ascii=False)
            link["channel"] = "boss"
            link["platform"] = "boss"
        return link

    label = _BROWSER_CAPTURE_LABEL[platform]
    helper = conn.execute(
        """
        SELECT * FROM links
        WHERE brand_id = ? AND dimension = 'hiring' AND platform = ? AND label = ?
        LIMIT 1
        """,
        (brand_id, platform, label),
    ).fetchone()
    if helper:
        link = dict(helper)
        if platform == "boss":
            config = _link_config(link)
            config["browser_automation"] = True
            config.setdefault("browser_capture", True)
            conn.execute(
                "UPDATE links SET config_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(config, ensure_ascii=False), utc_now(), link["id"]),
            )
            link["config_json"] = json.dumps(config, ensure_ascii=False)
        return link

    now = utc_now()
    link_id = new_id()
    conn.execute(
        """
        INSERT INTO links (id, brand_id, dimension, channel, platform, url, canonical_url,
            label, cadence, status, config_json, created_at, updated_at)
        VALUES (?, ?, 'hiring', ?, ?, ?, ?, ?, 'manual', 'paused', ?, ?, ?)
        """,
        (
            link_id, brand_id, platform, platform, source_url, canon, label,
            json.dumps(
                {
                    "browser_capture": True,
                    "browser_automation": platform == "boss",
                    "first_source_title": clean_text(source_title),
                },
                ensure_ascii=False,
            ),
            now, now,
        ),
    )
    return dict(conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone())


def ingest_browser_hiring_capture(
    conn: sqlite3.Connection,
    brand: dict,
    *,
    platform: str,
    source_url: str,
    source_title: str = "",
    page_status: str = "ok",
    page_error: str = "",
    jobs: list[dict] | None = None,
) -> dict:
    """Ingest jobs extracted by the user's normal signed-in browser.

    This path deliberately does not automate login, solve challenges, or mark
    unseen list items closed. It only records content that the user can already
    view in the active browser tab.
    """
    platform = (platform or "").lower()
    if platform not in _BROWSER_CAPTURE_LABEL:
        raise ValueError("浏览器助手目前仅支持 BOSS 直聘和 LinkedIn 职位。")

    expected_hosts = {
        "boss": ("zhipin.com",),
        "linkedin": ("linkedin.com",),
    }[platform]

    def valid_host(value: str) -> bool:
        host = (urlparse(value).hostname or "").lower()
        return any(host == root or host.endswith(f".{root}") for root in expected_hosts)

    if not valid_host(source_url):
        raise ValueError("当前页面域名与所选招聘平台不匹配。")

    link = _browser_capture_link(conn, brand["id"], platform, source_url, source_title)
    now = utc_now()
    blocked_capture = page_status == "blocked"
    if blocked_capture and not jobs:
        error = clean_text(page_error) or "页面需要登录或安全验证，请在浏览器中完成后重新采集。"
        conn.execute(
            "UPDATE links SET last_collect_at = ?, last_status = 'blocked', last_error = ?, updated_at = ? WHERE id = ?",
            (now, error[:300], now, link["id"]),
        )
        return {"link_id": link["id"], "captured": 0, "changed": 0, "closed": 0, "errors": 1, "status": "blocked"}

    summary = {
        "link_id": link["id"],
        "captured": 0,
        "changed": 0,
        "closed": 0,
        "errors": 1 if blocked_capture else 0,
        "status": "blocked" if blocked_capture else "ok",
    }
    seen: set[str] = set()
    for item in jobs or []:
        url = (item.get("url") or "").strip()
        if not valid_host(url):
            summary["errors"] += 1
            continue
        external_id = _browser_job_id(platform, url)
        if not external_id:
            summary["errors"] += 1
            continue
        canon = canonical_url(url)
        if not canon or canon in seen:
            continue
        seen.add(canon)

        ref = JobRef(
            url=url,
            external_id=external_id,
            title=item.get("title") or "",
            department=item.get("department") or "",
            city=item.get("city") or "",
            jd_text=item.get("jd_text") or "",
            posted_at=item.get("posted_at") or "",
            raw=item.get("raw") or {},
        )
        posting_id = _upsert_posting(conn, link, ref)
        posting = dict(conn.execute("SELECT * FROM job_postings WHERE id = ?", (posting_id,)).fetchone())
        # Listing cards often omit JD/city/department. Preserve the most complete
        # known values so a partial browser capture does not create false changes.
        snap = JobSnapshot(
            title=item.get("title") or posting.get("title") or "",
            department=item.get("department") or posting.get("department") or "",
            city=item.get("city") or posting.get("city") or "",
            jd_text=item.get("jd_text") or posting.get("jd_text") or "",
            posted_at=item.get("posted_at") or posting.get("posted_at") or "",
            refreshed_at=item.get("refreshed_at") or "",
            applicant_signal=item.get("applicant_signal") or "",
            is_open=item.get("is_open"),
            status="ok" if item.get("jd_text") else "partial",
            raw={
                **(item.get("raw") or {}),
                "provider": "browser_helper",
                "source_url": source_url,
                "source_title": clean_text(source_title),
            },
        )
        result = _record_snapshot(conn, posting, snap)
        summary["captured"] += 1
        summary["changed"] += int(result["changed"])
        summary["closed"] += int(result["job_status"] == "closed")

    if blocked_capture:
        link_status = "blocked"
        link_error = clean_text(page_error) or "访问职位详情时遇到登录或安全验证，请人工处理后重试。"
    else:
        link_status = "ok" if summary["captured"] else "partial"
        link_error = "" if summary["captured"] else "当前页面没有发现可识别的职位；请打开职位列表或职位详情页后重试。"
    conn.execute(
        "UPDATE links SET last_collect_at = ?, last_status = ?, last_error = ?, updated_at = ? WHERE id = ?",
        (now, link_status, link_error, now, link["id"]),
    )
    summary["status"] = link_status
    return summary


def run_hiring_collection(conn: sqlite3.Connection, brand: dict, link_id: str | None = None) -> dict:
    """Expand configured hiring links into postings and capture monitored postings."""
    summary = {"links": 0, "postings": 0, "captured": 0, "changed": 0, "closed": 0, "errors": 0}

    link_clause = "AND id = ?" if link_id else ""
    link_params: tuple = (brand["id"], link_id) if link_id else (brand["id"],)
    links = conn.execute(
        f"""
        SELECT * FROM links
        WHERE brand_id = ? AND dimension = 'hiring' AND status = 'active'
              AND url IS NOT NULL AND url != '' {link_clause}
        """,
        link_params,
    ).fetchall()

    for row in links:
        link = dict(row)
        if _uses_browser_worker(link):
            continue
        platform = _link_platform(link)
        provider = pick_provider(platform, conn)
        if provider is None:
            continue
        summary["links"] += 1
        try:
            refs = provider.expand(conn, link)
        except Exception as exc:  # noqa: BLE001
            conn.execute(
                "UPDATE links SET last_status = ?, last_error = ?, last_collect_at = ?, updated_at = ? WHERE id = ?",
                ("error", str(exc)[:300], utc_now(), utc_now(), link["id"]),
            )
            summary["errors"] += 1
            continue

        seen_ids: list[str] = []
        real_expansion = any((r.external_id or "").strip() for r in refs)
        for ref in refs:
            pid = _upsert_posting(conn, link, ref)
            seen_ids.append(pid)
            summary["postings"] += 1

        # Disappearance = "招到人/岗位下线": close open postings not seen this run.
        if real_expansion and seen_ids:
            placeholders = ",".join("?" for _ in seen_ids)
            stale = conn.execute(
                f"""
                SELECT * FROM job_postings
                WHERE link_id = ? AND status = 'open' AND id NOT IN ({placeholders})
                """,
                (link["id"], *seen_ids),
            ).fetchall()
            for post_row in stale:
                _close_posting(conn, dict(post_row))
                summary["closed"] += 1

        conn.execute(
            "UPDATE links SET last_status = ?, last_error = '', last_collect_at = ?, updated_at = ? WHERE id = ?",
            ("ok", utc_now(), utc_now(), link["id"]),
        )

    # Capture monitored open postings (fetch full JD + status).
    listing_clause = "AND link_id = ?" if link_id else ""
    listing_params: tuple = (brand["id"], link_id) if link_id else (brand["id"],)
    postings = conn.execute(
        f"""
        SELECT * FROM job_postings
        WHERE brand_id = ? AND status = 'open' {listing_clause}
        ORDER BY last_seen DESC LIMIT 200
        """,
        listing_params,
    ).fetchall()

    for row in postings:
        posting = dict(row)
        source_link = conn.execute(
            "SELECT config_json, platform, url FROM links WHERE id = ?",
            (posting.get("link_id"),),
        ).fetchone()
        if source_link and _uses_browser_worker(dict(source_link)):
            continue
        provider = pick_provider(posting.get("platform"), conn)
        if provider is None:
            continue
        try:
            snap = provider.fetch(conn, posting)
        except Exception as exc:  # noqa: BLE001
            conn.execute(
                "UPDATE job_postings SET last_status = 'error', last_error = ?, last_seen = ?, updated_at = ? WHERE id = ?",
                (str(exc)[:300], utc_now(), utc_now(), posting["id"]),
            )
            summary["errors"] += 1
            continue
        result = _record_snapshot(conn, posting, snap)
        summary["captured"] += 1
        if result["changed"]:
            summary["changed"] += 1
        if result["job_status"] == "closed":
            summary["closed"] += 1
        if snap.status in ("error", "blocked"):
            summary["errors"] += 1

    return summary


def _close_posting(conn: sqlite3.Connection, posting: dict) -> None:
    now = utc_now()
    day = today()
    conn.execute(
        "DELETE FROM job_snapshots WHERE posting_id = ? AND snapshot_date = ?",
        (posting["id"], day),
    )
    conn.execute(
        """
        INSERT INTO job_snapshots (id, posting_id, brand_id, link_id, snapshot_date, platform,
            status, is_open, title, department, city, change_score, changes_json, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'closed', 0, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(), posting["id"], posting["brand_id"], posting.get("link_id"), day,
            posting.get("platform"), posting.get("title"), posting.get("department"),
            posting.get("city"), 1.0,
            json.dumps([{"field": "status", "from": "open", "to": "closed"}], ensure_ascii=False),
            json.dumps({"reason": "disappeared_from_source"}, ensure_ascii=False), now,
        ),
    )
    conn.execute(
        "UPDATE job_postings SET status = 'closed', closed_at = ?, last_change_at = ?, updated_at = ? WHERE id = ?",
        (now, now, now, posting["id"]),
    )


# ----------------------------------------------------------------- linkedin people
def _upsert_profile(conn: sqlite3.Connection, link: dict, ref: ProfileRef) -> str:
    now = utc_now()
    canon = canonical_url(ref.profile_url)
    ext = (ref.external_id or "").strip()
    existing = conn.execute(
        """
        SELECT * FROM linkedin_profiles
        WHERE brand_id = ? AND ((external_id != '' AND external_id = ?) OR canonical_url = ?)
        LIMIT 1
        """,
        (link["brand_id"], ext, canon),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE linkedin_profiles
            SET name = COALESCE(NULLIF(?, ''), name),
                headline = COALESCE(NULLIF(?, ''), headline),
                title = COALESCE(NULLIF(?, ''), title),
                profile_url = COALESCE(NULLIF(?, ''), profile_url),
                avatar_url = COALESCE(NULLIF(?, ''), avatar_url),
                status = 'active',
                last_seen = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                clean_text(ref.name), clean_text(ref.headline), clean_text(ref.title),
                ref.profile_url, ref.avatar_url, now, now, existing["id"],
            ),
        )
        return existing["id"]
    profile_id = new_id()
    conn.execute(
        """
        INSERT INTO linkedin_profiles (id, brand_id, link_id, source_type, external_id, name, headline, title,
            profile_url, canonical_url, avatar_url, status, monitor, first_seen, last_seen,
            created_at, updated_at)
        VALUES (?, ?, ?, 'company', ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?, ?)
        """,
        (
            profile_id, link["brand_id"], link["id"], ext, clean_text(ref.name),
            clean_text(ref.headline), clean_text(ref.title), ref.profile_url, canon,
            ref.avatar_url, now, now, now, now,
        ),
    )
    return profile_id


def _profile_snapshot_to_dict(snap: ProfileSnapshot, profile: dict) -> dict:
    return {
        "name": clean_text(snap.name) or profile.get("name") or "",
        "headline": clean_text(snap.headline) or profile.get("headline") or "",
        "title": clean_text(snap.title) or profile.get("title") or "",
    }


def _record_profile_snapshot(conn: sqlite3.Connection, profile: dict, snap: ProfileSnapshot) -> dict:
    now = utc_now()
    day = today()
    if snap.status in ("blocked", "error"):
        conn.execute(
            "UPDATE linkedin_profiles SET last_status = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (snap.status, snap.error, now, profile["id"]),
        )
        return {"changed": False, "status": snap.status}

    current = _profile_snapshot_to_dict(snap, profile)
    previous_row = conn.execute(
        "SELECT * FROM linkedin_profile_snapshots WHERE profile_id = ? ORDER BY snapshot_date DESC, created_at DESC LIMIT 1",
        (profile["id"],),
    ).fetchone()
    previous = (
        {key: previous_row[key] or "" for key in ("name", "headline", "title")}
        if previous_row
        else {key: profile.get(key) or "" for key in ("name", "headline", "title")}
    )
    changes = _diff_fingerprint(previous, current) if any(previous.values()) else []
    earlier_today_changes = (
        json.loads(previous_row["changes_json"] or "[]")
        if previous_row and previous_row["snapshot_date"] == day
        else []
    )
    snapshot_changes = earlier_today_changes + changes

    conn.execute(
        "DELETE FROM linkedin_profile_snapshots WHERE profile_id = ? AND snapshot_date = ?",
        (profile["id"], day),
    )
    conn.execute(
        """
        INSERT INTO linkedin_profile_snapshots (id, profile_id, brand_id, snapshot_date,
            name, headline, title, status, changes_json, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(), profile["id"], profile["brand_id"], day,
            current["name"], current["headline"], current["title"],
            "active" if snap.is_active is not False else "inactive",
            json.dumps(snapshot_changes, ensure_ascii=False), json.dumps(snap.raw, ensure_ascii=False), now,
        ),
    )
    conn.execute(
        """
        UPDATE linkedin_profiles
        SET name = ?, headline = ?, title = ?, status = ?, last_seen = ?,
            last_status = ?, last_error = ?,
            last_profile_change_at = CASE WHEN ? THEN ? ELSE last_profile_change_at END,
            updated_at = ?
        WHERE id = ?
        """,
        (
            current["name"], current["headline"], current["title"],
            "inactive" if snap.is_active is False else "active", now,
            snap.status, snap.error, int(bool(changes)), now, now, profile["id"],
        ),
    )
    if changes:
        labels = {"name": "姓名", "headline": "头衔", "title": "职位"}
        text = "；".join(
            f"{labels.get(change['field'], change['field'])}：{change.get('from') or '—'} → {change.get('to') or '—'}"
            for change in changes
        )
        external_id = f"profile-change:{day}:{_jd_hash(json.dumps(changes, sort_keys=True))[:12]}"
        exists = conn.execute(
            "SELECT 1 FROM linkedin_activities WHERE profile_id = ? AND external_id = ? LIMIT 1",
            (profile["id"], external_id),
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO linkedin_activities (id, profile_id, brand_id, external_id,
                    activity_type, text, url, posted_at, raw_json, created_at)
                VALUES (?, ?, ?, ?, 'profile_change', ?, ?, ?, ?, ?)
                """,
                (
                    new_id(), profile["id"], profile["brand_id"], external_id, text,
                    profile.get("profile_url") or "", now,
                    json.dumps({"changes": changes}, ensure_ascii=False), now,
                ),
            )
            conn.execute(
                "UPDATE linkedin_profiles SET last_activity_at = ? WHERE id = ?",
                (now, profile["id"]),
            )
    return {"changed": bool(changes), "status": snap.status}


def _record_activities(conn: sqlite3.Connection, profile: dict, activities: list) -> int:
    now = utc_now()
    added = 0
    for act in activities:
        text = clean_text(act.text)
        if not text:
            continue
        ext = (act.external_id or "").strip() or _jd_hash(text)[:32]
        dup = conn.execute(
            "SELECT 1 FROM linkedin_activities WHERE profile_id = ? AND external_id = ? LIMIT 1",
            (profile["id"], ext),
        ).fetchone()
        if dup:
            continue
        conn.execute(
            """
            INSERT INTO linkedin_activities (id, profile_id, brand_id, external_id, activity_type,
                text, url, posted_at, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(), profile["id"], profile["brand_id"], ext, act.activity_type or "post",
                text[:2000], act.url, act.posted_at or None,
                json.dumps(act.raw, ensure_ascii=False), now,
            ),
        )
        added += 1
    if activities:
        conn.execute(
            "UPDATE linkedin_profiles SET last_activity_at = ?, last_seen = ?, updated_at = ? WHERE id = ?",
            (now, now, now, profile["id"]),
        )
    return added


def discover_linkedin_people_candidates(
    conn: sqlite3.Connection,
    brand: dict,
    link_id: str | None = None,
) -> dict:
    """Import employee candidates from configured LinkedIn company People pages."""
    summary = {"links": 0, "profiles": 0, "errors": 0}
    link_clause = "AND id = ?" if link_id else ""
    link_params: tuple = (brand["id"], link_id) if link_id else (brand["id"],)
    links = conn.execute(
        f"""
        SELECT * FROM links
        WHERE brand_id = ? AND dimension = 'hiring' AND status = 'active'
              AND platform = 'linkedin_people' AND url IS NOT NULL AND url != '' {link_clause}
        """,
        link_params,
    ).fetchall()

    for row in links:
        link = dict(row)
        provider = pick_people_provider("linkedin", conn)
        if provider is None:
            continue
        summary["links"] += 1
        try:
            refs = provider.expand_profiles(conn, link)
        except Exception as exc:  # noqa: BLE001
            conn.execute(
                "UPDATE links SET last_status = ?, last_error = ?, last_collect_at = ?, updated_at = ? WHERE id = ?",
                ("error", str(exc)[:300], utc_now(), utc_now(), link["id"]),
            )
            summary["errors"] += 1
            continue
        for ref in refs:
            _upsert_profile(conn, link, ref)
            summary["profiles"] += 1
        status = "ok" if refs else "partial"
        error = "" if refs else "当前 LinkedIn People 页面没有发现可导入员工，请检查页面 URL、登录 Cookie 或访问状态。"
        conn.execute(
            "UPDATE links SET last_status = ?, last_error = ?, last_collect_at = ?, updated_at = ? WHERE id = ?",
            (status, error, utc_now(), utc_now(), link["id"]),
        )
    return summary


def run_linkedin_people_collection(conn: sqlite3.Connection, brand: dict, link_id: str | None = None) -> dict:
    """Discover candidates, then capture monitored LinkedIn profiles and activity."""
    discovery = discover_linkedin_people_candidates(conn, brand, link_id=link_id)
    summary = {
        **discovery,
        "profile_changes": 0,
        "activities": 0,
    }

    provider = pick_people_provider("linkedin", conn)
    if provider is not None:
        profiles = conn.execute(
            f"""
            SELECT * FROM linkedin_profiles
            WHERE brand_id = ? AND status = 'active' AND monitor = 1
            ORDER BY last_activity_at IS NULL, last_seen DESC LIMIT 60
            """,
            (brand["id"],),
        ).fetchall()
        for row in profiles:
            profile = dict(row)
            try:
                snapshot = provider.fetch_profile(conn, profile)
                profile_result = _record_profile_snapshot(conn, profile, snapshot)
                if profile_result["changed"]:
                    summary["profile_changes"] += 1
                if snapshot.status in ("error", "blocked"):
                    summary["errors"] += 1
                activities = provider.fetch_activities(conn, profile)
            except Exception as exc:  # noqa: BLE001
                conn.execute(
                    "UPDATE linkedin_profiles SET last_status = 'error', last_error = ?, updated_at = ? WHERE id = ?",
                    (str(exc)[:300], utc_now(), profile["id"]),
                )
                summary["errors"] += 1
                continue
            summary["activities"] += _record_activities(conn, profile, activities)

    return summary
