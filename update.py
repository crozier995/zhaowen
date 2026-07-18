# -*- coding: utf-8 -*-
"""朝闻 · 每日新闻抓取器
从真实 RSS 源抓取新闻，按日期归档为 data/YYYY-MM-DD.js。
摘要保持媒体原文（过长截断并标注），每条附出处链接。
已有人工评论（comment 字段）在重跑时按链接匹配合并保留。
"""
import json, os, re, sys, time, html
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)

FEEDS = {
    "36氪":   ("https://36kr.com/feed", "商业"),
    "爱范儿":  ("https://www.ifanr.com/feed", "科技"),
    "IT之家": ("https://www.ithome.com/rss/", "科技"),
    "Solidot": ("https://www.solidot.org/index.rss", "科学"),
    "界面新闻": ("https://a.jiemian.com/index.php?m=article&a=rss", "社会"),
    "BBC中文": ("https://feeds.bbci.co.uk/zhongwen/simp/rss.xml", "世界"),
    "机核":   ("https://www.gcores.com/rss", "游戏"),
    "游研社": ("https://www.yystv.cn/rss", "游戏"),
}

# 细分类：按关键词重判类别（按优先级匹配，先中先得）
CAT_RULES = [
    ("游戏", ["游戏", "PS5", "PS4", "Xbox", "Switch", "任天堂", "Steam", "电竞", "Bethesda", "暴雪", "手游", "主机", "上古卷轴", "辐射", "GTA", "塞尔达"]),
    ("体育", ["NBA", "足球", "世界杯", "奥运", "CBA", "F1", "网球", "羽毛球", "马拉松", "联赛", "球队", "球星", "季后赛", "梅西", "C罗"]),
    ("科学", ["研究", "科学家", "宇宙", "量子", "基因", "考古", "天文", "NASA", "恒星", "微塑料", "纳米", "临床试验", "疫苗", "医学", "行星", "化石"]),
    ("文化", ["电影", "票房", "音乐", "书籍", "小说", "剧集", "漫威", "诺兰", "蜘蛛侠", "专辑", "演唱会", "艺术", "文物", "禁书", "唱片", "CD"]),
    ("生活", ["健康", "饮食", "天气", "消费", "旅游", "交通", "电梯", "毒品", "电子烟", "诈骗", "睡眠", "减肥", "台风", "高温"]),
    ("商业", ["股价", "基金", "IPO", "融资", "收购", "债券", "市值", "营收", "财报", "私募", "期货", "反倾销", "涨价", "销量"]),
    ("社会", ["崩塌", "地震", "灾害", "事故", "卫健委", "公安", "法院", "判决", "救援", "失联", "台风", "洪水", "诈骗"]),
]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
WINDOW_HOURS = 40          # 只保留近 40 小时
MAX_PER_DAY = 40
MAX_PER_SRC = 12           # 每天每个源最多保留条数
SUMMARY_LEN = 300          # 摘要截断长度（截断处会标注）


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def classify(title, summary, default):
    text = (title + " " + summary)
    for cat, kws in CAT_RULES:
        if any(k in text for k in kws):
            return cat
    return default


def find_image(it, desc_html):
    """尽力从 RSS 条目里提取配图 URL"""
    url = ""
    for tag in ("{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail"):
        el = it.find(tag)
        if el is not None and el.get("url"):
            url = el.get("url")
            break
    if not url:
        for el in it.iter("enclosure"):
            if (el.get("type") or "").startswith("image") and el.get("url"):
                url = el.get("url")
                break
    if not url:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_html or "")
        if m:
            url = m.group(1)
    # 规范化：修复协议缺失/重复（如 https:https:// 或 //img.x.com）
    if url.startswith("//"):
        url = "https:" + url
    if "https://" in url:
        url = "https://" + url.split("https://")[-1]
    elif "http://" in url:
        url = "http://" + url.split("http://")[-1]
    else:
        return ""
    return url


def localize_image(url, day, idx):
    """把配图下载到本地 data/img/，返回相对路径；失败返回空"""
    if not url:
        return ""
    try:
        imgdir = os.path.join(DATA, "img")
        os.makedirs(imgdir, exist_ok=True)
        ext = os.path.splitext(url.split("?")[0])[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        fn = f"{day}_{idx:02d}{ext}"
        fp = os.path.join(imgdir, fn)
        if not os.path.exists(fp):
            r = requests.get(url, timeout=15, headers=HEADERS)
            r.raise_for_status()
            if len(r.content) < 3000:   # 太小通常是占位/错误图
                return ""
            with open(fp, "wb") as f:
                f.write(r.content)
        return f"data/img/{fn}"
    except Exception:
        return ""


def prune_images(days=10):
    """只保留最近 N 天的配图"""
    imgdir = os.path.join(DATA, "img")
    if not os.path.isdir(imgdir):
        return
    cutoff = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days)).strftime("%Y-%m-%d")
    for fn in os.listdir(imgdir):
        if fn[:10] < cutoff:
            try:
                os.remove(os.path.join(imgdir, fn))
            except OSError:
                pass


