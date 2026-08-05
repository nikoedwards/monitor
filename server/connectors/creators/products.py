"""Product attribution for creator content.

Creator discovery runs at brand scope, then this module maps each discovered
post to zero, one, or many catalog products.  Matches keep their evidence and
confidence so the UI can filter by product without duplicating collected posts.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from urllib.parse import unquote, urlparse

from ...util import clean_text, utc_now


def _normal(value: str | None) -> str:
    return clean_text(unicodedata.normalize("NFKC", value or "")).casefold()


def _has_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in value)


def _usable_signal(value: str, *, allow_short: bool = False) -> bool:
    compact = re.sub(r"\s+", "", value)
    if not compact:
        return False
    if allow_short:
        return len(compact) >= 2
    if _has_cjk(compact):
        return len(compact) >= 2
    return len(compact) >= 3


def _note_aliases(notes: str | None) -> list[str]:
    aliases: list[str] = []
    for part in re.split(r"[\n,，;；|]+", notes or ""):
        value = clean_text(part)
        if value and len(value) <= 80 and _usable_signal(_normal(value)):
            aliases.append(value)
    return aliases[:12]


def _url_signals(url: str | None) -> list[str]:
    value = _normal(url)
    if not value:
        return []
    signals = [value]
    try:
        parsed = urlparse(value)
    except ValueError:
        return signals
    parts = [unquote(part) for part in (parsed.path or "").split("/") if part]
    if parts:
        slug = _normal(parts[-1].replace("-", " ").replace("_", " "))
        if _usable_signal(slug):
            signals.append(slug)
    return signals


def _append_signal(target: list[dict], value: str | None, kind: str, confidence: float) -> None:
    signal = _normal(value)
    if not _usable_signal(signal, allow_short=kind in {"sku", "asin"}):
        return
    if any(item["value"] == signal for item in target):
        return
    target.append({"value": signal, "kind": kind, "confidence": confidence})


def load_product_signals(conn: sqlite3.Connection, brand_id: str) -> list[dict]:
    """Return product rows enriched with strong text/URL/catalog signals."""
    products = [
        {**dict(row), "signals": []}
        for row in conn.execute(
            "SELECT id, brand_id, name, sku, category, notes FROM products WHERE brand_id = ? ORDER BY name",
            (brand_id,),
        ).fetchall()
    ]
    by_id = {item["id"]: item for item in products}
    for product in products:
        _append_signal(product["signals"], product.get("name"), "product_name", 0.96)
        _append_signal(product["signals"], product.get("sku"), "sku", 0.99)
        for alias in _note_aliases(product.get("notes")):
            _append_signal(product["signals"], alias, "alias", 0.90)

    try:
        links = conn.execute(
            "SELECT product_id, url, label FROM links WHERE brand_id = ? AND product_id IS NOT NULL",
            (brand_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        links = []
    for row in links:
        product = by_id.get(row["product_id"])
        if not product:
            continue
        for signal in _url_signals(row["url"]):
            _append_signal(product["signals"], signal, "product_url", 0.98)
        _append_signal(product["signals"], row["label"], "link_label", 0.88)

    try:
        listings = conn.execute(
            "SELECT product_id, asin, sku, title, url FROM sales_listings "
            "WHERE brand_id = ? AND product_id IS NOT NULL",
            (brand_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        listings = []
    for row in listings:
        product = by_id.get(row["product_id"])
        if not product:
            continue
        _append_signal(product["signals"], row["asin"], "asin", 0.99)
        _append_signal(product["signals"], row["sku"], "sku", 0.99)
        _append_signal(product["signals"], row["title"], "listing_title", 0.91)
        for signal in _url_signals(row["url"]):
            _append_signal(product["signals"], signal, "listing_url", 0.98)
    return products


def creator_product_queries(conn: sqlite3.Connection, brand_id: str, *, limit: int = 8) -> list[str]:
    """Small, quota-aware query expansion used by creator discovery."""
    brand_row = conn.execute(
        "SELECT name FROM brands WHERE id = ?",
        (brand_id,),
    ).fetchone()
    brand_name = clean_text(brand_row["name"] if brand_row else "")

    product_candidates: list[list[str]] = []
    for product in load_product_signals(conn, brand_id):
        candidates = [product.get("name"), product.get("sku"), *_note_aliases(product.get("notes"))]
        product_candidates.append([clean_text(candidate) for candidate in candidates if clean_text(candidate)])

    queries: list[str] = []
    seen: set[str] = set()
    # Round-robin keeps a large alias list for the first product from consuming
    # the entire YouTube query budget before the rest of the catalog is covered.
    depth = 0
    while any(depth < len(candidates) for candidates in product_candidates):
        for candidates in product_candidates:
            if depth >= len(candidates):
                continue
            value = candidates[depth]
            key = _normal(value)
            if not key or key in seen or not _usable_signal(key):
                continue
            seen.add(key)
            query = value if not brand_name or _normal(brand_name) in key else f"{brand_name} {value}"
            queries.append(query)
            if len(queries) >= max(1, limit):
                return queries
        depth += 1
    return queries


def _contains_signal(haystack: str, signal: str) -> bool:
    if not signal:
        return False
    if _has_cjk(signal) or "://" in signal or "/" in signal:
        return signal in haystack
    return bool(re.search(rf"(?<!\w){re.escape(signal)}(?!\w)", haystack))


def match_record_to_products(record: dict, products: list[dict]) -> list[dict]:
    """Return every product match for one normalized creator record."""
    text = _normal(
        " ".join(
            str(record.get(key) or "")
            for key in ("title", "body", "url", "author")
        )
    )
    raw = record.get("raw")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            raw = {}
    if isinstance(raw, dict):
        # Search queries are discovery metadata, not proof that the returned
        # content actually mentions a product. Keep provider-derived content
        # fields available while excluding query bookkeeping from attribution.
        raw = {
            key: value
            for key, value in raw.items()
            if key not in {"query", "matched_queries", "search_query", "keywords"}
        }
    if raw:
        text = f"{text} {_normal(json.dumps(raw, ensure_ascii=False))}"

    matches: list[dict] = []
    for product in products:
        evidence = [
            {"signal": signal["value"], "kind": signal["kind"]}
            for signal in product.get("signals", [])
            if _contains_signal(text, signal["value"])
        ]
        if not evidence:
            continue
        strongest = max(
            (signal for signal in product["signals"] if any(item["signal"] == signal["value"] for item in evidence)),
            key=lambda signal: signal["confidence"],
        )
        matches.append({
            "product_id": product["id"],
            "product_name": product["name"],
            "confidence": strongest["confidence"],
            "match_type": strongest["kind"],
            "evidence": evidence[:8],
        })
    return sorted(matches, key=lambda item: (-item["confidence"], item["product_name"]))


def rebuild_product_matches(conn: sqlite3.Connection, brand_id: str) -> dict:
    """Rebuild creator-post product attribution for one brand, idempotently."""
    if not brand_id:
        return {"records": 0, "matched_records": 0, "matches": 0}
    products = load_product_signals(conn, brand_id)
    product_ids = {product["id"] for product in products}
    rows = conn.execute(
        "SELECT * FROM records WHERE brand_id = ? AND channel = 'creators'",
        (brand_id,),
    ).fetchall()
    conn.execute("DELETE FROM record_product_matches WHERE brand_id = ?", (brand_id,))

    now = utc_now()
    matched_records = 0
    match_count = 0
    for row in rows:
        record = dict(row)
        try:
            record["raw"] = json.loads(record.get("raw_json") or "{}")
        except (TypeError, ValueError):
            record["raw"] = {}
        matches = match_record_to_products(record, products)

        # Existing product_id values are treated as explicit/manual attribution.
        manual_product_id = record.get("product_id")
        if manual_product_id in product_ids and not any(item["product_id"] == manual_product_id for item in matches):
            product = next(item for item in products if item["id"] == manual_product_id)
            matches.insert(0, {
                "product_id": manual_product_id,
                "product_name": product["name"],
                "confidence": 1.0,
                "match_type": "manual",
                "evidence": [{"kind": "manual", "signal": manual_product_id}],
            })

        if matches:
            matched_records += 1
        for match in matches:
            conn.execute(
                """
                INSERT INTO record_product_matches
                    (record_id, product_id, brand_id, confidence, match_type, evidence_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"], match["product_id"], brand_id, match["confidence"],
                    match["match_type"], json.dumps(match["evidence"], ensure_ascii=False), now, now,
                ),
            )
            match_count += 1
    return {"records": len(rows), "matched_records": matched_records, "matches": match_count}
