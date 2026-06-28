"""Brand, product, and link (catalog) endpoints + brand URL analysis."""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from .. import ai
from ..fetchers import FetchError, fetch_page
from ..schemas import AiDraftIn, BrandAnalyzeIn, BrandFromDraftIn, BrandIn, LinkIn, LinkUpdate, ProductIn
from ..util import (
    canonical_url,
    clean_external_link,
    clean_text,
    detect_social_platform,
    host_key,
    is_amazon_url,
    is_likely_account_link,
    new_id,
    normalize_url,
    utc_now,
)
from .common import fetch_brand, get_conn

router = APIRouter(prefix="/api", tags=["brands"])


def brand_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    for key in ("social_links", "ecommerce_links", "monitoring_keywords"):
        item[key] = json.loads(item.pop(f"{key}_json") or ("[]" if key == "monitoring_keywords" else "{}"))
    item.pop("raw_json", None)
    item["is_competitor"] = bool(item.get("is_competitor"))
    item["is_primary"] = bool(item.get("is_primary"))
    return item


@router.get("/brands")
def list_brands(conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM brands ORDER BY is_primary DESC, name").fetchall()
    return {"brands": [brand_to_dict(r) for r in rows]}


@router.post("/brands", status_code=201)
def create_brand(payload: BrandIn, conn: sqlite3.Connection = Depends(get_conn)):
    now = utc_now()
    brand_id = new_id()
    conn.execute(
        """
        INSERT INTO brands (id, name, slug, is_competitor, is_primary, official_website,
            amazon_url, category, description, logo_url, social_links_json,
            ecommerce_links_json, monitoring_keywords_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            brand_id, payload.name.strip(), payload.name.strip().lower().replace(" ", "-"),
            int(payload.is_competitor), int(payload.is_primary), payload.official_website,
            payload.amazon_url, payload.category, payload.description, payload.logo_url,
            json.dumps(payload.social_links, ensure_ascii=False),
            json.dumps(payload.ecommerce_links, ensure_ascii=False),
            json.dumps(payload.monitoring_keywords, ensure_ascii=False), now, now,
        ),
    )
    return brand_to_dict(conn.execute("SELECT * FROM brands WHERE id = ?", (brand_id,)).fetchone())


@router.put("/brands/{brand_id}")
def update_brand(brand_id: str, payload: BrandIn, conn: sqlite3.Connection = Depends(get_conn)):
    fetch_brand(conn, brand_id)
    conn.execute(
        """
        UPDATE brands SET name = ?, is_competitor = ?, is_primary = ?, official_website = ?,
            amazon_url = ?, category = ?, description = ?, logo_url = ?, social_links_json = ?,
            ecommerce_links_json = ?, monitoring_keywords_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            payload.name.strip(), int(payload.is_competitor), int(payload.is_primary),
            payload.official_website, payload.amazon_url, payload.category, payload.description,
            payload.logo_url, json.dumps(payload.social_links, ensure_ascii=False),
            json.dumps(payload.ecommerce_links, ensure_ascii=False),
            json.dumps(payload.monitoring_keywords, ensure_ascii=False), utc_now(), brand_id,
        ),
    )
    return brand_to_dict(conn.execute("SELECT * FROM brands WHERE id = ?", (brand_id,)).fetchone())