def parse_time(s):
    if not s:
        return None
    s = s.strip()
    # 命名时区（GMT/UT/UTC/Z）统一转成数字时区，避免被误当北京时间
    s = re.sub(r"\s+(GMT|UTC|UT|Z)$", " +0000", s)
    for fmt in ("%a, %d %b %Y %H:%M:%S %z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
            return dt.astimezone(timezone(timedelta(hours=8)))
        except Exception:
            continue
    return None


def fetch_feed(name, url, cat):
    try:
        r = requests.get(url, timeout=20, headers=HEADERS)
        r.raise_for_status()
    except Exception as e:
        print(f"  [跳过] {name}: {e}")
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        print(f"  [跳过] {name}: XML 解析失败")
        return []
    items = []
    # RSS 2.0
    for it in root.iter("item"):
        t = it.findtext("title") or ""
        link = it.findtext("link") or ""
        pub = it.findtext("pubDate") or it.findtext("date") or ""
        desc = it.findtext("description") or ""
        items.append((t, link, pub, desc, find_image(it, desc)))
    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for it in root.findall("a:entry", ns):
        t = it.findtext("a:title", "", ns)
        link_el = it.find("a:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        pub = it.findtext("a:published", "", ns) or it.findtext("a:updated", "", ns)
        desc = it.findtext("a:summary", "", ns) or it.findtext("a:content", "", ns)
        items.append((t, link, pub, desc, find_image(it, desc)))
    now = datetime.now(timezone(timedelta(hours=8)))
    out = []
    for t, link, pub, desc, img in items:
        dt = parse_time(pub)
        if not link.strip():
            continue
        if dt and (now - dt) > timedelta(hours=WINDOW_HOURS):
            continue
        summary = strip_html(desc)
        if len(summary) > SUMMARY_LEN:
            summary = summary[:SUMMARY_LEN].rstrip() + " …（截断，全文见原文）"
        title = strip_html(t)
        out.append({
            "t": title, "url": link.strip(), "src": name,
            "cat": classify(title, summary, cat), "ts": dt.isoformat() if dt else "",
            "sum": summary, "img": img, "comment": "",
        })
    print(f"  [OK] {name}: {len(out)} 条")
    return out


def day_of(item, now):
    dt = datetime.fromisoformat(item["ts"]) if item["ts"] else now
    return dt.strftime("%Y-%m-%d")


def main():
    now = datetime.now(timezone(timedelta(hours=8)))
    pool, seen = [], set()
    for name, (url, cat) in FEEDS.items():
        for it in fetch_feed(name, url, cat):
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            pool.append(it)
    pool.sort(key=lambda x: x["ts"], reverse=True)

    # 按天归档，并保留已有 comment
    days = {}
    for it in pool:
        days.setdefault(day_of(it, now), []).append(it)
    dates = set()
    for d, items in days.items():
        f = os.path.join(DATA, f"{d}.js")
        # 追加式归档：旧条目全部保留（含点评/精选），只补充新出现的 URL
        old_items, old_meta = [], {}
        if os.path.exists(f):
            m = re.search(r"EDITIONS\[.*?\]\s*=\s*(\{.*\})\s*;?\s*$",
                          open(f, encoding="utf-8").read(), re.S)
            if m:
                try:
                    old = json.loads(m.group(1))
                    old_items = old.get("items", [])
                    old_meta = {i["url"]: (i.get("comment", ""), i.get("pick", 0))
                                for i in old_items}
                except Exception:
                    pass
        for it in items:
            if it["url"] in old_meta:
                cmt, pk = old_meta[it["url"]]
                if cmt:
                    it["comment"] = cmt
                if pk:
                    it["pick"] = pk
        fresh_urls = {it["url"] for it in items}
        legacy = [o for o in old_items if o["url"] not in fresh_urls]
        # 每源限量只约束新抓取部分；旧条目全保留。先合并再统一排序
        per_src = {}
        for it in items:
            per_src.setdefault(it["src"], []).append(it)
        items = sorted((it for lst in per_src.values() for it in lst[:MAX_PER_SRC]),
                       key=lambda x: x["ts"], reverse=True)[:MAX_PER_DAY]
        # 有精选/点评的旧条目永远保留，哪怕本轮回源被配额挤掉
        kept = {i["url"] for i in items} | {o["url"] for o in legacy}
        must_keep = [o for o in old_items
                     if (o.get("pick") or o.get("comment")) and o["url"] not in kept]
        items = sorted(items + legacy + must_keep, key=lambda x: x.get("ts", ""), reverse=True)
        # 配图本地化（已是本地路径的跳过），远程下载失败的保留原 URL 由前端兜底
        for n, it in enumerate(items):
            if it.get("img", "").startswith("http"):
                local = localize_image(it["img"], d, n)
                if local:
                    it["img"] = local
        payload = {"date": d,
                   "updated": now.strftime("%H:%M"),
                   "items": items}
        with open(f, "w", encoding="utf-8") as fp:
            fp.write(f'window.EDITIONS=window.EDITIONS||{{}};EDITIONS["{d}"]=')
            json.dump(payload, fp, ensure_ascii=False, indent=1)
            fp.write(";\n")
        dates.add(d)
        print(f"  [归档] {d}: {len(items)} 条")

    # 归档索引（含历史）
    for fn in os.listdir(DATA):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.js", fn):
            dates.add(fn[:-3])
    idx = sorted(dates, reverse=True)
    with open(os.path.join(DATA, "index.js"), "w", encoding="utf-8") as fp:
        fp.write("window.ARCHIVE=" + json.dumps(idx) + ";\n")
    prune_images()
    print(f"完成，共 {len(idx)} 天。今天 {now.strftime('%Y-%m-%d')} 的新闻已更新。")


if __name__ == "__main__":
    main()
