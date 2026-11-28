"""Evidence and editorial selection for the marketing overview."""
from __future__ import annotations

from urllib.parse import urlsplit


LABELS = {"media": "媒体", "creators": "达人", "community": "社群", "social": "官方社交", "ads": "广告"}
EMPTY = "本期证据不足，暂无可确认的营销洞察。"


def content_examples(records: list[dict]) -> list[dict]:
    """Take a few recent records from distinct sources before repeating a source."""
    seen, examples, remainder = set(), [], []
    for record in records:
        title = " ".join(str(record.get("title") or record.get("body") or "").split())[:140]
        if not title:
            continue
        source = str(record.get("author") or record.get("platform") or "")[:80]
        body = " ".join(str(record.get("body") or "").split())[:200]
        excerpt = body[len(title):].strip() if body.startswith(title) else body
        sample = {"text": f"{str(record.get('occurred_at') or '')[:10]} {source}《{title}》", "excerpt": excerpt}
        url = record.get("url") or ""
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            try:
                if urlsplit(url).hostname:
                    sample["url"] = url
            except ValueError:
                pass
        if source in seen:
            if len(remainder) < 3:
                remainder.append(sample)
        else:
            seen.add(source)
            examples.append(sample)
        if len(examples) == 3:
            break
    return (examples + remainder)[:3]


def period_facts(channel: str, module: dict) -> str:
    n = module.get("total", 0)
    if module.get("status") != "ready":
        return ""
    if channel == "ads":
        changed = module.get("changed_ads")
        edits = f"，证实文案变化 {changed} 条" if changed is not None else ""
        return f"首次收录 {module['new_ads']} 条，记录停投 {module['stopped_ads']} 条{edits}。"
    if channel == "creators":
        return (f"收录 {n} 条，可识别的 {module['identified_accounts']} 个平台账号中，"
                f"{module['single_post_accounts']} 个仅出现一次。"
                f"{module['collab_posts']} 条命中品牌关联标记，{module['sponsored_posts']} 条命中赞助标记。")
    if channel == "community":
        return f"收录 {n} 条，其中主帖 {module['posts']} 条、回复 {module['replies']} 条。"
    if channel == "media":
        tiers = {item["tier"]: item["total"] for item in module["by_tier"]}
        return (f"收录 {n} 篇，一至四线媒体报道分别为 "
                + "、".join(str(tiers.get(f"tier_{i}", 0)) for i in range(1, 5)) + " 篇。")
    if not module["measured_posts"]:
        return f"收录 {n} 条内容，缺少互动数。"
    return f"收录 {n} 条内容，有互动数的 {module['measured_posts']} 条合计 {module['total_engagement']} 次互动，其中单条最高 {module['peak_engagement']} 次。"


def marketing_evidence(current: dict, previous: dict, window: dict) -> dict:
    evidence = {}
    periods = (("current", "本期", current), ("previous", "上期", previous))
    for period, label, modules in periods:
        for channel, module in modules.items():
            fact = period_facts(channel, module)
            if not fact:
                continue
            ref = f"{period}.{channel}"
            if module.get("total") == 1000:
                fact = "达到读取上限，仅计最近 1000 条。" + fact
            latest = (module.get("last_data_at") or "")[:10]
            end = window.get("end" if period == "current" else "previous_end", "")
            if channel != "ads" and latest and latest < end:
                fact += f"末条记录日期为 {latest}。"
            evidence[ref] = {"text": f"{label}{LABELS[channel]} · {fact}"}
            if channel == "social" and module.get("peak_example"):
                sample = module["peak_example"][0]
                evidence[f"{ref}.peak"] = {**sample, "text": f"{label}互动最高的官方内容 · {sample['text']}"}
            for i, example in enumerate(module.get("examples", []), start=1):
                evidence[f"{ref}.{i}"] = {**example, "text": f"{label}{LABELS[channel]} · {example['text']}"}
    return {"range": window, "evidence": evidence}


