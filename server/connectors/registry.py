"""Connector registry: the catalog of all data sources + db sync."""
from __future__ import annotations

import sqlite3

from ..util import utc_now
from .base import ConnectorSpec
from . import collectors
from . import social
from .creators import runner as creator_runner

REGISTRY: list[ConnectorSpec] = [
    # ---- Manual ----
    ConnectorSpec(
        id="manual_csv", name="手动 / CSV 录入", category="manual", dimension="voc",
        tier=1, vendor="Internal", sync_mode="manual",
        notes="用户之声手动录入与 CSV 批量导入。",
    ),
    ConnectorSpec(
        id="offline_sales", name="线下销售录入", category="sales", dimension="sales",
        tier=1, vendor="Internal", sync_mode="manual",
        notes="线下销售数据人工录入到销售时序表。",
    ),
    # ---- Tier 1: real, no credential ----
    ConnectorSpec(
        id="google_news", name="Google News", category="media", dimension="marketing",
        tier=1, vendor="Google", sync_mode="scheduled", cadence="hourly",
        notes="按品牌关键词抓取 Google News RSS 媒体报道。",
        collect=collectors.collect_google_news,
    ),
    ConnectorSpec(
        id="reddit_search", name="Reddit 社群", category="community", dimension="marketing",
        tier=1, vendor="Reddit", sync_mode="scheduled", cadence="daily",
        notes="品牌主名称全站搜索 + 配置的官方 subreddit 全量内容流(JSON 端点,可选 REDDIT_BEARER_TOKEN,回退 RSS)。",
        collect=collectors.collect_reddit,
    ),
    ConnectorSpec(
        id="community_site", name="自建社群 / 论坛", category="community", dimension="marketing",
        tier=1, vendor="Web", sync_mode="scheduled", cadence="daily",
        notes="抓取品牌配置的自建社群/论坛链接:Discourse JSON API → RSS 发现 → 通用页面爬取。",
        collect=collectors.collect_community_sites,
    ),
    ConnectorSpec(
        id="social_accounts", name="官方社媒账号", category="social", dimension="marketing",
        tier=1, vendor="Platform API / Public Feed", sync_mode="scheduled", cadence="daily",
        notes="按品牌配置的社媒账号链接采集公开内容。YouTube 使用 Atom feed/网页指标；Instagram 使用公开网页接口；TikTok 使用公开页面提取；X 等平台会明确标记暂未支持。",
        collect=social.collect_social_accounts,
    ),
    ConnectorSpec(
        id="app_store_reviews", name="App Store 评分与评论", category="voc", dimension="voc",
        tier=1, vendor="Apple", sync_mode="scheduled", cadence="daily",
        notes="按主要国家市场抓取 App Store 公开累计评分、平均评分与最新评论；未配置链接时自动发现官方 App（无需 API key）。",
        collect=collectors.collect_app_store,
    ),
    ConnectorSpec(
        id="brand_site", name="品牌站点分析", category="web", dimension="platform",
        tier=1, vendor="Web", sync_mode="manual",
        notes="抓取官网元数据 / JSON-LD 建立品牌档案与链接发现。",
    ),
    ConnectorSpec(
        id="web_snapshot", name="网页快照监控", category="web", dimension="web",
        tier=1, vendor="Web", sync_mode="scheduled", cadence="daily",
        notes="Playwright/浏览器截图 + 变更分析,支持子页面发现与回溯。",
    ),
    # ---- Tier 2: needs credential / public fallback ----
    ConnectorSpec(
        id="meta_ads", name="Meta 广告库", category="ads", dimension="marketing",
        tier=1, vendor="Meta", sync_mode="scheduled", cadence="daily",
        notes="通过 Meta Ad Library 公开页面抓取广告;无需 Token。若配置 access token 则优先使用官方 API。",
        collect=collectors.collect_meta_ads,
    ),
    ConnectorSpec(
        id="youtube_search", name="YouTube 红人", category="creators", dimension="marketing",
        tier=1, vendor="YouTube / yt-dlp", sync_mode="scheduled", cadence="daily",
        notes="优先使用 YouTube Data API；未配置 API key 时使用公开搜索，按品牌关键词抓取近期视频。",
        collect=creator_runner.collect_youtube,
    ),
    ConnectorSpec(
        id="instagram_listening", name="Instagram 红人关键词发现", category="creators", dimension="marketing",
        tier=1, vendor="Bing / Public Web", sync_mode="scheduled", cadence="daily",
        notes="通过 Bing 公开搜索发现 Instagram 公开达人主页，再采集公开内容；不需要 API key。",
        collect=creator_runner.collect_instagram,
    ),
    ConnectorSpec(
        id="tiktok_listening", name="TikTok 红人关键词发现", category="creators", dimension="marketing",
        tier=1, vendor="Bing / yt-dlp", sync_mode="scheduled", cadence="daily",
        notes="通过 Bing 公开搜索发现 TikTok 公开达人主页，再用 yt-dlp 采集公开内容；不需要 API key。",
        collect=creator_runner.collect_tiktok,
    ),
    ConnectorSpec(
        id="x_search", name="X 红人关键词发现", category="creators", dimension="marketing",
        tier=3, vendor="Public Web", sync_mode="manual", cadence="daily",
        notes="X 对未登录访问和自动化采集限制严格；暂无稳定免费关键词发现接口。",
    ),
    ConnectorSpec(
        id="discord_community", name="Discord 社群", category="community", dimension="marketing",
        tier=2, vendor="Discord", sync_mode="scheduled", cadence="daily",
        credential_key="discord_bot_token",
        notes="占位(阶段二待讨论)。自家服务器:bot token + REST /channels/{id}/messages(需 MESSAGE_CONTENT 特权 intent)读消息;"
              "无法入群的竞品:仅能用 invite ?with_counts / widget.json 取成员-在线规模指标;user-token 抓取违反 ToS,不采用。",
    ),
    ConnectorSpec(
        id="facebook_groups", name="Facebook 群组", category="community", dimension="marketing",
        tier=2, vendor="Meta", sync_mode="scheduled", cadence="daily",
        credential_key="facebook_access_token",
        notes="占位(阶段二待讨论)。Graph 群组接口大多已废弃/锁权限;公开 Page(非 Group)可走 Graph Page API;"
              "登录态无头浏览器(Cookie + Playwright)脆弱且 ToS 灰色,仅作高级可选;兜底人工/CSV 录入。",
    ),
    # ---- Tier 3: paid / not freely available, seam only ----
    ConnectorSpec(
        id="amazon_sales", name="Amazon 销量监控", category="sales", dimension="sales",
        tier=1, vendor="Scrape / SellerSprite", sync_mode="scheduled", cadence="daily",
        notes="在「品牌管理」配置 Amazon 店铺/单品链接即自动展开 Listing 并每日采集(销售监控页)。"
              "默认尽力爬取;配置卖家精灵 secret-key 后优先使用其 OpenAPI 获取销量/排名。",
    ),
]

