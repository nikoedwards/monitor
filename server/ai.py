"""LLM integration (Anthropic Messages protocol via configurable proxy).

Used to draft a full brand touchpoint profile from a single keyword. The API
key is stored server-side (settings table, env fallback) and never returned to
the browser. Uses stdlib urllib (no extra dependency).
"""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULTS = {
    "llm_base_url": "https://agent-api.shuiditech.com/api/v1/messages",
    "llm_model": "claude-opus-4.8",
    "llm_app_title": "monitor-hub",
    "llm_max_tokens": "4096",
}

# settings key -> env var fallback
ENV_FALLBACK = {
    "llm_api_key": "LLM_API_KEY",
    "llm_base_url": "LLM_BASE_URL",
    "llm_model": "LLM_MODEL",
    "llm_app_title": "LLM_APP_TITLE",
    "llm_max_tokens": "LLM_MAX_TOKENS",
}


class LlmError(RuntimeError):
    pass


def get_config(conn: sqlite3.Connection) -> dict:
    import os

    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    cfg: dict[str, str] = {}
    for key, env in ENV_FALLBACK.items():
        cfg[key] = (stored.get(key) or os.environ.get(env) or DEFAULTS.get(key, "") or "").strip()
    return cfg


def is_configured(conn: sqlite3.Connection) -> bool:
    return bool(get_config(conn).get("llm_api_key"))


def call_llm(cfg: dict, system: str, prompt: str) -> str:
    if not cfg.get("llm_api_key"):
        raise LlmError("尚未配置大模型 Token，请先在设置中填写。")
    try:
        max_tokens = int(cfg.get("llm_max_tokens") or 4096)
    except ValueError:
        max_tokens = 4096
    payload = {
        "model": cfg.get("llm_model") or DEFAULTS["llm_model"],
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        cfg["llm_base_url"] or DEFAULTS["llm_base_url"],
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {cfg['llm_api_key']}",
            "X-WP-Title": cfg.get("llm_app_title") or DEFAULTS["llm_app_title"],
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=90) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LlmError(f"大模型返回错误 {exc.code}: {detail}") from exc
    except (URLError, OSError) as exc:
        raise LlmError(f"调用大模型失败: {exc}") from exc

    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
        blocks = data.get("content") or []
        for block in blocks:
            if isinstance(block, dict) and block.get("type", "text") == "text" and block.get("text"):
                return block["text"]
        # Some proxies may return {content: "..."}
        if isinstance(data.get("content"), str):
            return data["content"]
    except (json.JSONDecodeError, AttributeError) as exc:
        raise LlmError(f"无法解析大模型响应: {exc}") from exc
    raise LlmError("大模型响应中没有文本内容。")


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    # strip markdown code fences
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # fall back to first balanced {...}
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise LlmError("大模型未返回有效 JSON")


DRAFT_SYSTEM = (
    "你是品牌情报分析助手。根据用户给出的关键词（通常是品牌名），"
    "输出该品牌的结构化档案，仅返回一个 JSON 对象，不要任何解释或 markdown 围栏。"
)

DRAFT_PROMPT_TEMPLATE = """关键词：{keyword}

请输出严格符合以下结构的 JSON（字段缺失时给空字符串或空数组，不要编造不存在的链接，宁可留空）：

{{
  "name": "品牌规范名称",
  "category": "所属品类",
  "description": "一句话简介",
  "official_website": "官网 URL",
  "is_competitor": false,
  "monitoring_keywords": ["中文名", "英文名", "缩写/常见别称", "旧名/曾用名", "核心子品牌或产品线名"],
  "products": [{{"name": "产品名", "category": "子品类", "sku": ""}}],
  "sales": [{{"platform": "amazon|dtc|walmart|target|ebay|temu|tiktok_shop|shopee|lazada|aliexpress|offline", "url": "店铺/商品页 URL"}}],
  "social": [{{"platform": "instagram|tiktok|x|youtube|facebook|linkedin|pinterest", "url": "官方账号主页 URL"}}],
  "community": [{{"platform": "reddit|discord|facebook_group|telegram|quora", "url": "官方社群/讨论区 URL"}}]
}}

要求：
- sales/social/community 中 platform 必须从给定取值中选择；其它平台放入对应分类并自定义 platform 名。
- 只给你较有把握的真实链接，不确定就不要放进数组。
- monitoring_keywords 的目标是“尽可能追全该品牌的媒体报道”，请穷举同一品牌实体的所有称呼方式：中文名、英文名、缩写、旧名/曾用名、常见错拼或带/不带空格的写法，以及可能不带母品牌名单独被报道的核心子品牌/产品线名。
- 不要放入宽泛的品类词、通用词或人名（会引入大量无关报道）；每个词都应能明确指向这一个品牌实体。最多 10 个，宁缺毋滥。"""


def draft_brand(conn: sqlite3.Connection, keyword: str) -> dict:
    cfg = get_config(conn)
    text = call_llm(cfg, DRAFT_SYSTEM, DRAFT_PROMPT_TEMPLATE.format(keyword=keyword.strip()))
    draft = _extract_json(text)
    draft.setdefault("name", keyword.strip())
    for key in ("products", "sales", "social", "community", "monitoring_keywords"):
        if not isinstance(draft.get(key), list):
            draft[key] = []
    return draft


# ---------------------------------------------------------- insight summary
SUMMARY_SYSTEM = (
    "你是品牌舆情与社群分析助手。基于用户给出的真实记录，输出简洁、可执行的中文洞察。"
    "只依据给定数据，不要编造事实或链接。仅返回一个 JSON 对象，不要任何解释或 markdown 围栏。"
)