def rule_findings(current: dict, previous: dict) -> list[dict]:
    findings = []

    def add(title, refs, hypothesis, check):
        findings.append({"title": title, "evidence_ids": refs, "hypothesis": hypothesis, "check": check})

    community = current.get("community", {})
    prior = previous.get("community", {})
    if all(10 <= m.get("total", 0) < 1000 for m in (community, prior)):
        change = community["replies"] / community["total"] - prior["replies"] / prior["total"]
        if abs(change) >= 0.20:
            direction = "上升" if change > 0 else "下降"
            add(f"社群回复占比{direction}了 {abs(change)*100:.0f} 个百分点",
                ["current.community", "previous.community"], "",
                "按同一社群拆分主帖和回复，确认变化来自讨论节奏还是收录来源的变化。")

    social = current.get("social", {})
    if 4 <= social.get("measured_posts", 0) == social.get("total") < 1000 and social["total_engagement"] >= 20:
        share = social["peak_engagement"] / social["total_engagement"]
        if share >= 0.65:
            refs = ["current.social", "current.social.peak"] if social.get("peak_example") else ["current.social"]
            add(f"单条官方内容贡献了本期 {share:.0%} 的互动", refs,
                "", "单列这条内容，再比较其余帖子的互动，判断该题材是否值得复用。")

    creators = current.get("creators", {})
    accounts = creators.get("identified_accounts", 0)
    last = previous.get("creators", {})
    # With all authors identified, total minus account count is exactly the
    # number of additional posts beyond each account's first in that period.
    comparable = all(8 <= m.get("identified_posts", 0) == m.get("total") < 1000 for m in (creators, last))
    if comparable and creators["total"] <= last["total"] * 0.8 and abs(accounts - last["identified_accounts"]) <= 0.1 * last["identified_accounts"]:
        add("达人发帖减少，参与账号数仍接近上期", ["current.creators", "previous.creators"],
            "变化主要落在同一账号的后续发帖上，发布排期可能比合作规模更值得查。",
            "交叉核对两期账号名单与后续发帖日期，区分原有账号降频和新账号替换。")
    elif accounts >= 8 and creators["total"] < 1000 and creators["single_post_accounts"] / accounts >= 0.8:
        add("多数可识别的达人账号在本期只发过一条相关内容", ["current.creators"], "",
            "下期核对哪些账号围绕同一卖点再次发布，区分持续传播和一次性提及。")

    ads = current.get("ads", {})
    new, stopped = ads.get("new_ads", 0), ads.get("stopped_ads", 0)
    if stopped >= 5 and stopped >= 1.4 * new:
        add("记录到的停投素材多于首次收录的素材", ["current.ads"],
            "一批素材可能到了集中下线的节点，单凭素材条数还看不出预算方向。",
            "核对停投素材的上线日期和落地页，看它们是否属于同一轮活动。")

    media = current.get("media", {})
    n = media.get("total", 0)
    top = sum(row["total"] for row in media.get("by_tier", []) if row["tier"] == "tier_1")
    classified = sum(row["total"] for row in media.get("by_tier", []) if row["tier"] in {"tier_1", "tier_2", "tier_3", "tier_4"})
    if 10 <= n < 1000 and classified == n and top / n <= 0.1:
        add("本期报道很少进入一线媒体", ["current.media"], "",
            "对照报道标题与首发来源，区分同一消息被转载和多个媒体独立选题。")
    return findings[:3]


def grounded_findings(rows: list, evidence: dict) -> list[dict]:
    result, titles, used_refs = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        refs = row.get("evidence_ids")
        if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or r not in evidence for r in refs):
            continue
        if not any(r.startswith("current.") for r in refs) or len(set(refs)) > 4:
            continue
        title, hypothesis, check = (row.get(k, "") for k in ("title", "hypothesis", "check"))
        if not all(isinstance(t, str) for t in (title, hypothesis, check)):
            continue
        title, hypothesis, check = title.strip(), hypothesis.strip(), check.strip()
        key = frozenset(refs)
        if not title or title in titles or key in used_refs or (hypothesis and not check):
            continue
        titles.add(title)
        used_refs.add(key)
        result.append({"title": title, "hypothesis": hypothesis, "check": check,
                       "evidence": [evidence[r] for r in dict.fromkeys(refs)]})
        if len(result) == 3:
            break
    return result
