"""Source-specific relevance checks shared by collectors and record queries."""
from __future__ import annotations

import re
from typing import Any


_REDDIT_POST_RE = re.compile(r"/comments/([a-z0-9]+)(?:[/?#]|$)", re.I)
_QUERY_PART_RE = re.compile(r"[A-Za-z0-9]+|[\u3400-\u9fff]+")


def reddit_post_id(url: str) -> str:
    """Return a Reddit post id; subreddit/profile/search URLs are not posts."""
    match = _REDDIT_POST_RE.search(url or "")
    return match.group(1) if match else ""


def search_query_parts(query: str) -> list[str]:
    """Split aliases such as ``NotePin`` into phrase parts (``Note``, ``Pin``)."""
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", query or "")
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", expanded)
    return _QUERY_PART_RE.findall(expanded)


def _query_pattern(query: str) -> re.Pattern[str] | None:
    parts = search_query_parts(query)
    if not parts:
        return None
    phrase = r"[\W_]*".join(re.escape(part) for part in parts)
    left = r"(?<![A-Za-z0-9])" if parts[0][0].isascii() else ""
    right = r"(?![A-Za-z0-9])" if parts[-1][-1].isascii() else ""
    return re.compile(f"{left}{phrase}{right}", re.I)


def query_match_evidence(query: str, title: str = "", body: str = "") -> dict[str, str] | None:
    """Require an alias/phrase match in the visible title or post body."""
    pattern = _query_pattern(query)
    if pattern is None:
        return None
    for field, value in (("title", title), ("body", body)):
        match = pattern.search(value or "")
        if match:
            return {"matched_in": field, "matched_text": match.group(0), "match_rule": "exact_alias"}
    return None


def google_news_match_evidence(
    query: str,
    brand_name: str = "",
    title: str = "",
    body: str = "",
) -> dict[str, str] | None:
    """Require visible Google News evidence for the trigger query or brand name.

    Google News RSS occasionally returns unrelated items for a keyword bucket.
    We therefore fail closed unless the RSS title/summary visibly contains the
    triggering phrase or the brand's primary name.
    """
    seen: set[str] = set()
    for candidate in (query, brand_name):
        candidate = str(candidate or "").strip()
        key = candidate.casefold()
        if not candidate or key in seen:
            continue
        seen.add(key)
        evidence = query_match_evidence(candidate, title, body)
        if evidence:
            return {
                **evidence,
                "matched_query": candidate,
                "validation_method": "rss_title_summary",
            }
    return None


def google_news_record_is_relevant(record: dict[str, Any], brand_name: str | None = None) -> bool:
    """Hide legacy Google News rows that have no visible brand evidence."""
    if record.get("source_id") != "google_news":
        return True
    raw = record.get("raw") or {}
    query = raw.get("query") if isinstance(raw, dict) else ""
    return google_news_match_evidence(
        str(query or ""),
        str(brand_name or ""),
        str(record.get("title") or ""),
        str(record.get("body") or ""),
    ) is not None


def reddit_search_record_is_relevant(record: dict[str, Any], brand_name: str | None = None) -> bool:
    """Hide legacy Reddit search false positives while retaining official hubs.

    Site-search records must have been triggered by the current primary brand
    name and match it in their title/body. Explicitly configured subreddit feeds
    have no query and remain a separate, intentionally broad official-hub stream.
    """
    if record.get("source_id") != "reddit_search":
        return True
    raw = record.get("raw") or {}
    query = raw.get("query") if isinstance(raw, dict) else None
    if not isinstance(query, str) or not query.strip():
        return True
    if brand_name and query.strip().casefold() != brand_name.strip().casefold():
        return False
    if not reddit_post_id(str(record.get("url") or "")):
        return False
    return query_match_evidence(
        query.strip(), str(record.get("title") or ""), str(record.get("body") or "")
    ) is not None