BY_ID: dict[str, ConnectorSpec] = {spec.id: spec for spec in REGISTRY}


def get_spec(source_id: str) -> ConnectorSpec | None:
    return BY_ID.get(source_id)


def _source_ids_referenced_by_foreign_keys(conn: sqlite3.Connection) -> set[str]:
    """Return source ids still needed by rows in this database.

    Older databases declared ``records.source_id`` as a foreign key while the
    current schema is intentionally additive and does not recreate that table.
    A connector can therefore disappear from the registry while its historical
    records still require the corresponding ``sources`` row to exist.  Inspect
    the schema instead of hard-coding one legacy table so future source-linked
    tables are handled the same way.
    """
    referenced: set[str] = set()
    tables = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()

    def value(row, key: str, index: int):
        try:
            return row[key]
        except (IndexError, KeyError, TypeError):
            return row[index]

    def quote_identifier(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    for table_row in tables:
        table = value(table_row, "name", 0)
        for foreign_key in conn.execute(
            f"PRAGMA foreign_key_list({quote_identifier(table)})"
        ).fetchall():
            parent_table = value(foreign_key, "table", 2)
            if parent_table != "sources":
                continue
            child_column = value(foreign_key, "from", 3)
            rows = conn.execute(
                f"SELECT {quote_identifier(child_column)} "
                f"FROM {quote_identifier(table)} "
                f"WHERE {quote_identifier(child_column)} IS NOT NULL"
            ).fetchall()
            referenced.update(str(value(row, 0, 0)) for row in rows)
    # ``source_brand_runs`` predates the foreign-key declaration and may still
    # contain useful per-brand runtime state for a retired connector.
    try:
        rows = conn.execute(
            "SELECT source_id FROM source_brand_runs WHERE source_id IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = ()
    referenced.update(str(value(row, 0, 0)) for row in rows)
    return referenced


def sync_to_db(conn: sqlite3.Connection) -> None:
    """Upsert connector metadata into the sources table (preserve runtime stats)."""
    now = utc_now()
    keep = tuple(BY_ID.keys())
    placeholders = ", ".join("?" for _ in keep)
    # Keep retired connector rows when historical data still points at them.
    # Deleting those rows from a legacy DB with FK enforcement enabled makes
    # application startup fail before the API can serve any request.
    referenced = _source_ids_referenced_by_foreign_keys(conn)
    stale_rows = conn.execute(
        f"SELECT id FROM sources WHERE id NOT IN ({placeholders})", keep
    ).fetchall()
    for row in stale_rows:
        source_id = row[0]
        if source_id in referenced:
            continue
        try:
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        except sqlite3.IntegrityError as exc:
            # A legacy trigger or FK not visible through sqlite_master should
            # not prevent the registry from syncing. Leave that source row in
            # place and let its historical data continue to resolve.
            if "foreign key" not in str(exc).lower():
                raise
    for spec in REGISTRY:
        existing = conn.execute("SELECT id FROM sources WHERE id = ?", (spec.id,)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE sources SET name = ?, category = ?, tier = ?, vendor = ?,
                    sync_mode = ?, status = ?, needs_credentials = ?, credential_key = ?,
                    cadence = ?, notes = ?
                WHERE id = ?
                """,
                (
                    spec.name, spec.category, spec.tier, spec.vendor, spec.sync_mode,
                    spec.status, int(spec.needs_credentials), spec.credential_key,
                    spec.cadence, spec.notes, spec.id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO sources (id, name, category, tier, vendor, sync_mode, status,
                    needs_credentials, credential_key, cadence, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.id, spec.name, spec.category, spec.tier, spec.vendor, spec.sync_mode,
                    spec.status, int(spec.needs_credentials), spec.credential_key,
                    spec.cadence, spec.notes, now,
                ),
            )
