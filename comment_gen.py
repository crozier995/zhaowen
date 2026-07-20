# -*- coding: utf-8 -*-
"""朝闻 · 云端点评生成器（GitHub Actions 用）
调用 Moonshot Kimi API 为当日新闻生成精选与深刻点评。
无 MOONSHOT_API_KEY 时静默跳过（退出码 0），不影响主流程。
"""
import json, os, re, sys
from datetime import datetime, timedelta, timezone
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
API = "https://api.kimi.com/coding/v1/messages"   # Kimi Code 开放平台（会员额度内）

PROMPT = """你是「朝闻」新闻日报的主编。下面是今天抓取的新闻候选（JSON 数组，含标题/来源/分类/摘要）。
请完成两步工作：
1. 从中精选 12~14 条：剔除纯宏观政治、IPO/财报快讯类；优先科技、科学、游戏、文化、社会生活、消费数码，注意覆盖多个分类。
2. 为每条精选写 3~4 句中文「深刻版」点评：不要停留在"发生了什么"，要回答"为什么重要"——指出背后的趋势、利益结构、人性规律或第二层影响；观点鲜明、判断诚实。

严格只输出 JSON 数组（不要输出任何其他文字），格式：
[{"url": "原条目的 url", "pick": 1, "comment": "你的点评"}, ...]
pick 从 1 开始（1 为头条）。url 必须原样照抄候选条目里的值，不得改写。
"""


def main():
    key = os.environ.get("MOONSHOT_API_KEY", "").strip()
    if not key:
        print("未配置 MOONSHOT_API_KEY，跳过点评生成（新闻照常更新）")
        return 0
    today = os.environ.get("ZW_DATE") or datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    fp = os.path.join(DATA, f"{today}.js")
    if not os.path.exists(fp):
        print(f"今日数据文件不存在: {fp}")
        return 0
    raw = open(fp, encoding="utf-8").read()
    m = re.search(r"(EDITIONS\[.*?\]\s*=\s*)(\{.*\})\s*;\s*$", raw, re.S)
    d = json.loads(m.group(2))
    items = d.get("items", [])
    # 已有足够点评则跳过（避免覆盖人工/往期策划）
    if sum(1 for i in items if i.get("comment")) >= 10:
        print("今日已有点评，跳过")
        return 0

    cand = [{"url": i["url"], "src": i["src"], "cat": i["cat"],
             "t": i["t"], "sum": i.get("sum", "")[:220]} for i in items]
    body = {
        "model": "k3",
        "max_tokens": 4096,
        "system": PROMPT,
        "messages": [
            {"role": "user", "content": json.dumps(cand, ensure_ascii=False)},
        ],
    }
    try:
        r = requests.post(API, json=body, timeout=180,
                          headers={"x-api-key": key,
                                   "anthropic-version": "2023-06-01",
                                   "Content-Type": "application/json"})
        r.raise_for_status()
        blocks = r.json().get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    except Exception as e:
        print(f"API 调用失败（不影响新闻更新）: {e}")
        return 0

    m2 = re.search(r"\[.*\]", text, re.S)
    if not m2:
        print("API 返回格式异常，跳过")
        return 0
    try:
        picks = json.loads(m2.group(0))
    except Exception:
        print("API 返回 JSON 解析失败，跳过")
        return 0

    by_url = {p.get("url"): p for p in picks if p.get("url") and p.get("comment")}
    n = 0
    for it in items:
        p = by_url.get(it["url"])
        if p:
            it["pick"] = int(p.get("pick", 99))
            it["comment"] = str(p["comment"]).strip()
            n += 1
    items.sort(key=lambda x: (0 if x.get("pick") else 1, x.get("pick", 999)))
    d["items"] = items
    with open(fp, "w", encoding="utf-8") as f:
        f.write(f'window.EDITIONS=window.EDITIONS||{{}};EDITIONS["{today}"]=')
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write(";\n")
    print(f"Kimi API 点评完成: {n} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
