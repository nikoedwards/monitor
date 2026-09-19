"""Pure helpers explaining why creator content entered the monitoring stream.

Only public title/description/metadata and, when available, public captions are
used. The resulting evidence never claims that the complete video was about a
brand or that visual/audio frames were inspected.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Iterable

from ...relevance import query_match_evidence
from ...util import clean_text

ANALYSIS_SCOPE = "title_description_metadata"
ANALYSIS_NOTE = (
    "仅检查视频标题、描述和公开元数据；未分析视频画面、音频或完整字幕，"
    "因此无法判断品牌出现的具体视频时间段或是否覆盖整段视频。"
)
TRANSCRIPT_ANALYSIS_NOTE = (
    "匹配到公开字幕/自动字幕中的关键词，并保留字幕时间段；字幕可能不完整或由平台自动生成，"
    "因此仍不能证明品牌覆盖整段视频。"
)
_TIMING = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?(?:[\.,]\d{1,3})?)\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?(?:[\.,]\d{1,3})?)"
)
_TAG = re.compile(r"<[^>]+>")


def _seconds(value: str) -> float | None:
    parts = value.replace(",", ".").split(":")
    try:
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except (TypeError, ValueError):
        return None
    return None


def parse_transcript_cues(text: str, *, source: str = "automatic_caption", limit: int = 500) -> list[dict[str, Any]]:
    """Parse WebVTT/SRT-like text into bounded timestamped cues."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[dict[str, Any]] = []
    index = 0
    while index < len(lines) and len(cues) < max(1, limit):
        timing = _TIMING.search(lines[index])
        if not timing:
            index += 1
            continue
        start, end = _seconds(timing.group("start")), _seconds(timing.group("end"))
        index += 1
        parts: list[str] = []
        while index < len(lines) and lines[index].strip() and not _TIMING.search(lines[index]):
            line = lines[index].strip()
            if not line.startswith(("NOTE", "STYLE", "REGION")):
                parts.append(line)
            index += 1
        cue_text = clean_text(html.unescape(_TAG.sub("", " ".join(parts))))
        if start is not None and end is not None and end >= start and cue_text:
            cues.append({"start": round(start, 3), "end": round(end, 3), "text": cue_text[:500], "source": source})
    return cues


def parse_transcript_payload(text: str, *, source: str = "automatic_caption", limit: int = 500) -> list[dict[str, Any]]:
    """Parse VTT/SRT or yt-dlp JSON3 captions."""
    value = str(text or "")
    if value.lstrip().startswith("{"):
        try:
            payload = json.loads(value)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("events"), list):
            cues: list[dict[str, Any]] = []
            for event in payload["events"][: max(1, limit)]:
                if not isinstance(event, dict):
                    continue
                try:
                    start = float(event.get("tStartMs", 0)) / 1000
                    end = start + float(event.get("dDurationMs", 0)) / 1000
                except (TypeError, ValueError):
                    continue
                text_value = clean_text("".join(
                    str(seg.get("utf8") or "") for seg in (event.get("segs") or []) if isinstance(seg, dict)
                ))
                if text_value and end >= start:
                    cues.append({"start": round(start, 3), "end": round(end, 3), "text": text_value[:500], "source": source})
            return cues
    return parse_transcript_cues(value, source=source, limit=limit)


def transcript_matches(cues: Iterable[dict], terms: Iterable[str], *, limit: int = 12) -> list[dict[str, Any]]:
    candidates: list[str] = []
    seen: set[str] = set()
    for term in sorted((clean_text(item) for item in terms), key=len, reverse=True):
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            candidates.append(term)
    matches: list[dict[str, Any]] = []
    for cue in cues or []:
        if not isinstance(cue, dict):
            continue
        text = clean_text(cue.get("text"))
        if not text:
            continue
        for term in candidates:
            match = query_match_evidence(term, "", text)
            if not match:
                continue
            try:
                start, end = float(cue.get("start")), float(cue.get("end"))
            except (TypeError, ValueError):
                continue
            matches.append({
                "start": round(start, 3), "end": round(end, 3), "text": text[:500],
                "source": clean_text(cue.get("source")) or "automatic_caption",
                "matched_query": term, "matched_text": match.get("matched_text") or term,
            })
            break
        if len(matches) >= max(1, limit):
            break
    return matches


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except (TypeError, ValueError):
            value = []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [clean_text(item) for item in value if clean_text(item)]


