"""Lightweight rule-based sentiment / intent / topic tagging and PR heuristics.

No ML dependency: keyword lexicons with negation handling. Designed to be
extensible (add terms to the dictionaries below).
"""
from __future__ import annotations

from collections import Counter
import re

POSITIVE_TERMS = {
    "amazing", "love", "loved", "great", "excellent", "easy", "fast", "helpful",
    "reliable", "recommend", "smooth", "好用", "喜欢", "推荐", "稳定", "快", "满意",
}
NEGATIVE_TERMS = {
    "bad", "broken", "confusing", "crash", "delayed", "difficult", "expensive",
    "hate", "refund", "slow", "stopped", "terrible", "unstable", "差", "难用",
    "太贵", "退款", "慢", "崩溃", "投诉", "不稳定",
}
NEGATION_TERMS = {"not", "no", "never", "don't", "doesn't", "isn't", "wasn't", "没", "不", "无"}

TOPIC_TERMS = {
    "price": {"price", "pricing", "expensive", "discount", "coupon", "太贵", "价格"},
    "quality": {"quality", "broken", "durable", "reliable", "材质", "质量"},
    "delivery": {"shipping", "delivery", "delayed", "arrived", "物流", "到货"},
    "feature": {"feature", "integration", "dashboard", "workflow", "功能", "接口"},
    "support": {"support", "service", "agent", "ticket", "客服", "售后"},
    "experience": {"app", "ios", "android", "ux", "experience", "crash", "卡顿", "闪退", "体验", "不好用"},
    "ads": {"ad", "ads", "campaign", "creative", "广告", "投放", "素材"},
    "creator": {"creator", "influencer", "tiktok", "reels", "达人", "红人"},
    "retail": {"amazon", "shopify", "store", "checkout", "电商", "订单"},
    "pr": {"press", "media", "publication", "journalist", "媒体", "报道"},
}

PAID_PR_TERMS = {
    "sponsored", "paid content", "partner content", "advertorial", "press release",
    "pr newswire", "business wire", "globenewswire", "newswire", "ein presswire",
    "美通社", "新闻稿", "通稿", "赞助", "推广",
}

PUBLICATION_REACH_HINTS = [
    ("reuters", 9_000_000, "tier_1"),
    ("associated press", 8_500_000, "tier_1"),
    ("ap news", 8_000_000, "tier_1"),
    ("bloomberg", 7_500_000, "tier_1"),
    ("forbes", 6_500_000, "tier_1"),
    ("the wall street journal", 6_000_000, "tier_1"),
    ("new york times", 6_000_000, "tier_1"),
    ("bbc", 6_000_000, "tier_1"),
    ("cnn", 5_500_000, "tier_1"),
    ("techcrunch", 2_500_000, "tier_2"),
    ("the verge", 2_200_000, "tier_2"),
    ("wired", 2_000_000, "tier_2"),
    ("fast company", 1_700_000, "tier_2"),
    ("adweek", 1_200_000, "tier_2"),
    ("business wire", 550_000, "wire"),
    ("pr newswire", 520_000, "wire"),
    ("globenewswire", 420_000, "wire"),
]

PR_THEME_TERMS = {
    "product_launch": {"launch", "unveil", "introduce", "release", "debut", "发布", "推出", "新品"},
    "partnership": {"partner", "partnership", "collaboration", "alliance", "合作", "联名"},
    "funding": {"funding", "investment", "raised", "series a", "融资", "投资"},
    "retail_expansion": {"retail", "store", "amazon", "walmart", "target", "marketplace", "渠道", "上架"},
    "creator_campaign": {"creator", "influencer", "tiktok", "youtube", "ambassador", "红人", "达人"},
    "leadership": {"ceo", "executive", "appoint", "hire", "founder", "任命", "高管"},
    "sustainability": {"sustainable", "climate", "recycle", "carbon", "esg", "可持续", "环保"},
    "reputation_risk": {"recall", "lawsuit", "complaint", "investigation", "breach", "召回", "诉讼", "调查"},
}

VOC_ACTION_STATUSES = {"open", "assigned", "in_progress", "resolved", "closed"}
OWNER_TEAMS = {"experience_team", "support_team", "product_team", "marketing_team"}


def _count_terms(text: str, lowered: str, terms: set[str]) -> int:
    return sum(1 for term in terms if term in lowered or term in text)


def _has_negation(lowered: str) -> bool:
    return any(term in lowered for term in NEGATION_TERMS)


def _matched_terms(text: str, lowered: str, terms: set[str]) -> list[str]:
    return sorted(term for term in terms if term in lowered or term in text)


def _evidence_snippets(text: str, terms: list[str], limit: int = 2) -> list[str]:
    if not terms:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", text) if part.strip()]
    lowered_terms = [term.lower() for term in terms]
    matches = [part for part in parts if any(term in part.lower() for term in lowered_terms)]
    return [part[:240] for part in matches[:limit]]


