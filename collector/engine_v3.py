#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
专业信息收集助手 V3 引擎
=======================
对齐《提示词_专业信息收集助手_V3》：
  · 27 个领域（含再制造、臭氧层ODS/HFCs 两个专项）
  · 8 个重点部委覆盖度自查
  · 仅收当月；按发布时间先后排列、按自然周分节
  · 每条：机关+文号+全称 / 时间 / 3-5 要点（带分类小标题）/ 官方链接
  · 附表：时间轴、覆盖度自查、无新增领域参考、待核项
输出：data/regulations.json、output/*.xlsx、单文件网页
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import (CST, Browser, Fetcher, article_text, clean, extract_candidates,  # noqa: E402
                    extract_points, find_doc_no, guess_org, is_document, normalize_title,
                    parse_date, process_source)

# ROOT：代码与配置所在目录（云端为函数代码目录）
# OUT  ：产物写出目录（云端代码目录只读，需指向 /tmp）
ROOT = Path(os.environ.get("LAW_ROOT") or Path(__file__).resolve().parent.parent)
OUT = Path(os.environ.get("LAW_OUT_DIR") or ROOT)

# 要点分类（提示词 4.2）
POINT_LABELS = [
    ("适用范围", ("适用", "范围", "对象", "适用于", "本通知所称")),
    ("目标任务", ("目标", "任务", "到20", "力争", "达到", "不低于", "提升")),
    ("奖惩监管", ("奖励", "补贴", "补助", "处罚", "考核", "监管", "不得", "禁止", "责任")),
    ("时限程序", ("申报", "报送", "截止", "期限", "程序", "前完成", "自20", "起施行")),
    ("资金安排", ("资金", "预算", "万元", "亿元", "拨付", "专项债")),
]

MINISTRY_ALIAS = {
    "中共中央/国务院": ("中共中央", "国务院"),
    "国家发展改革委": ("发展改革委", "发改委", "国家发展改革委"),
    "工业和信息化部": ("工业和信息化部", "工信部"),
    "生态环境部": ("生态环境部", "环保部"),
    "财政部": ("财政部",),
    "国家能源局": ("能源局",),
    "市场监管总局": ("市场监管总局", "国家市场监督管理总局"),
    "商务部": ("商务部",),
}


def point_label(sentence: str) -> str:
    for label, keys in POINT_LABELS:
        if any(k in sentence for k in keys):
            return label
    return "其他要求"


def detect_nature(title: str) -> str:
    if any(k in title for k in ("征求意见稿", "征求意见", "草案")):
        return "征求意见稿"
    if any(k in title for k in ("公示", "名单", "入围")):
        return "公示/名单"
    if any(k in title for k in ("答记者问", "发布会", "政策在身边", "联合印发",
                                "有关负责同志", "解读", "一图读懂")):
        return "政策动态"
    if title.rstrip().endswith(("？", "?")):      # 问答体报道
        return "政策动态"
    if any(k in title for k in ("通报", "报告")):
        return "政策动态"
    return "正式发文"


def level_of(item: dict) -> str:
    if item["region"] != "国家":
        return "地方"
    if any(k in item["publisher"] for k in ("中共中央", "国务院", "全国人大")):
        return "中央"
    return "部委"


def match_domains(text: str, domains: list[dict], limit: int = 3) -> list[str]:
    hits = []
    for d in domains:
        if any(k in text for k in d["keys"]):
            hits.append(d["name"])
            if len(hits) >= limit:
                break
    return hits


def week_of(day: int) -> str:
    if day <= 10:
        return "第一周（1—10日）"
    if day <= 17:
        return "第二周（11—17日）"
    if day <= 24:
        return "第三周（18—24日）"
    return "第四周（25日—月末）"


def enrich_v3(item, src, cfg, fetcher, domains):
    item = dict(item)
    all_keys = [k for d in domains for k in d["keys"]]
    is_api = src.get("adapter") == "api"
    text = ""
    if is_api:
        # 接口型源：字段已由接口给出，详情页是 JS 渲染，不再抓正文
        item["date"] = parse_date(item.get("date_hint", ""))
    else:
        try:
            html = fetcher.get(item["url"], src["url"], cfg["collect"]["timeout"])
            text, head = article_text(html)
            d = parse_date(head)
            if d and d > (datetime.now(CST) + timedelta(days=7)).strftime("%Y-%m-%d"):
                d = ""
            item["date"] = parse_date(item.get("date_hint", "")) or d or ""
        except Exception:
            item["date"] = parse_date(item.get("date_hint", ""))

    item["title"] = normalize_title(item["title"])
    year = str(datetime.now(CST).year)
    item["doc_no"] = find_doc_no(text, year)
    item["publisher"] = guess_org(item["title"], src.get("org", src["name"]))
    item["region"] = src.get("region", "国家")
    item["level"] = level_of(item)
    item["source"] = src["name"]
    item["nature"] = detect_nature(item["title"])
    item["type"] = item.get("type") or ""

    doms = match_domains(item["title"], domains)
    if not doms and text:
        doms = match_domains(text[:6000], domains, limit=2)
    if not doms and is_api and src.get("default_domain"):
        doms = [src["default_domain"]]
    item["domains"] = doms

    if is_api:
        n = item.get("_api_note") or ""
        item["points"] = [{"label": "时限程序", "text": n}] if n else []
        item["attach_only"] = False
    else:
        pts = extract_points(text, all_keys, title=item["title"]) if text else []
        item["points"] = [{"label": point_label(p), "text": p} for p in pts]
        item["attach_only"] = bool(
            text and not pts and re.search(r"\.(pdf|ofd|wps|docx?)\b", text, re.I))
    item["note"] = ""
    if item["attach_only"]:
        item["note"] = "正文以 PDF/OFD 附件发布，无网页正文可摘抄"
    if not item["doc_no"]:
        item["note"] = (item["note"] + "；" if item["note"] else "") + "文号未在网页正文中体现，以正式文本为准"
    return item


def build_coverage(items, cfg, statuses, domains, month_prefix):
    # 部门维度（8 个重点部委）
    ministries = []
    for m in cfg["focus_ministries"]:
        aliases = MINISTRY_ALIAS.get(m, (m,))
        own = [i for i in items if any(a in i["publisher"] for a in aliases)]
        joint = [i for i in items if not any(a in i["publisher"] for a in aliases)
                 and any(a in i["title"] for a in aliases)]
        if own:
            status = "有独立发文"
        elif joint:
            status = "△ 仅联合发文"
        else:
            status = "△ 无独立对口发文"
        ministries.append({"name": m, "count": len(own), "status": status,
                           "ids": [i["no"] for i in own],
                           "note": "" if own else "本月未检索到该部门单独发文"})

    # 地区维度
    regions = []
    for r in sorted({s.get("region", "") for s in cfg["sources"] if s.get("region")} - {""}):
        hit = [i for i in items if i["region"] == r]
        regions.append({"name": r, "count": len(hit), "ids": [i["no"] for i in hit]})

    # 领域维度（27 个）
    domain_rows = []
    for d in domains:
        hit = [i for i in items if d["name"] in i.get("domains", [])]
        domain_rows.append({"name": d["name"], "count": len(hit),
                            "ids": [i["no"] for i in hit],
                            "status": "有" if hit else "本月未检索到"})
    return ministries, regions, domain_rows


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    c = cfg["collect"]
    domains = cfg["domains"]
    fetcher = Fetcher(c.get("timeout", 25), c.get("retry", 2))
    browser = Browser(c.get("browser_wait", 3500))
    now = datetime.now(CST)
    month_prefix = now.strftime("%Y-%m")

    # 候选：用全部领域关键词筛选
    patched_cfg = dict(cfg)
    patched_cfg["keywords"] = {"core": [k for d in domains for k in d["keys"]],
                               "context": ["工业", "能源", "生态", "绿色", "减排", "降碳"]}

    all_items, ref_items, statuses = [], [], []
    domain_candidate = {d["name"]: 0 for d in domains}

    for src in cfg["sources"]:
        if os.environ.get("LAW_SKIP_OFFSHORE") == "1" and src.get("offshore_skip"):
            statuses.append({"name": src["name"], "group": src.get("group", ""),
                             "adapter": src.get("adapter", "html"), "ok": False,
                             "candidates": 0, "hit": 0,
                             "error": "境外网络不可达，GitHub 运行时跳过（国内部署自动恢复）"})
            continue
        if src.get("enabled", True) is False:
            statuses.append({"name": src["name"], "group": src.get("group", ""),
                             "adapter": "-", "ok": False, "candidates": 0, "hit": 0,
                             "error": src.get("note", "已禁用")})
            continue
        picked, st = process_source(src, patched_cfg, fetcher, browser)
        st["group"] = src.get("group", "")
        for p in picked:
            for d in domains:
                if any(k in p["title"] for k in d["keys"]):
                    domain_candidate[d["name"]] += 1
        print(f"[{st['adapter']:<7}] {src['name']:<14} 候选{st['candidates']:>4} 相关{len(picked):>3}")

        if not picked:
            statuses.append(st)
            continue

        if src.get("adapter") == "browser":
            enriched = [enrich_v3(it, src, cfg, fetcher, domains) for it in picked]
        else:
            with ThreadPoolExecutor(max_workers=c.get("detail_concurrency", 6)) as ex:
                enriched = list(ex.map(lambda it: enrich_v3(it, src, cfg, fetcher, domains), picked))

        ref_cutoff = (now - timedelta(days=c.get("reference_days", 150))).strftime("%Y-%m-%d")
        kept = []
        for it in enriched:
            if not it.get("domains"):
                continue
            if not it["date"].startswith(month_prefix):
                if it["date"] and it["date"] >= ref_cutoff:
                    ref_items.append(it)
                continue
            kept.append(it)
        kept = kept[: c.get("max_items_per_source", 15)]
        st["hit"] = len(kept)
        statuses.append(st)
        all_items += kept

    browser.close()

    # 去重（标题归一）
    uniq, seen = [], set()
    for it in all_items:
        key = re.sub(r"[\s《》〈〉（）()\[\]【】、，,：:·—\-]", "", it["title"])[:38]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    # 未来日期（多为生效日/计划日）剔除
    today = datetime.now(CST).strftime("%Y-%m-%d")
    future_limit = (datetime.now(CST) + timedelta(days=1)).strftime("%Y-%m-%d")
    uniq = [i for i in uniq if i.get("date") and i["date"] <= future_limit]

    # 解读/答记者问归并：同一文件的动态类条目并入正式发文的补充说明（提示词 4.3）
    def core_sig(t: str) -> str:
        m = re.search(r"《([^》]{4,60})》", t)
        if m:
            return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", m.group(1))[:26]
        t = re.sub(r"[【】\[\]|｜]", "", t)
        for w in ("一图读懂", "答记者问", "有关负责同志就", "政策解读", "解读", "政策在身边", "部门联合印发"):
            t = t.replace(w, "")
        t = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", t)
        return t[:26]

    formal = {}
    for it in uniq:
        if it["nature"] == "正式发文":
            formal.setdefault(core_sig(it["title"]), it)
    kept = []
    for it in uniq:
        if it["nature"] in ("政策动态", "征求意见稿") and False:
            pass
        if it["nature"] == "政策动态":
            target = formal.get(core_sig(it["title"]))
            if target is not None and target is not it:
                extra = f"另有答记者问/解读（{it['date']}），可作口径参考：{it['url']}"
                target["note"] = (target["note"] + "；" if target["note"] else "") + extra
                continue
        kept.append(it)
    uniq = kept

    # 排序：发布时间先后；同日 中央→部委→地方（提示词 4.1）
    order = {"中央": 0, "部委": 1, "地方": 2}
    uniq.sort(key=lambda x: (x.get("date", "9999"), order.get(x.get("level"), 3), x["title"]))
    for idx, it in enumerate(uniq, 1):
        it["no"] = idx
        try:
            it["week"] = week_of(int(it["date"][8:10]))
        except Exception:
            it["week"] = "日期待核"

    # 近期参考（去重、倒序取前 12）
    refs, rseen = [], set()
    for it in sorted(ref_items, key=lambda x: x.get("date", ""), reverse=True):
        key = re.sub(r"[\s《》〈〉（）()\[\]【】、，,：:·—\-]", "", it["title"])[:38]
        if key in rseen:
            continue
        rseen.add(key)
        refs.append({k: v for k, v in it.items() if k != "week"})
        if len(refs) >= 12:
            break

    ministries, regions, domain_rows = build_coverage(uniq, cfg, statuses, domains, month_prefix)

    # 关键词（领域）检索情况：候选命中 vs 入库
    domain_stats = []
    for d, row in zip(domains, domain_rows):
        cand = domain_candidate.get(d["name"], 0)
        domain_stats.append({"name": d["name"], "keys": d["keys"],
                             "candidates": cand, "hits": row["count"],
                             "status": "有新增" if row["count"] else ("有候选未入库" if cand else "无候选")})

    # 附表四：本月无新增领域 + 最近一次参考
    no_hit = []
    for row in domain_rows:
        if row["count"]:
            continue
        d = next(x for x in domains if x["name"] == row["name"])
        near = next((r for r in refs if any(k in r["title"] for k in d["keys"])), None)
        no_hit.append({"name": row["name"], "last_title": near["title"] if near else "",
                       "last_date": near["date"] if near else "",
                       "last_url": near["url"] if near else "",
                       "last_publisher": near.get("publisher", "") if near else ""})

    # 附表五：待核项
    pending = []
    for s in statuses:
        if not s["ok"]:
            pending.append({"item": f"信息源：{s['name']}", "status": f"未采集成功（{s.get('error', '')[:60]}）"})
    no_doc = [i for i in uniq if not i["doc_no"]]
    if no_doc:
        pending.append({"item": f"{len(no_doc)} 条文件未在网页正文体现文号", "status": "以正式文本为准，已逐条标注"})
    wxofficial = [i for i in uniq if not re.search(r"\.(gov\.cn|org\.cn|cn\.|com\.cn)/", i["url"])]
    if wxofficial:
        pending.append({"item": f"{len(wxofficial)} 条链接非 .gov.cn 域名", "status": "来自交易所/协会官网，属官方渠道"})

    payload = {
        "site": cfg["site"],
        "version": "V3",
        "month": month_prefix,
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(uniq),
        "sort_rule": "按发布时间先后排列；同日按 中央 → 部委 → 地方；按月内自然周分节",
        "time_rule": "时间口径：成文日期优先，缺则用公开发布日期",
        "domain_stats": domain_stats,
        "coverage": {"ministries": ministries, "regions": regions, "domains": domain_rows},
        "no_hit_domains": no_hit,
        "pending": pending,
        "sources": statuses,
        "reference": refs,
        "items": [{k: v for k, v in i.items() if not k.startswith("_")} for i in uniq],
    }

    out = OUT / cfg["output"]["json"]
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    out.write_text(text, encoding="utf-8")

    # 同步一份给网页（GitHub Pages 部署的是 docs/ 目录）
    web_data = OUT / "docs" / "data"
    web_data.mkdir(parents=True, exist_ok=True)
    (web_data / "regulations.json").write_text(text, encoding="utf-8")

    # 单文件网页
    tpl_path = ROOT / "docs" / "index.html"
    if tpl_path.exists():
        tpl = tpl_path.read_text(encoding="utf-8")
        marker = "<script>\nconst esc"
        if marker in tpl:
            standalone = tpl.replace(
                marker, f"<script>window.__DATA__={json.dumps(payload, ensure_ascii=False)};</script>\n" + marker)
            (OUT / cfg["output"].get("standalone", "双碳法规监测-单文件版.html")).write_text(
                standalone, encoding="utf-8")

    from export_excel_v3 import export
    xlsx = export(payload, OUT / cfg["output"]["excel_dir"])

    ok_src = sum(1 for s in statuses if s["ok"])
    covered = sum(1 for d in domain_rows if d["count"])
    print(f"\n[done] 源 {ok_src}/{len(statuses)} 成功 | 当月 {len(uniq)} 条 | "
          f"领域覆盖 {covered}/27 | 部委有独立发文 {sum(1 for m in ministries if m['count'])}/8")
    print(f"[done] Excel: {xlsx.name}")
    return payload


if __name__ == "__main__":
    main()
