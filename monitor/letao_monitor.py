# -*- coding: utf-8 -*-
"""
乐淘一番关键词上新监控（GitHub Actions 云端版）
- 轮询各平台搜索页（SSR HTML，带登录 Cookie）
- 按商品唯一 ID 去重，识别新上架
- 新品写入 alerts.log；通知由 workflow 根据该文件发送
环境变量：
  LETAO_TOKEN  乐淘登录 token（32位，GitHub Secrets 配置）
  MONITOR_KEYWORD  监控关键词（默认 cotopaxi）
  STATE_DIR  状态文件目录（默认脚本所在目录）
"""
import json, re, time, os, sys, urllib.request, ssl

COOKIE_TOKEN = os.environ.get("LETAO_TOKEN", "")
KEYWORD = os.environ.get("MONITOR_KEYWORD", "cotopaxi")
PLATFORMS = ["mercari", "YAHOO-AUCTIONS", "SURUGA", "PAYPAY", "ANIMATE", "lashinbang"]
STATE_DIR = os.environ.get("STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(STATE_DIR, "seen_items.json")
ALERT_LOG = os.path.join(STATE_DIR, "alerts.log")
BASE = "https://letaoyifan.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

ITEM_RE = re.compile(r'href="/goods_detail/([A-Za-z0-9-]+)/([A-Za-z0-9]+)"')

def fetch_platform(platform):
    url = f"{BASE}/good_cate/{platform}/{KEYWORD}"
    req = urllib.request.Request(url, headers={
        "Cookie": f"Authori-zation={COOKIE_TOKEN}; lang=chs",
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
        html = r.read().decode("utf-8", "ignore")
    if "goods_detail" not in html:
        if "验证" in html or "login" in html.lower()[:2000]:
            return None  # 登录态失效
    seen, out = set(), []
    for plat, iid in ITEM_RE.findall(html):
        if iid not in seen:
            seen.add(iid)
            out.append(iid)
    return out

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)

def main():
    if not COOKIE_TOKEN:
        print("[AUTH] 未配置 LETAO_TOKEN 环境变量")
        return 2
    state = load_state()
    baseline = not state
    all_new, report, auth_fail = [], [], False
    for p in PLATFORMS:
        try:
            ids = fetch_platform(p)
        except Exception as e:
            report.append(f"[ERR] {p}: {e}")
            continue
        if ids is None:
            report.append(f"[AUTH] {p}: 登录态失效，需更新 token")
            auth_fail = True
            continue
        old = set(state.get(p, []))
        new = [i for i in ids if i not in old]
        state[p] = ids
        report.append(f"[OK] {p}: 在售 {len(ids)}，新增 {len(new)}")
        all_new += [(p, i) for i in new]
    save_state(state)

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== letao monitor {stamp} (baseline={baseline}) ===")
    print("\n".join(report))
    if auth_fail and not all_new:
        return 2
    if all_new and not baseline:
        lines = [f"{stamp}  上新 {len(all_new)} 条:"]
        for p, i in all_new:
            lines.append(f"  [{p}] {BASE}/goods_detail/{p}/{i}")
        msg = "\n".join(lines)
        print(msg)
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n\n")
    elif baseline:
        print("baseline done")
    return 0

if __name__ == "__main__":
    sys.exit(main())
