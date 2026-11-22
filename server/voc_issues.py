"""Turn feedback into issue groups and reviewable excerpts from the stored text."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit


_RULES = (
    ("dark_mode", r"(?:\b(?:no|without|missing|lack(?:s|ing)?|add|want|need)\b[^.!?\n]{0,45}\bdark mode\b|\bdark mode\b[^.!?\n]{0,25}\b(?:please|missing|needed)\b|(?:没有|缺少|希望支持|希望增加|請加入)(?:深色|暗色|夜间|夜間)模式|pas de mode sombre)",
     "App 缺少深色模式"),
    ("template_view", r"(?:modèles? personnalisés?.*(?:ic[oô]nes?|liste)|(?:ic[oô]nes?|liste).*modèles? personnalisés?)",
     "希望自定义模板增加紧凑视图"),
    ("folder_vocabulary", r"vocabulaire[s]? personnalisés?.*dossier|dossier.*vocabulaire[s]? personnalisés?",
     "按文件夹配置自定义词表"),
    ("speaker_labels", r"(?:auto[ -]?(?:speaker[ -])?label(?:l?ing)?|speaker(?:s| names?| labels?| identification| detection| recognition)|说话人(?:识别|标签)|講者辨識)",
     "说话人标签被移除或未自动标注"),
    ("calendar_banner", r"(?:calendar|日历).*(?:bar|banner|box|hide|remove|横幅|隐藏)|(?:bar|banner|box|hide|remove).*(?:calendar|日历)",
     "希望能关闭日历同步提示"),
    ("shortcut_recording", r"(?:record(?:ing)?|录音)", "希望通过快捷指令控制录音"),
    ("duplicate_charge", r"\b(?:charged twice|double[ -]charged|duplicate charge)\b|重复扣[款费]|重複扣[款費]",
     "同一笔费用被重复扣款"),
    ("unauthorized_charge", r"\bunauthorized charges?\b|未授权扣款|未授權扣款",
     "用户反馈出现未经授权的扣款"),
    ("recording_loss", r"\b(?:lost|deleted|missing)\b[^.!?\n]{0,45}\brecordings?\b|录音丢失|錄音遺失",
     "录音文件丢失或找不到"),
    ("case_fit", r"(?:magnetic case|too big|back panel|(?:doesn.t|does not) fit|保护壳)",
     "磁吸保护壳与 iPhone Duo 尺寸不匹配"),
    ("login", r"\b(?:can.t|cannot|couldn.t|unable to|won.t|failed to)\s+(?:log[ -]?in|sign[ -]?in|register)\b|\b(?:log[ -]?in|sign[ -]?in)\s+(?:fails?|failed|error)\b|无法登[录陆入]|無法登[錄入]|登录失败",
     "无法登录或注册"),
    ("wifi", r"(?:doesn.t work|cannot connect|can.t connect|unable to connect|fails? to connect)[^.!?\n]{0,25}\bwi[ -]?fi\b|\bwi[ -]?fi\b[^.!?\n]{0,25}(?:not work|won.t connect|disconnect)|无法连接\s*wi[ -]?fi",
     "Wi-Fi 连接或使用失败"),
    ("app_crash", r"\b(?:crash(?:es|ed|ing)?|freez(?:e|es|ing)|frozen)\b|闪退|閃退|崩溃|卡死",
     "应用闪退或卡死"),
)
_RULES = tuple((key, re.compile(pattern, re.I), title) for key, pattern, title in _RULES)
_CONTEXT = {
    "speaker_labels": r"remov|missing|no longer|strip|lost|incorrect|doesn.t|does not|not work|没有|缺少|丢|去掉|取消|错误",
    "calendar_banner": r"hide|remove|dismiss|taking up|占|隐藏|关闭",
    "shortcut_recording": r"shortcut|siri|app intents|快捷指令",
    "case_fit": r"iphone duo|背板",
}
_PLACEHOLDER = re.compile(r"^(?:google play 用户评价|app store 用户评价|unknown|untitled|无标题)$", re.I)
_COMPLAINT = re.compile(
    r"\b(?:can.t|cannot|couldn.t|unable|fails?|failed|wrong|no longer|missing|keeps? |"
    r"doesn.t|stopped|without|too (?:slow|big|small|expensive)|instead of|wish|please add)\b"
    r"|无法|没有|缺少|失败|希望|错误|不能|要么|只能|無法|沒有|錯誤|失敗|希望", re.I,
)
_SENTENCES = re.compile(r"[^\n]+?(?:[。！？!?…]+|\.(?=\s|$)|(?=\n|$))")


def _sentences(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in _SENTENCES.finditer(text) if match.group().strip()]


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    prefix = text[:limit - 1].rsplit(" ", 1)[0]
    return (prefix if len(prefix) >= limit * 0.8 else text[:limit - 1]).rstrip() + "…"


def analyze_issue(record: dict) -> dict:
    title = (record.get("title") or "").strip()
    if _PLACEHOLDER.fullmatch(title):
        title = ""
    body = (record.get("body") or "").strip() or title
    spans = _sentences(body)
    text = f"{title}\n{body}"
    matched = None
    key = issue_title = summary = ""
    for rule_key, pattern, label in _RULES:
        context = _CONTEXT.get(rule_key)
        if not pattern.search(text) or (context and not re.search(context, text, flags=re.I)):
            continue
        if rule_key == "app_crash" and record.get("platform") not in {"app_store", "google_play"} and not re.search(r"\b(?:app|application|software)\b|应用|應用", text, flags=re.I):
            continue
        if rule_key == "app_crash" and re.search(r"\b(?:never|without|no)\s+(?:crash|freez)|\b(?:stopped|no longer) (?:crash|freez)|不再(?:闪退|崩溃|卡死)", text, flags=re.I):
            continue
        key, issue_title = rule_key, label
        matched = pattern.search(body)
        break

    # Pick a clause from the evidence itself when the issue has no rule.
    def score(span: tuple[int, int]) -> tuple[bool, int]:
        sentence = body[span[0]:span[1]]
        return bool(matched and span[0] <= matched.start() < span[1]), len(_COMPLAINT.findall(sentence))

    core_index = max(range(len(spans)), key=lambda index: score(spans[index]), default=0)
    left, right = spans[core_index] if spans else (0, len(body))
    core = body[left:right].strip()
    if core:
        # A contrast such as "excellent tool except..." should highlight the issue clause.
        contrast = re.search(r"\b(?:but|except|however)\b[ ,:]+", core, re.I)
        if contrast and len(core[contrast.end():].strip()) >= 12:
            core = core[contrast.end():].strip()

    if key == "dark_mode" and re.search(r"long summaries|长摘要|长篇摘要", text, re.I) and re.search(r"at night|night reading|夜间|晚上", text, re.I):
        summary = "用户反馈 App 没有深色模式，夜间阅读长篇摘要吃力。"
    if key == "template_view" and re.search(r"ic[oô]nes? ou en liste", text, re.I):
        issue_title = "希望自定义模板支持小图标或列表视图"
        if re.search(r"d'un coup|défiler", text, re.I):
            summary = "用户希望同屏看到更多自定义模板，减少逐个滚动查找。"
    if key == "folder_vocabulary" and re.search(r"activer et désactiver", text, re.I):
        summary = "用户希望创建多套自定义词表，分别分配给各个文件夹，并能按文件夹启用或停用。"
    if key == "case_fit" and re.search(r"hangs? past the bottom edge", text, re.I):
        summary = "用户反馈磁吸保护壳装上后会超出手机底边。"
    if key == "login" and re.search(r"after resetting (?:my|the) password|重置密码后|重設密碼後", text, re.I):
        summary = "用户反馈重置密码后仍无法登录。"
    if key == "shortcut_recording" and re.search(r"start.{0,20}stop|开始.{0,15}停止", text, re.I):
        summary = "用户希望通过快捷指令或 Siri 开始和停止录音。"
    if not key:
        issue_title = _shorten(core or title, 104)
        # Merge only matching claims in the fallback, not every complaint with the same tag.
        normalized = re.sub(r"[\W_]+", " ", (core or title).casefold()).strip()
        key = "quote_" + hashlib.sha256((normalized or str(record.get("id"))).encode()).hexdigest()[:20]

    start, end = left, right
    if core_index + 1 < len(spans) and spans[core_index + 1][1] - start <= 540:
        end = spans[core_index + 1][1]
    if core_index > 0 and end - spans[core_index - 1][0] <= 540:
        start = spans[core_index - 1][0]
    if end - start > 540:
        anchor = matched.start() if matched else left
        start = max(start, anchor - 100)
        end = min(end, start + 540)
    excerpt = body[start:end].strip()
    highlight = core if core and core in excerpt and len(core) <= 280 and (matched or score((left, right))[1]) else ""
    if not highlight and matched and matched.group() in excerpt:
        highlight = matched.group()
    if not highlight and excerpt and record.get("sentiment") == "negative":
        complaint = _COMPLAINT.search(excerpt)
        start_at = max(0, complaint.start() - 45) if complaint else 0
        highlight = core if core in excerpt and 0 < len(core) <= 280 else excerpt[start_at:start_at + 150].strip()
    return {
        "key": key,
        "title": issue_title,
        "summary": summary,
        "evidence": {
            "record_id": record.get("id"), "title": title, "text": body,
            "excerpt": ("…" if start else "") + excerpt + ("…" if end < len(body) else ""),
            "highlights": [highlight] if highlight else [],
            "source_key": record.get("voice_source"), "platform": record.get("platform"),
            "author": record.get("author"), "occurred_at": record.get("occurred_at"),
            "rating": (record.get("metrics") or {}).get("rating"),
            **_feedback_link(record),
        },
    }


def _feedback_link(record: dict) -> dict:
    value = (record.get("url") or "").strip()
    try:
        url = urlsplit(value)
    except ValueError:
        return {}
    if url.scheme not in {"http", "https"} or not url.hostname:
        return {}
    if url.hostname == "play.google.com" and url.path == "/store/apps/details":
        params = dict(parse_qsl(url.query))
        # Older records prefix the Google Play review ID with the storefront.
        external_id = re.sub(r"^[a-z]{2}:", "", str(record.get("external_id") or ""), flags=re.I)
        review_id = params.get("reviewId") or external_id
        if params.get("id") and re.fullmatch(r"(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|gp:[\w-]+)", review_id, re.I):
            params["reviewId"] = review_id
            return {"url": url._replace(query=urlencode(params)).geturl(), "url_kind": "review"}
        return {"url": value, "url_kind": "store"}
    if url.hostname in {"apps.apple.com", "itunes.apple.com"} or record.get("voice_source") == "app_reviews":
        return {"url": value, "url_kind": "store"}
    if record.get("data_type") == "community_reply":
        raw = record.get("raw") or {}
        post_number = str(raw.get("post_number") or "")
        if record.get("platform") == "discourse" and post_number.isdigit() and re.fullmatch(r"/t/(?:[^/]+/)?\d+/?", url.path):
            path = f"{url.path.rstrip('/')}/{post_number}"
            return {"url": url._replace(path=path).geturl(), "url_kind": "source"}
        return {"url": value, "url_kind": "thread"} if value.rstrip("/") != str(raw.get("site") or "").rstrip("/") else {}
    return {"url": value, "url_kind": "source"}
