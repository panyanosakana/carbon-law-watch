#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
双碳法规信息自动收集引擎
========================
输入：config.yaml（信息源 + 关键词 + 当月过滤）
输出：data/regulations.json + output/双碳法规汇总-YYYY-MM.xlsx

流程：
  1. 逐个信息源抓取列表页（requests 或 Playwright 渲染）
  2. 通用条目识别（不依赖站点固定选择器）
  3. 标题领域关键词筛选 + 排除词过滤
  4. 并发抓取正文页 → 提取日期、文号、发文机关、核心要点（原文摘抄）
  5. 仅保留当月发布，去重排序
  6. 输出 JSON（网站用）与 Excel（交付用）
"""
import difflib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
import urllib3
import yaml
from bs4 import BeautifulSoup

urllib3.disable_warnings()

ROOT = Path(__file__).resolve().parent.parent
CST = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
# 文号：发改环资〔2026〕123号 / 工信部节〔2026〕45号 / 第12号令 / 2026年第5号公告
DOCNO_PATTERNS = [
    re.compile(r"[\u4e00-\u9fa5]{2,12}[〔\[［(]\s*20\d{2}\s*[〕\]］)]\s*\d{1,4}\s*号"),
    re.compile(r"第\s*\d{1,4}\s*号令"),
    re.compile(r"20\d{2}\s*年\s*第\s*\d{1,4}\s*号(?:公告)?"),
    re.compile(r"20\d{2}\s*年第\s*\d{1,4}\s*号"),
    re.compile(r"[\u4e00-\u9fa5]{2,10}(?:公告|令)\s*[（(]?20\d{2}[）)]?\s*第\s*\d{1,4}\s*号"),
]
AGENCY_TAIL = ("部", "委", "局", "厅", "委员会", "办公室", "总局", "署", "政府", "人大", "协会", "中心")

# 规范性文件的标题特征（用于剔除新闻稿）
DOC_KEYS = ("通知", "公告", "通告", "办法", "规定", "条例", "意见", "方案", "规划", "细则",
            "规则", "指南", "目录", "清单", "决定", "批复", "征求意见", "草案", "导则",
            "指引", "实施方案", "管理办法", "暂行办法", "申报", "公示", "名单", "征集",
            "试点", "名录", "标准")
NEWS_KEYS = ("启动", "举行", "举办", "召开", "签约", "调研", "宣讲", "座谈", "再创佳绩",
             "入围", "获评", "亮相", "揭牌", "成功举办", "顺利", "赴", "考察", "培训会",
             "现场会", "工作会议", "发布会", "圆桌", "论坛", "峰会", "喜报", "捷报",
             "已制定", "累计", "突破", "一图读懂", "政策在身边")


def is_document(title: str) -> bool:
    """判断是否为法规/政策类文件标题（剔除新闻、动态、活动报道）"""
    t = title or ""
    if any(k in t for k in NEWS_KEYS):
        return False
    return any(k in t for k in DOC_KEYS)


# ---------------- 抓取层 ----------------

class Fetcher:
    """requests 会话池：部委站常需 cookie 跳转，首次失败自动重试一次。"""

    def __init__(self, timeout=25, retry=2):
        self.timeout, self.retry = timeout, retry
        self.local = threading.local()

    def session(self) -> requests.Session:
        s = getattr(self.local, "s", None)
        if s is None:
            s = requests.Session()
            s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                              "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
            self.local.s = s
        return s

    def get(self, url, referer=None, timeout=None) -> str:
        t = timeout or self.timeout
        last = None
        for i in range(self.retry):
            try:
                r = self.session().get(url, timeout=t, verify=False,
                                       headers={"Referer": referer or url})
                if r.status_code == 200 and len(r.text) > 500:
                    if not r.encoding or r.encoding.lower() == "iso-8859-1":
                        r.encoding = r.apparent_encoding or "utf-8"
                    return r.text
                last = f"HTTP {r.status_code}, {len(r.text)}B"
            except Exception as e:
                last = type(e).__name__
                time.sleep(0.8)
        raise RuntimeError(last or "empty")


class Browser:
    """Playwright 无头浏览器，用于 JS 动态渲染站点（单线程使用）。"""

    def __init__(self, wait_ms=3500):
        self.wait_ms = wait_ms
        self._pw = self._browser = None

    def start(self):
        if self._browser:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])

    def get(self, url, timeout=50) -> str:
        self.start()
        ctx = self._browser.new_context(user_agent=UA, ignore_https_errors=True,
                                        locale="zh-CN", viewport={"width": 1400, "height": 900})
        # 拦截图片/字体/媒体，显著提速并避免个别资源卡死
        ctx.route("**/*", lambda route: route.abort()
                  if route.request.resource_type in ("image", "media", "font") else route.continue_())
        pg = ctx.new_page()
        try:
            pg.goto(url, timeout=timeout * 1000, wait_until="commit")
            pg.wait_for_timeout(self.wait_ms)
            try:
                pg.mouse.wheel(0, 1500)
                pg.wait_for_timeout(900)
            except Exception:
                pass
            return pg.content()
        finally:
            ctx.close()

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass


# ---------------- 解析层 ----------------

def clean(s: str) -> str:
    s = re.sub(r"[\s\u3000\xa0]+", " ", s or "").strip()
    s = re.sub(r"^[•·・\-—*>◆■●○\d]+[、.．]?\s*", "", s)   # 去列表符号/序号
    s = re.sub(r"\s*[\[【(（]\d{1,2}[-/]\d{1,2}[\]】)）]\s*$", "", s)  # 去尾部日期标记
    s = re.sub(r"\s+20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*$", "", s)      # 去尾部完整日期
    return s.strip()


def normalize_title(t: str) -> str:
    """清理列表页拼接进标题的文号、日期等噪声"""
    t = clean(t)
    t = re.sub(r"[（(]\s*[\u4e00-\u9fa5]{2,12}[〔\[［]\s*20\d{2}\s*[〕\]］)]\s*\d{1,4}\s*号\s*[）)]?$", "", t)
    t = re.sub(r"\s*[〔\[［]\s*20\d{2}\s*[〕\]］]\s*\d{1,4}\s*号\s*[）)]?$", "", t)
    t = re.sub(r"\s+20\d{2}年第\s*\d+\s*号$", "", t)
    t = re.sub(r"[（(]\s*$", "", t)
    return t.strip()


def parse_date(text: str) -> str:
    m = DATE_RE.search(text or "")
    if not m:
        return ""
    y, mo, d = m.groups()
    try:
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    except ValueError:
        return ""


def extract_candidates(html: str, base_url: str) -> list[dict]:
    """通用列表条目识别：li/tr 内的首个链接 + 其上下文中的日期。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    items, seen = [], set()
    nodes = soup.find_all(["li", "tr", "dd", "p", "div"])
    for node in nodes:
        a = node.find("a", href=True)
        if not a:
            continue
        title = normalize_title(a.get_text(" "))
        if len(title) < 10:
            continue
        href = a["href"].strip()
        if href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        ctx = clean(node.get_text(" "))
        date = parse_date(ctx) or parse_date(title)
        url = urljoin(base_url, href)
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        items.append({"title": title, "url": url, "date_hint": date})
    return items