SUMMARY_PROMPT_TEMPLATE = """以下是某品牌在 {start} 至 {end} 期间、渠道「{channel}」的 {count} 条真实记录（可能包含帖子、回复与媒体报道）：

{records}

请仅返回严格符合以下结构的 JSON：

{{
  "summary": "3-5 句话的整体总结",
  "highlights": ["关键发现1", "关键发现2"],
  "themes": [{{"theme": "高频主题", "mentions": 0}}],
  "sentiment": {{"positive": 0, "neutral": 0, "negative": 0, "overall": "positive|neutral|negative"}},
  "representative": [{{"title": "代表性内容标题", "url": "链接", "why": "为何有代表性"}}]
}}

要求：
- 完全基于给定记录，不要编造；representative 中的 url 必须来自上面的输入记录。
- highlights 3-6 条，themes 取最高频的若干主题，representative 2-5 条。
- 若数据稀少也要如实总结，不要夸大。"""


def summarize_records(conn: sqlite3.Connection, records: list[dict], context: dict) -> dict:
    """Generate an LLM insight summary over a set of records (brand + range + channel)."""
    cfg = get_config(conn)
    items = []
    for r in records[:120]:
        items.append({
            "title": (r.get("title") or "")[:160],
            "body": (r.get("body") or "")[:280],
            "type": r.get("data_type"),
            "platform": r.get("platform"),
            "sentiment": r.get("sentiment"),
            "url": r.get("url"),
            "at": r.get("occurred_at"),
        })
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        start=context.get("start") or "",
        end=context.get("end") or "",
        channel=context.get("channel") or "全部",
        count=len(items),
        records=json.dumps(items, ensure_ascii=False),
    )
    text = call_llm(cfg, SUMMARY_SYSTEM, prompt)
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        raise LlmError("大模型未返回有效的总结对象")
    parsed.setdefault("summary", "")
    for key in ("highlights", "themes", "representative"):
        if not isinstance(parsed.get(key), list):
            parsed[key] = []
    if not isinstance(parsed.get("sentiment"), dict):
        parsed["sentiment"] = {}
    return parsed


# ---------------------------------------------------------- listing auto-mapping
MAP_SYSTEM = (
    "你是电商销售数据分析助手。你的任务是把电商在售商品（Listing）映射到品牌自有的产品（Product）。"
    "只依据语义判断，不要编造。仅返回一个 JSON 对象，不要任何解释或 markdown 围栏。"
)

MAP_PROMPT_TEMPLATE = """下面是某品牌的「产品列表」和一批待映射的「Listing 列表」。
请判断每个 Listing 最可能对应的产品。

产品列表（product_id 必须从中选择）：
{products}

待映射 Listing：
{listings}

请只返回如下结构的 JSON：

{{
  "mappings": [
    {{"listing_id": "Listing 的 id", "product_id": "匹配到的 product_id 或 null", "confidence": 0.0, "reason": "简短理由"}}
  ]
}}

要求：
- product_id 必须是产品列表中真实存在的 id；无法确定时填 null，不要勉强匹配。
- confidence 为 0~1 的小数，表示匹配把握；不确定就给低分或填 null。
- 同一产品可对应多个 Listing（不同变体/套装/站点）。
- 只依据标题、ASIN/SKU、品类等语义信息判断，礼品卡、信用卡、配件等与产品无关的 Listing 一律填 null。
- mappings 必须覆盖全部待映射 Listing，每个 Listing 一条。"""


def automap_listings(
    conn: sqlite3.Connection,
    listings: list[dict],
    products: list[dict],
    min_confidence: float = 0.6,
) -> list[dict]:
    """Ask the LLM to map each (unmapped) listing to a product.

    Returns a list of {listing_id, product_id, confidence, reason, applied}
    where ``applied`` reflects whether the match is confident + valid enough to
    write back. The caller persists the applied rows.
    """
    cfg = get_config(conn)
    valid_ids = {p["id"] for p in products}
    products_text = json.dumps(
        [{"product_id": p["id"], "name": p.get("name"), "sku": p.get("sku") or "", "category": p.get("category") or ""} for p in products],
        ensure_ascii=False,
        indent=2,
    )
    listings_text = json.dumps(
        [{"listing_id": l["id"], "title": l.get("title") or "", "asin": l.get("asin") or "", "sku": l.get("sku") or "", "channel": l.get("channel") or ""} for l in listings],
        ensure_ascii=False,
        indent=2,
    )
    text = call_llm(cfg, MAP_SYSTEM, MAP_PROMPT_TEMPLATE.format(products=products_text, listings=listings_text))
    parsed = _extract_json(text)
    raw = parsed.get("mappings") if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        raise LlmError("大模型未返回有效的 mappings 数组")

    listing_ids = {l["id"] for l in listings}
    results: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        lid = item.get("listing_id")
        if lid not in listing_ids or lid in seen:
            continue
        seen.add(lid)
        pid = item.get("product_id")
        if pid not in valid_ids:
            pid = None
        try:
            confidence = float(item.get("confidence")) if item.get("confidence") is not None else None
        except (TypeError, ValueError):
            confidence = None
        applied = bool(pid) and (confidence is None or confidence >= min_confidence)
        results.append({
            "listing_id": lid,
            "product_id": pid if applied else None,
            "confidence": confidence,
            "reason": str(item.get("reason") or "")[:200],
            "applied": applied,
        })
    return results