@router.delete("/brands/{brand_id}")
def delete_brand(brand_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    fetch_brand(conn, brand_id)
    conn.execute("DELETE FROM brands WHERE id = ?", (brand_id,))
    conn.execute("DELETE FROM products WHERE brand_id = ?", (brand_id,))
    conn.execute("DELETE FROM links WHERE brand_id = ?", (brand_id,))
    return {"deleted": brand_id}


@router.post("/brands/analyze")
def analyze_brand(payload: BrandAnalyzeIn):
    url = normalize_url(payload.url)
    try:
        page = fetch_page(url)
    except FetchError as exc:
        return {"error": str(exc), "url": url}
    social_links: dict[str, str] = {}
    ecommerce_links: dict[str, str] = {}
    for href in page.get("anchors", []):
        link = clean_external_link(page.get("final_url") or url, href)
        if not link:
            continue
        if is_amazon_url(link):
            ecommerce_links.setdefault("amazon", link)
            continue
        platform = detect_social_platform(link)
        if platform and is_likely_account_link(platform, link):
            social_links.setdefault(platform, link)
    meta = page.get("meta", {})
    return {
        "url": url,
        "final_url": page.get("final_url"),
        "name": clean_text(meta.get("og:site_name") or page.get("title")),
        "description": clean_text(meta.get("description") or meta.get("og:description")),
        "logo_url": meta.get("og:image") or page.get("icon_url"),
        "official_website": page.get("final_url"),
        "social_links": social_links,
        "ecommerce_links": ecommerce_links,
    }


# ------------------------------------------------------------------- AI draft
@router.post("/brands/ai-draft")
def ai_draft(payload: AiDraftIn, conn: sqlite3.Connection = Depends(get_conn)):
    if not ai.is_configured(conn):
        raise HTTPException(status_code=400, detail="尚未配置大模型 Token，请先在设置中填写。")
    try:
        return {"draft": ai.draft_brand(conn, payload.keyword)}
    except ai.LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/brands/from-draft", status_code=201)
def create_from_draft(payload: BrandFromDraftIn, conn: sqlite3.Connection = Depends(get_conn)):
    now = utc_now()
    brand_id = new_id()
    conn.execute(
        """
        INSERT INTO brands (id, name, slug, is_competitor, is_primary, official_website,
            category, description, logo_url, social_links_json, ecommerce_links_json,
            monitoring_keywords_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            brand_id, payload.name.strip(), payload.name.strip().lower().replace(" ", "-"),
            int(payload.is_competitor), int(payload.is_primary), payload.official_website,
            payload.category, payload.description, payload.logo_url,
            json.dumps(payload.social_links, ensure_ascii=False),
            json.dumps(payload.ecommerce_links, ensure_ascii=False),
            json.dumps(payload.monitoring_keywords, ensure_ascii=False), now, now,
        ),
    )
    for product in payload.products:
        if not product.name.strip():
            continue
        conn.execute(
            "INSERT INTO products (id, brand_id, name, sku, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id(), brand_id, product.name.strip(), product.sku, product.category, now, now),
        )
    for link in payload.links:
        if not (link.url or "").strip():
            continue
        conn.execute(
            """
            INSERT INTO links (id, brand_id, dimension, channel, platform, url, canonical_url,
                label, cadence, status, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'daily', 'active', '{}', ?, ?)
            """,
            (
                new_id(), brand_id, link.dimension, link.channel, link.platform,
                link.url, canonical_url(link.url), link.label, now, now,
            ),
        )
    return brand_to_dict(conn.execute("SELECT * FROM brands WHERE id = ?", (brand_id,)).fetchone())


# --------------------------------------------------------------------- products
def product_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item.pop("raw_json", None)
    return item


@router.get("/brands/{brand_id}/products")
def list_products(brand_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM products WHERE brand_id = ? ORDER BY name", (brand_id,)).fetchall()
    return {"products": [product_to_dict(r) for r in rows]}


@router.post("/products", status_code=201)
def create_product(payload: ProductIn, conn: sqlite3.Connection = Depends(get_conn)):
    fetch_brand(conn, payload.brand_id)
    now = utc_now()
    product_id = new_id()
    conn.execute(
        "INSERT INTO products (id, brand_id, name, sku, category, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (product_id, payload.brand_id, payload.name.strip(), payload.sku, payload.category, payload.notes, now, now),
    )
    return product_to_dict(conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone())


@router.delete("/products/{product_id}")
def delete_product(product_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    return {"deleted": product_id}


# ------------------------------------------------------------------------ links
def link_to_dict(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["config"] = json.loads(item.pop("config_json") or "{}")
    return item


@router.get("/links")
def list_links(
    brand_id: str | None = None,
    dimension: str | None = None,
    channel: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
):
    clauses, params = [], []
    for key, value in (("brand_id", brand_id), ("dimension", dimension), ("channel", channel)):
        if value:
            clauses.append(f"{key} = ?")
            params.append(value)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(f"SELECT * FROM links{where} ORDER BY created_at DESC", params).fetchall()
    return {"links": [link_to_dict(r) for r in rows]}


@router.post("/links", status_code=201)
def create_link(payload: LinkIn, conn: sqlite3.Connection = Depends(get_conn)):
    fetch_brand(conn, payload.brand_id)
    now = utc_now()
    link_id = new_id()
    url = payload.url or ""
    conn.execute(
        """
        INSERT INTO links (id, brand_id, product_id, dimension, channel, platform, url,
            canonical_url, label, region, source_id, cadence, status, config_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            link_id, payload.brand_id, payload.product_id, payload.dimension, payload.channel,
            payload.platform, url, canonical_url(url), payload.label, payload.region,
            payload.source_id, payload.cadence, payload.status,
            json.dumps(payload.config, ensure_ascii=False), now, now,
        ),
    )
    return link_to_dict(conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone())


@router.put("/links/{link_id}")
def update_link(link_id: str, payload: LinkUpdate, conn: sqlite3.Connection = Depends(get_conn)):
    existing = conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Link not found")
    data = {**dict(existing), **payload.model_dump(exclude_none=True), "updated_at": utc_now()}
    data["canonical_url"] = canonical_url(data.get("url") or "")
    conn.execute(
        "UPDATE links SET url = ?, canonical_url = ?, label = ?, status = ?, platform = ?, updated_at = ? WHERE id = ?",
        (data["url"], data["canonical_url"], data["label"], data["status"], data["platform"], data["updated_at"], link_id),
    )
    return link_to_dict(conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone())


@router.delete("/links/{link_id}")
def delete_link(link_id: str, conn: sqlite3.Connection = Depends(get_conn)):
    conn.execute("DELETE FROM links WHERE id = ?", (link_id,))
    return {"deleted": link_id}