def analyze_text(text: str) -> dict:
    text = text or ""
    lowered = text.lower()
    positive_terms = _matched_terms(text, lowered, POSITIVE_TERMS)
    negative_terms = _matched_terms(text, lowered, NEGATIVE_TERMS)
    negation_terms = _matched_terms(text, lowered, NEGATION_TERMS)
    positive = len(positive_terms)
    negative = len(negative_terms)
    negation_flipped = False

    # Simple negation flip: "not great" reads less positive.
    if _has_negation(lowered) and positive > negative:
        positive, negative = negative, positive
        negation_flipped = True

    score = max(-1.0, min(1.0, (positive - negative) / 3))
    if score > 0.2:
        sentiment = "positive"
    elif score < -0.2:
        sentiment = "negative"
    else:
        sentiment = "neutral"

    if any(t in lowered or t in text for t in ("refund", "退款", "broken", "crash", "投诉")):
        intent = "complaint"
    elif any(t in lowered or t in text for t in ("wish", "need", "希望", "能不能", "feature")):
        intent = "request"
    elif any(t in lowered or t in text for t in ("buy", "switch", "competitor", "换成", "购买")):
        intent = "purchase_signal"
    elif sentiment == "positive":
        intent = "praise"
    else:
        intent = "observation"

    topics = [
        topic
        for topic, terms in TOPIC_TERMS.items()
        if any(t in lowered or t in text for t in terms)
    ][:4]

    matched_terms = positive_terms + negative_terms + negation_terms
    if negation_flipped:
        reason = "检测到正向词，同时检测到否定词；当前规则将正向计数反转为负向。"
    elif sentiment == "negative":
        reason = "负向词命中数高于正向词命中数，因此判定为负向。"
    elif sentiment == "positive":
        reason = "正向词命中数高于负向词命中数，因此判定为正向。"
    else:
        reason = "正负向词数量接近或未达到阈值，因此判定为中性。"

    return {
        "sentiment": sentiment,
        "sentiment_score": round(score, 4),
        "intent": intent,
        "topics": topics,
        "sentiment_explanation": {
            "method": "关键词规则",
            "reason": reason,
            "positive_terms": positive_terms,
            "negative_terms": negative_terms,
            "negation_terms": negation_terms,
            "evidence": _evidence_snippets(text, matched_terms),
        },
    }


def team_for_record(record: dict, topic: str = "") -> str:
    topics = set(record.get("topics") or [])
    if topic:
        topics.add(topic)
    haystack = " ".join(
        str(record.get(key) or "")
        for key in ("body", "title", "product", "platform", "data_type")
    ).lower()
    if "experience" in topics or any(
        t in haystack for t in ("app", "ios", "android", "ux", "crash", "卡顿", "闪退", "体验")
    ):
        return "experience_team"
    if (
        record.get("intent") == "complaint"
        or topics & {"support", "delivery"}
        or any(t in haystack for t in ("refund", "ticket", "投诉", "退款", "售后"))
    ):
        return "support_team"
    if record.get("intent") == "request" or topics & {"quality", "feature"}:
        return "product_team"
    return "marketing_team"


def team_for_records(records: list[dict], topic: str = "") -> str:
    if not records:
        return "marketing_team"
    counts = Counter(team_for_record(r, topic) for r in records)
    return counts.most_common(1)[0][0]


def priority_for_records(records: list[dict]) -> str:
    negative = sum(1 for r in records if r.get("sentiment") == "negative")
    rate = round(negative / len(records), 4) if records else 0.0
    complaints = sum(1 for r in records if r.get("intent") == "complaint")
    if negative >= 5 or (negative >= 3 and rate >= 0.5):
        return "urgent"
    if negative >= 2 or complaints >= 2:
        return "high"
    if negative or complaints:
        return "medium"
    return "low"


def classify_media_property(title: str, body: str, publication: str, url: str) -> tuple[str, float, str]:
    haystack = " ".join([title, body, publication, url]).lower()
    matched = [term for term in PAID_PR_TERMS if term in haystack]
    if matched:
        return "paid_pr", 0.82, f"matched {matched[0]}"
    if publication.lower() in {"pr newswire", "business wire", "globenewswire"}:
        return "paid_pr", 0.86, "wire publication"
    return "earned", 0.64, "no paid-placement marker"


def estimate_publication_metrics(publication: str, host: str) -> tuple[int, str]:
    key = f"{publication} {host}".lower()
    for needle, reach, tier in PUBLICATION_REACH_HINTS:
        if needle in key:
            return reach, tier
    if any(t in key for t in ("times", "post", "journal", "tribune", "news", "daily", "business")):
        return 220_000, "tier_3"
    if host:
        return 80_000, "tier_4"
    return 25_000, "unknown"


def detect_pr_themes(text: str) -> list[str]:
    lowered = (text or "").lower()
    themes = [
        theme
        for theme, terms in PR_THEME_TERMS.items()
        if any(t in lowered or t in text for t in terms)
    ]
    return themes[:4] or ["general_coverage"]