def _brand_terms(brand: dict | None, signals: dict | None = None) -> list[str]:
    values: list[str] = []
    if isinstance(brand, dict):
        name = clean_text(brand.get("name"))
        if name:
            values.append(name)
        values.extend(_list(brand.get("monitoring_keywords")))
        values.extend(_list(brand.get("monitoring_keywords_json")))
    if isinstance(signals, dict):
        values.extend(_list(signals.get("names")))
        values.extend(f"@{item}" for item in _list(signals.get("handles")))
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if len(value) >= 2 and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _field_matches(terms: Iterable[str], title: str, body: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for term in terms:
        match = query_match_evidence(term, title, body)
        if not match:
            continue
        key = (term.casefold(), match.get("matched_in", ""), match.get("matched_text", ""))
        if key not in seen:
            seen.add(key)
            result.append({"query": term, **match})
    return result


def _products(product_matches: Iterable[dict] | None, title: str, body: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for match in product_matches or []:
        if not isinstance(match, dict):
            continue
        terms = [clean_text(match.get("product_name"))]
        terms.extend(clean_text(item.get("signal")) for item in (match.get("evidence") or []) if isinstance(item, dict))
        located = _field_matches((term for term in terms if term), title, body)
        result.append({
            "product_id": clean_text(match.get("product_id")),
            "product_name": clean_text(match.get("product_name")),
            "match_type": clean_text(match.get("match_type")),
            "confidence": match.get("confidence"),
            "evidence": located[:8],
            "matched_in": located[0]["matched_in"] if located else "metadata",
            "matched_text": located[0]["matched_text"] if located else "",
        })
    return result


def _queries(raw: dict) -> list[str]:
    values = _list(raw.get("matched_queries"))
    for key in ("query", "discovery_query"):
        value = clean_text(raw.get(key))
        if value:
            values.append(value)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
    return result


def build_collection_evidence(*, title: str = "", body: str = "", brand: dict | None = None,
                              raw: dict | None = None, signals: dict | None = None,
                              product_matches: Iterable[dict] | None = None,
                              transcript_matches_data: Iterable[dict] | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    title_text = clean_text(title)
    description = raw.get("description") if "description" in raw else body
    body_text = clean_text(description) if description else ""
    brands = _field_matches(_brand_terms(brand, signals), title_text, body_text)
    products = _products(product_matches, title_text, body_text)
    if transcript_matches_data is None:
        transcript_matches_data = raw.get("transcript_matches")
    transcript_rows = [
        {"start": item.get("start"), "end": item.get("end"), "text": clean_text(item.get("text"))[:500],
         "source": clean_text(item.get("source")) or "automatic_caption",
         "matched_query": clean_text(item.get("matched_query")), "matched_text": clean_text(item.get("matched_text"))}
        for item in (transcript_matches_data or []) if isinstance(item, dict) and clean_text(item.get("text"))
    ][:24]
    visible = brands + [item for product in products for item in product.get("evidence", [])]
    fields = {item.get("matched_in") for item in visible}
    if transcript_rows and fields:
        scope, matched_in = "transcript_and_metadata", "transcript"
    elif transcript_rows:
        scope, matched_in = "transcript", "transcript"
    elif "title" in fields and "body" in fields:
        scope, matched_in = "title_and_description", "title_and_description"
    elif "title" in fields:
        scope, matched_in = "title", "title"
    elif "body" in fields:
        scope, matched_in = "description", "body"
    elif products:
        scope, matched_in = "metadata", "metadata"
    else:
        scope, matched_in = "unknown", ""
    has_brand, has_product = bool(brands), bool(products)
    brand_terms = {item.casefold() for item in _brand_terms(brand, signals)}
    product_terms = {clean_text(item.get("product_name")).casefold() for item in products if clean_text(item.get("product_name"))}
    transcript_brand = any(clean_text(item.get("matched_query")).casefold() in brand_terms for item in transcript_rows)
    transcript_product = any(clean_text(item.get("matched_query")).casefold() in product_terms for item in transcript_rows)
    if transcript_rows and transcript_brand and transcript_product:
        evidence_type = "transcript_brand_and_product_keyword"
    elif transcript_rows and transcript_brand:
        evidence_type = "transcript_brand_keyword"
    elif transcript_rows and transcript_product:
        evidence_type = "transcript_product_keyword"
    elif has_brand and has_product:
        evidence_type = "brand_and_product_keyword"
    elif has_brand:
        evidence_type = "brand_keyword"
    elif has_product:
        evidence_type = "product_keyword"
    else:
        evidence_type = "search_result_metadata"
    matched_texts: list[str] = []
    for value in sorted((clean_text(item.get("matched_text")) for item in visible), key=len, reverse=True):
        if value and not any(value.casefold() in old.casefold() or old.casefold() in value.casefold() for old in matched_texts):
            matched_texts.append(value)
    for item in transcript_rows:
        value = clean_text(item.get("matched_text"))
        if value and not any(value.casefold() in old.casefold() or old.casefold() in value.casefold() for old in matched_texts):
            matched_texts.append(value)
    transcript_status = clean_text(raw.get("transcript_status")) or "not_checked"
    queries = _queries(raw)
    if transcript_rows:
        ranges = ", ".join(f"{int(float(item['start']) // 60):02d}:{float(item['start']) % 60:05.2f}-{int(float(item['end']) // 60):02d}:{float(item['end']) % 60:05.2f}" for item in transcript_rows[:4] if item.get("start") is not None and item.get("end") is not None)
        reason = f"公开字幕在{ranges or '若干时间段'}命中关键词「{'、'.join(matched_texts[:4])}」；{TRANSCRIPT_ANALYSIS_NOTE}"
        note = TRANSCRIPT_ANALYSIS_NOTE
    elif matched_texts:
        label = "标题和描述" if scope == "title_and_description" else "标题" if scope == "title" else "描述" if scope == "description" else "公开元数据"
        extra = "未找到可用公开字幕，无法定位品牌在视频中的时间段；" if transcript_status == "unavailable" else "已检查可用公开字幕但未命中关键词；" if transcript_status == "available_no_match" else ""
        reason = f"可见{label}命中关键词「{'、'.join(matched_texts[:4])}」；{extra}{ANALYSIS_NOTE}"
        note = "未获取可定位的字幕时间段；" + ANALYSIS_NOTE
    elif queries:
        reason, note = f"搜索结果由关键词「{'、'.join(queries[:4])}」发现；{ANALYSIS_NOTE}", ANALYSIS_NOTE
    else:
        reason, note = f"搜索结果通过采集规则收录；{ANALYSIS_NOTE}", ANALYSIS_NOTE
    return {
        "evidence_type": evidence_type, "scope": scope, "matched_in": matched_in,
        "matched_text": matched_texts[0] if matched_texts else "", "matched_texts": matched_texts,
        "matched_queries": queries, "brand_matches": brands, "product_matches": products,
        "analysis_scope": "title_description_metadata_transcript" if transcript_rows else ANALYSIS_SCOPE,
        "analysis_note": note, "transcript_status": transcript_status,
        "transcript_source": clean_text(raw.get("transcript_source")), "transcript_matches": transcript_rows,
        "reason": reason,
    }


def enrich_creator_record_evidence(record: dict, *, brand: dict | None = None,
                                   signals: dict | None = None,
                                   product_matches: Iterable[dict] | None = None) -> dict:
    raw = record.get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    evidence = build_collection_evidence(title=record.get("title") or "", body=record.get("body") or "",
                                         brand=brand, signals=signals, raw=raw, product_matches=product_matches)
    updated = dict(record)
    updated["raw"] = {**raw, "evidence_type": evidence["evidence_type"], "scope": evidence["scope"],
                       "matched_in": evidence["matched_in"], "matched_text": evidence["matched_text"],
                       "matched_queries": evidence["matched_queries"], "collection_evidence": evidence}
    return updated
