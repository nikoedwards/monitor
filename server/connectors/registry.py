"""Connector registry: the catalog of all data sources + db sync."""
from __future__ import annotations

import sqlite3

from ..util import utc_now
from .base import ConnectorSpec
from . import collectors
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
        id="reddit_search", name="Reddit 搜索", category="community", dimension="marketing",
        tier=1, vendor="Reddit", sync_mode="scheduled", cadence="daily",
        notes="按品牌关键词与配置的 subreddit 抓取 Reddit(JSON 端点,可选 REDDIT_BEARER_TOKEN,回退 RSS)。",
        collect=collectors.collect_reddit,
    ),
    ConnectorSpec(
        id="community_site", name="自建社群 / 论坛", category="community", dimension="marketing",
        tier=1, vendor="Web", sync_mode="scheduled", cadence="daily",
        notes="抓取品牌配置的自建社群/论坛链接:Discourse JSON API → RSS 发现 → 通用页面爬取。",
        collect=collectors.collect_community_sites,
    ),
    ConnectorSpec(
        id="app_store_reviews", name="App Store 评论", category="voc", dimension="voc",
        tier=1, vendor="Apple", sync_mode="scheduled", cadence="daily",
        notes="抓取已配置 App Store 链接的客户评论(无需 API key)。",
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
    # ---- Tier 2: needs credential ----
    ConnectorSpec(
        id="meta_ads", name="Meta 广告库", category="ads", dimension="marketing",
        tier=2, vendor="Meta", sync_mode="scheduled", cadence="daily",
        credential_key="facebook_access_token",
        notes="Meta Ad Library API 抓竞品在投广告;需 Meta access token。",
        collect=collectors.collect_meta_ads,
    ),
    ConnectorSpec(
        id="youtube_search", name="YouTube 红人", category="creators", dimension="marketing",
        tier=2, vendor="Google", sync_mode="scheduled", cadence="daily",
        credential_key="youtube_api_key",
        notes="YouTube Data API 搜索品牌相关视频,补充播放/点赞/评论与订阅数,并做合作识别;需 API key。",
        collect=creator_runner.collect_youtube,
    ),
    ConnectorSpec(
        id="instagram_listening", name="Instagram 红人(第三方)", category="creators", dimension="marketing",
        tier=2, vendor="Ensemble Data", sync_mode="scheduled", cadence="daily",
        credential_key="ensembledata_token",
        notes="Instagram 无免费关键词搜索 API,经第三方聚合源采集红人内容;需 ensembledata_token。",
        collect=creator_runner.collect_instagram,
    ),
    ConnectorSpec(
        id="tiktok_listening", name="TikTok 红人(第三方)", category="creators", dimension="marketing",
        tier=2, vendor="Ensemble Data", sync_mode="scheduled", cadence="daily",
        credential_key="ensembledata_token",
        notes="TikTok 无免费关键词搜索 API,经第三方聚合源采集红人内容;需 ensembledata_token。",
        collect=creator_runner.collect_tiktok,
    ),
    ConnectorSpec(
        id="x_search", name="X 红人(第三方)", category="creators", dimension="marketing",
        tier=2, vendor="Ensemble Data", sync_mode="scheduled", cadence="daily",
        credential_key="ensembledata_token",
        notes="X(Twitter)经第三方聚合源采集红人内容(官方 API 限额高/付费贵);需 ensembledata_token。",
        collect=creator_runner.collect_x,
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


def sync_to_db(conn: sqlite3.Connection) -> None:
    """Upsert connector metadata into the sources table (preserve runtime stats)."""
    now = utc_now()
    keep = tuple(BY_ID.keys())
    placeholders = ", ".join("?" for _ in keep)
    conn.execute(f"DELETE FROM sources WHERE id NOT IN ({placeholders})", keep)
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