NAV_KEYS = ("政策文件", "政策法规", "规范性文件", "法规文件", "文件发布",
            "通知公告", "政策发布", "政务公开", "政策解读", "最新文件", "工信动态")


def discover_columns(html: str, base_url: str, limit: int = 3) -> list[str]:
    """从首页导航中自动发现「政策/文件/公告」类栏目地址"""
    from urllib.parse import urlparse
    host = urlparse(base_url).netloc
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        text = clean(a.get_text(" "))
        if not (2 <= len(text) <= 12):
            continue
        if not any(k in text for k in NAV_KEYS):
            continue
        u = urljoin(base_url, a["href"])
        if urlparse(u).netloc != host or u.rstrip("/") == base_url.rstrip("/"):
            continue
        if any(u.rstrip("/") == x.rstrip("/") for x in out):
            continue
        out.append(u)
        if len(out) >= limit:
            break
    return out


def article_text(html: str) -> tuple[str, str]:
    """提取正文纯文本 + 页面中的日期线索。容器识别失败时退回全文并按行清洗。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "form", "iframe",
                     "noscript", "aside"]):
        tag.decompose()
    body = None
    for sel in ["div.article", "div.TRS_Editor", "div#content", "div.content",
                "div.article-content", "div#zoom", "div.view", "div.article_con",
                "div.news_content", "div#article", "article", "div.main", "div.detail"]:
        cand = soup.select_one(sel)
        if cand and len(cand.get_text(strip=True)) > 200:
            body = cand
            break
    if body is None:
        body = soup.body or soup

    lines = []
    for ln in body.get_text("\n").split("\n"):
        s = clean(ln)
        if len(s) < 8:
            continue
        if re.search(r"(当前位置|您现在的位置|首页\s*[>»]|字体[：:]|打印本页|关闭窗口|"
                     r"扫一扫|版权所有|网站地图|ICP备|主办单位|技术支持|分享到)", s):
            continue
        lines.append(s)
    text = "\n".join(lines)
    return text, text[:3000]


def find_doc_no(text: str, year: str) -> str:
    """只在正文开头查找当年文号，且排除引用他文（如请示编号）的情况。"""
    head = (text or "")[:1500]
    for pat in DOCNO_PATTERNS:
        for m in pat.finditer(head):
            val = clean(m.group(0))
            if year not in val:                     # 只认当年文号
                continue
            before = head[max(0, m.start() - 90):m.start()]
            if any(k in before for k in ("请示", "申请", "依据", "按照", "根据", "参照", "执行")):
                continue                            # 多为引用他文
            return val
    return ""


def guess_org(title: str, default_org: str) -> str:
    """从标题前缀识别发文机关；识别不出则用信息源机关（不从文号推断，避免误判）"""
    t = clean(title)
    for kw in ("关于印发", "关于公布", "关于发布", "关于", "印发"):
        i = t.find(kw)
        if i > 3:
            head = re.sub(r"^(中华人民共和国)", "", t[:i]).strip()
            if 2 <= len(head) <= 26 and any(head.endswith(x) for x in AGENCY_TAIL):
                # 「市工信局」这类简称改为信息源全称
                if len(head) <= 9 and re.match(r"^[省市县区]", head) and default_org:
                    return default_org
                return head
            break
    return default_org


def guess_type(title: str) -> str:
    t = title
    if any(k in t for k in ("征求意见稿", "草案", "征求意见")):
        return "草案/征求意见"
    if any(k in t for k in ("标准", "技术规范", "核算规则", "指南")):
        return "标准/规范"
    if any(k in t for k in ("办法", "规定", "条例", "细则", "规则", "决定", "令")):
        return "规章"
    if any(k in t for k in ("规划", "方案", "意见", "通知", "公告", "通告", "批复")):
        return "政策文件"
    return "其他"


POINT_WEIGHTS = {
    "目标": 3, "任务": 3, "到20": 2, "要求": 2, "范围": 3, "适用": 3,
    "支持": 2, "补贴": 3, "补助": 3, "奖励": 3, "惩罚": 3, "处罚": 3,
    "考核": 2, "责任": 2, "不得": 3, "禁止": 3, "应当": 2, "鼓励": 2,
    "推广": 2, "比例": 2, "达到": 2, "不低于": 3, "以上": 1, "万元": 2,
    "吨": 2, "千瓦": 2, "证书": 2, "交易": 2, "核查": 2, "监管": 2,
}


def extract_points(text: str, keyword_pool: list[str], limit: int = 5, title: str = "") -> list[str]:
    """从原文直接摘抄 3-5 个核心要点句（不改写、不推断，保持客观中立）"""
    raw = re.split(r"[。；;\n]+", text)
    scored = []
    for seg in raw:
        s = clean(seg)
        s = re.sub(r"^[一二三四五六七八九十]+[、.]\s*", "", s)
        s = re.sub(r"^[（(][一二三四五六七八九十\d]+[）)]\s*", "", s)
        if not (26 <= len(s) <= 170):
            continue
        if re.search(r"(附件|点击|下载|打印|关闭|分享|上一篇|下一篇|扫一扫|网站地图|"
                     r"版权所有|你(公司|单位|局|厅)|收悉|请示|特此|此复|抄送)", s):
            continue
        # 收件人抬头、纯机构罗列、文件清单
        if s.endswith(("：", ":")) or re.search(r"\.(pdf|docx?|xlsx?|zip|wps)\b", s, re.I):
            continue
        if re.match(r"^(各省|各地区|各有关|国务院各|各省级|自治区、直辖市)", s):
            continue
        if len(re.findall(r"[、，]", s)) >= 5 and not re.search(r"(要求|目标|任务|标准|比例|达到)", s):
            continue
        if re.match(r"^(年|月|日|亿|万|千|个|家|项|条|吨|千瓦|兆瓦)", s):   # 半句残片
            continue
        if s.count("《") != s.count("》"):      # 书名号不完整（多为附件名截断）
            continue
        # 与标题高度重复的句子不算要点
        if title:
            tt = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", title)
            ss = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", s)
            if tt and difflib.SequenceMatcher(None, ss, tt).ratio() > 0.72:
                continue
        score = sum(w for k, w in POINT_WEIGHTS.items() if k in s)
        if any(k in s for k in keyword_pool):
            score += 2
        if re.search(r"\d+(?:\.\d+)?\s*(?:%|％|万吨|亿元|万元|千瓦|亿千瓦时|家|个)", s):
            score += 2
        scored.append((score, s))

    scored.sort(key=lambda x: -x[0])
    picked, sigs = [], []
    # 第一轮：强要点
    for sc, s in scored:
        if sc < 3:
            continue
        sig = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", s)[:24]
        if any(sig[:14] == p[:14] for p in sigs):
            continue
        sigs.append(sig)
        picked.append(s if len(s) <= 125 else s[:123] + "…")
        if len(picked) >= limit:
            return picked
    # 第二轮：不足 3 条时放宽，补齐实质性句子
    for sc, s in scored:
        if len(picked) >= 3:
            break
        if any(s.startswith(p[:12]) for p in picked):
            continue
        if not re.search(r"(应当|不得|要求|目标|范围|支持|鼓励|项目|企业|单位|标准|比例|达到)", s):
            continue
        sig = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", s)[:24]
        if any(sig[:14] == p[:14] for p in sigs):
            continue
        sigs.append(sig)
        picked.append(s if len(s) <= 125 else s[:123] + "…")
    return picked


def _fmt_note(tpl: str, row: dict) -> str:
    """把接口字段填进要点模板，日期只留年月日"""
    vals = {}
    for k, v in row.items():
        s = "" if v is None else str(v)
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            s = s[:10]
        vals[k] = s
    try:
        return clean(tpl.format(**vals))
    except Exception:
        return clean(tpl)


def fetch_api_candidates(src, cfg, fetcher):
    """对接 JSON 接口型信息源（如全国人大法律草案征求意见）"""
    api = src["api"]
    raw = fetcher.get(api["list"], src.get("referer") or src["url"],
                      cfg["collect"].get("timeout", 25))
    data = json.loads(raw)
    rows = data.get(api.get("rows_key", "rows")) or []
    out = []
    for r in rows:
        title = clean(str(r.get(api.get("title_field", "title"), "")))
        if not title:
            continue
        date = str(r.get(api.get("date_field", "date"), ""))[:10]
        link = (api.get("link") or "").replace("{id}", str(r.get(api.get("id_field", "id"), "")))
        out.append({
            "title": title,
            "url": link or src["url"],
            "date_hint": date,
            "_api_note": _fmt_note(api["note"], r) if api.get("note") else "",
        })
    return out


# ---------------- 主流程 ----------------

def process_source(src, cfg, fetcher, browser):
    """抓取单个信息源，返回 (命中条目列表, 状态)"""
    name = src["name"]
    c = cfg["collect"]
    status = {"name": name, "adapter": src.get("adapter", "html"), "ok": False,
              "candidates": 0, "hit": 0, "error": ""}

    # 接口型信息源：直接读 JSON，不做页面解析
    if src.get("adapter") == "api":
        try:
            cands = fetch_api_candidates(src, cfg, fetcher)
            status["ok"] = True
        except Exception as e:
            status["error"] = f"{type(e).__name__}: {str(e)[:80]}"
            return [], status
        status["candidates"] = len(cands)
        excl = cfg.get("exclude", [])
        kw = src.get("keywords_any") or cfg["keywords"]["core"]
        picked = []
        for cd in cands:
            t = cd["title"]
            if any(x in t for x in excl):
                continue
            if not is_document(t):
                continue
            if any(k in t for k in kw):
                picked.append(cd)
        return picked[: c.get("max_candidates", 25)], status
    urls = [src["url"]] + list(src.get("urls") or [])
    cands, errors = [], []
    if src.get("discover", True):
        try:
            home = (browser.get(urls[0], timeout=50) if src.get("adapter") == "browser"
                    else fetcher.get(urls[0], src.get("referer") or urls[0], c.get("timeout", 25)))
            urls += [u for u in discover_columns(home, urls[0], c.get("discover_limit", 3))
                     if u not in urls]
        except Exception as e:
            errors.append(f"discover:{type(e).__name__}")
    for u in urls:
        try:
            html = (browser.get(u, timeout=50) if src.get("adapter") == "browser"
                    else fetcher.get(u, src.get("referer") or src["url"], c.get("timeout", 25)))
            got = extract_candidates(html, u)
            cands += got
            status["ok"] = True
        except Exception as e:
            errors.append(f"{type(e).__name__}: {str(e)[:60]}")
    status["candidates"] = len(cands)
    status["error"] = "; ".join(errors)[:160]
    if not status["ok"]:
        return [], status

    core = cfg["keywords"]["core"]
    excl = cfg.get("exclude", [])
    picked = []
    for c in cands:
        t = c["title"]
        if any(x in t for x in excl):
            continue
        if not is_document(t):
            continue
        if any(k in t for k in core):
            picked.append(c)
    picked = picked[: c.get("max_candidates", 25)]
    return picked, status


def enrich(item, src, cfg, fetcher):
    """抓正文，补齐日期/文号/机关/要点"""
    c = cfg["collect"]
    core = cfg["keywords"]["core"]
    ctx = cfg["keywords"]["context"]
    text = ""
    try:
        html = fetcher.get(item["url"], src["url"], c.get("timeout", 25))
        text, head = article_text(html)
        d = parse_date(head)
        today = datetime.now(CST).strftime("%Y-%m-%d")
        if d and d > (datetime.now(CST) + timedelta(days=7)).strftime("%Y-%m-%d"):
            d = ""                      # 未来日期多为生效日，忽略
        hint = parse_date(item.get("date_hint", ""))
        item["date"] = hint or d or ""
    except Exception:
        pass

    item["title"] = normalize_title(item["title"])
    item.setdefault("date", item.get("date_hint", ""))
    year = str(datetime.now(CST).year)
    item["doc_no"] = find_doc_no(text, year)
    item["publisher"] = guess_org(item["title"], src.get("org", src["name"]))
    item["category"] = guess_type(item["title"])
    item["region"] = src.get("region", "国家")
    item["source"] = src["name"]

    if text:
        hits = sum(1 for k in ctx if k in text[:6000])
        item["_ctx"] = hits
        item["points"] = extract_points(text, core, title=item["title"])
        if not item["points"] and re.search(r"\.(pdf|ofd|wps|docx?)\b", text, re.I):
            item["attach_only"] = True
    else:
        item["_ctx"] = 0
        item["points"] = []
    return item


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    c = cfg["collect"]
    fetcher = Fetcher(c.get("timeout", 25), c.get("retry", 2))
    browser = Browser(c.get("browser_wait", 3500))
    now = datetime.now(CST)
    month_prefix = now.strftime("%Y-%m")

    all_items, ref_items, statuses = [], [], []
    for src in cfg["sources"]:
        if src.get("enabled", True) is False:
            statuses.append({"name": src["name"], "adapter": "-", "ok": False,
                             "candidates": 0, "hit": 0,
                             "error": src.get("note", "已禁用")})
            continue
        t0 = time.time()
        picked, st = process_source(src, cfg, fetcher, browser)
        st["seconds"] = round(time.time() - t0, 1)
        print(f"[{st['adapter']:<7}] {src['name']:<12} 候选{st['candidates']:>4} 命中{len(picked):>3} "
              f"{st['seconds']}s {st['error']}")
        statuses.append(st)

        if not picked:
            continue
        if src.get("adapter") == "browser":
            # 浏览器上下文串行补正文（Playwright sync API 非线程安全）
            enriched = [enrich(dict(it), src, cfg, fetcher) for it in picked]
        else:
            with ThreadPoolExecutor(max_workers=c.get("detail_concurrency", 6)) as ex:
                enriched = list(ex.map(lambda it: enrich(dict(it), src, cfg, fetcher), picked))

        # 当月过滤（规则 0）；非当月但相关的进「近期参考」（规则 3b）
        ref_cutoff = (now - timedelta(days=150)).strftime("%Y-%m-%d")
        kept = []
        for it in enriched:
            strong = any(k in it["title"] for k in cfg["keywords"]["core"])
            if not (strong or it["_ctx"] >= 2):
                continue
            if c.get("month_only") and not it["date"].startswith(month_prefix):
                if it["date"] and it["date"] >= ref_cutoff:
                    ref_items.append(it)
                continue
            kept.append(it)
        kept = kept[: c.get("max_items_per_source", 12)]
        st["hit"] = len(kept)
        all_items += kept

    browser.close()

    # 跨源去重（归一化标题）
    uniq, seen = [], set()
    for it in all_items:
        key = re.sub(r"[\s《》〈〉（）()\[\]【】、，,：:·—\-]", "", it["title"])[:36]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    uniq.sort(key=lambda x: (x.get("date", ""), x["title"]), reverse=True)
    # 同一主题的「解读/一图读懂」若已有正式文件，则去重
    base_titles = [re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", i["title"]) for i in uniq]
    dropped = set()
    for idx, it in enumerate(uniq):
        if not any(k in it["title"] for k in ("解读", "一问一答", "答记者问")):
            continue
        core_t = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", re.sub(r"(解读|一图读懂|答记者问|一问一答)", "", it["title"]))
        for j, bt in enumerate(base_titles):
            if j != idx and len(core_t) > 10 and difflib.SequenceMatcher(None, core_t, bt).ratio() > 0.7:
                dropped.add(idx)
                break
    uniq = [i for k, i in enumerate(uniq) if k not in dropped]

    # 近期参考去重（最多 10 条）
    ref_uniq, ref_seen = [], set()
    for it in sorted(ref_items, key=lambda x: x.get("date", ""), reverse=True):
        key = re.sub(r"[\s《》〈〉（）()\[\]【】、，,：:·—\-]", "", it["title"])[:36]
        if key in ref_seen:
            continue
        ref_seen.add(key)
        ref_uniq.append({k: v for k, v in it.items() if not k.startswith("_")})
        if len(ref_uniq) >= 10:
            break

    payload = {
        "site": cfg["site"],
        "month": month_prefix,
        "reference": ref_uniq,
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(uniq),
        "dimensions": {
            "regions": sorted({i["region"] for i in uniq}),
            "publishers": sorted({i["publisher"] for i in uniq}),
            "categories": sorted({i["category"] for i in uniq}),
        },
        "sources": statuses,
        "items": [{k: v for k, v in i.items() if not k.startswith("_")} for i in uniq],
    }
    out = ROOT / cfg["output"]["json"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 单文件网页（数据内嵌，可离线打开/直接分享）
    tpl = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    marker = "<script>\nconst esc"
    if marker in tpl:
        standalone = tpl.replace(
            marker, f"<script>window.__DATA__={json.dumps(payload, ensure_ascii=False)};</script>\n" + marker)
        (ROOT / "双碳法规监测-单文件版.html").write_text(standalone, encoding="utf-8")

    # Excel 交付
    from export_excel import export
    xlsx = export(payload, ROOT / cfg["output"]["excel_dir"])

    ok_src = sum(1 for s in statuses if s["ok"])
    print(f"\n[done] 采集源 {ok_src}/{len(statuses)} 成功 | 当月命中 {len(uniq)} 条 | 近期参考 {len(ref_uniq)} 条")
    print(f"[done] JSON: {out.relative_to(ROOT)}")
    print(f"[done] Excel: {xlsx.name}")


if __name__ == "__main__":
    main()
