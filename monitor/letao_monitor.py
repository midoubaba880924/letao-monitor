# -*- coding: utf-8 -*-
"""
乐淘一番关键词上新监控（GitHub Actions 云端版 v2）
- 轮询各平台搜索页（SSR HTML，带登录 Cookie）
- 按商品唯一 ID 去重，识别新上架
- 新品抓详情页提取: 标题/价格/人民币估价/成色/图片
- 生成 alerts.json（结构化）+ alerts.log（纯文本），通知由 notify.py 发送
环境变量：
  LETAO_TOKEN  乐淘登录 token（GitHub Secrets）
  MONITOR_KEYWORD  监控关键词（默认 cotopaxi）
  STATE_DIR  状态文件目录
  TEST_ITEM  可选，goods_detail 完整URL，用于手动测试推送
"""
import json, re, os, sys, time, urllib.request, ssl

COOKIE_TOKEN = os.environ.get("LETAO_TOKEN", "")
KEYWORD = os.environ.get("MONITOR_KEYWORD", "cotopaxi")
PLATFORMS = ["mercari"]
STATE_DIR = os.environ.get("STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(STATE_DIR, "seen_items.json")
ALERT_LOG = os.path.join(STATE_DIR, "alerts.log")
ALERT_JSON = os.path.join(STATE_DIR, "alerts.json")
BASE = "https://letaoyifan.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

ITEM_RE = re.compile(r'href="/goods_detail/([A-Za-z0-9-]+)/([A-Za-z0-9]+)"')
CONDS = ["新,未使用", "接近未使用", "无明显划痕或污垢", "有一些划痕或污垢", "有划痕和污垢", "状态差", "脏污"]

def fetch(url):
    req = urllib.request.Request(url, headers={
        "Cookie": f"Authori-zation={COOKIE_TOKEN}; lang=chs",
        "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        return r.read().decode("utf-8", "ignore")

def parse_search(platform):
    base = f"{BASE}/good_cate/{platform}/{KEYWORD}"
    # sort=created_time: 强制按最新上架排序（实测默认已是最新序，此参数免疫默认排序变更）
    ids = _extract(base + "?sort=created_time")
    # 防分页漏单：接近单页上限时补抓第2页
    if len(ids) >= 100:
        ids += _extract(base + "?sort=created_time&page=2")
    return ids

def _extract(url):
    html = fetch(url)
    seen, out = set(), []
    for plat, iid in ITEM_RE.findall(html):
        if iid not in seen:
            seen.add(iid); out.append(iid)
    return out

def enrich(platform, iid):
    """抓详情页提取富信息"""
    info = {"platform": platform, "id": iid, "title": "", "price": "", "cny": "", "cond": "", "img": ""}
    try:
        h = fetch(f"{BASE}/goods_detail/{platform}/{iid}")
        t = re.search(r"<title>([^<]+)</title>", h)
        if t:
            info["title"] = re.sub(r"\s*-\s*[A-Za-z ]*browserHeadText.*$", "", t.group(1)).strip()
        m = re.search(r'price:"([\d,]+)"', h)
        if m: info["price"] = m.group(1)
        m = re.search(r'convertPrice:"([\d.]+)"', h)
        if m: info["cny"] = m.group(1)
        for c in CONDS:
            if c in h: info["cond"] = c; break
        m = re.search(r'image:"(https:[^"]+?)"', h)
        if m: info["img"] = m.group(1).replace("\\u002F", "/")
    except Exception as e:
        info["err"] = str(e)[:60]
    info["pc"] = f"{BASE}/goods_detail/{platform}/{iid}"
    info["h5"] = f"https://mall.leyifan.com/#/subPackages/pagesOther/goods_details/index?id={iid}&curSiteValue={platform}"
    return info

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)

def main():
    if not COOKIE_TOKEN:
        print("[AUTH] LETAO_TOKEN 未配置"); return 2
    state = load_state()
    baseline = not state
    new_ids, report, auth_fail = [], [], False

    test_item = os.environ.get("TEST_ITEM", "").strip()
    if test_item:  # 手动测试模式
        m = re.search(r"goods_detail/([A-Za-z0-9-]+)/([A-Za-z0-9]+)", test_item)
        if m:
            new_ids = [(m.group(1), m.group(2))]
            print(f"[TEST] 强制推送 {m.group(2)}")
        else:
            print("[TEST] TEST_ITEM 格式无效"); return 1
    else:
        for p in PLATFORMS:
            try:
                ids = parse_search(p)
            except Exception as e:
                report.append(f"[ERR] {p}: {e}"); continue
            if not ids and "goods_detail" not in ids and p == "ANIMATE":
                pass
            old = set(state.get(p, []))
            new = [i for i in ids if i not in old]
            state[p] = ids
            report.append(f"[OK] {p}: 在售 {len(ids)}，新增 {len(new)}")
            new_ids += [(p, i) for i in new]
        if all(r.startswith("[AUTH]") for r in report) and report:
            auth_fail = True

    save_state(state)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== letao monitor {stamp} baseline={baseline} ===")
    print("\n".join(report))

    if new_ids and not baseline or test_item:
        items = []
        for p, i in new_ids[:30]:  # 上限30条/轮防极端刷屏；notify 分批全量推送不漏
            info = enrich(p, i)
            items.append(info)
            print(f"  + [{p}] {info['title'][:40]} | {info['price']}日元 约¥{info['cny']}")
        if len(new_ids) > 30:  # 超出部分记录链接到日志兜底
            with open(ALERT_LOG, "a", encoding="utf-8") as f:
                for p, i in new_ids[30:]:
                    f.write(f"{stamp} 超量 [{p}] {BASE}/goods_detail/{p}/{i}\n")
        payload = {"time": stamp, "items": items}
        with open(ALERT_JSON, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        lines = [f"{stamp} 上新 {len(items)} 条:"]
        for it in items:
            lines.append(f"  [{it['platform']}] {it['title'][:40]} {it['price']}日元 {it['pc']}")
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
        print("\n".join(lines))
    elif baseline:
        print("baseline done")
        if os.path.exists(ALERT_JSON): os.remove(ALERT_JSON)
    else:
        print("no new items")
        if os.path.exists(ALERT_JSON): os.remove(ALERT_JSON)

    if auth_fail and not new_ids:
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
