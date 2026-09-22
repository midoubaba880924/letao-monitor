# -*- coding: utf-8 -*-
"""
乐淘一番关键词上新监控 v4（GitHub Actions）
- 仅推送监控启动后真正新上架的商品（ID去重 + 24h推送去重双保险）
- 防风控：抓取结果为空/数量骤减/单轮新增异常多 → 判定异常，保持原状态不推送
- 排序: 显式 ?sort=created_time（乐淘透传 Mercari updated_at），≥100条自动补抓第2页
环境变量: LETAO_TOKEN / MONITOR_KEYWORD / STATE_DIR / TEST_ITEM(可选,强制推送指定商品)
"""
import json, re, os, sys, time, urllib.request, ssl
from datetime import datetime, timezone, timedelta
import notify as notifier   # 多渠道告警（send_alert）

TZ_BEIJING = timezone(timedelta(hours=8))  # 服务器是UTC，固定换算北京时间

COOKIE_TOKEN = os.environ.get("LETAO_TOKEN", "")
KEYWORD = os.environ.get("MONITOR_KEYWORD", "cotopaxi")
PLATFORMS = ["mercari"]
STATE_DIR = os.environ.get("STATE_DIR", os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(STATE_DIR, "seen_items.json")
ALERT_LOG = os.path.join(STATE_DIR, "alerts.log")
ALERT_JSON = os.path.join(STATE_DIR, "alerts.json")
BASE = "https://letaoyifan.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"
PUSH_DEDUP_HOURS = 24   # 同一商品24小时内不重复推送
ANOMALY_NEW_LIMIT = 15  # 单轮新增超过此数 → 判定状态异常，不推送
# 只推包类（用户要求：衣服裤子不推）。命中任一关键词才推送
# 日文包类词 + Cotopaxi 英文型号/包型词：Allpa/Batac/KAPAI 等标题常只写英文型号，不加会漏推核心背包
BAG_KEYWORDS = ["バックパック", "リュック", "バッグ", "かばん", "カバン", "鞄",
                "ボディバッグ", "ウエストポーチ", "ウェストバッグ", "ポーチ",
                "ショルダー", "トート", "ダッフル", "ハンドバッグ", "ボストン", "ゲートル",
                "サコッシュ", "ヒップパック",
                "allpa", "batac", "kapai", "hip pack", "backpack", "duffel", "tote", "saccoche",
                "elqui", "luzon", "sling", "waist pack", "accessory bag"]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

ITEM_RE = re.compile(r'href="/goods_detail/([A-Za-z0-9-]+)/([A-Za-z0-9]+)"')

def fetch(url, retries=2):
    """带完整浏览器头 + 失败退避重试（10s/20s），应对瞬时风控拦截"""
    headers = {
        "Cookie": f"Authori-zation={COOKIE_TOKEN}; lang=chs",
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8",
        "Referer": f"{BASE}/",
        "Origin": BASE,
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Connection": "keep-alive",
    }
    delays = [0, 10, 20]
    last_err = None
    for attempt in range(retries + 1):
        if delays[attempt]:
            time.sleep(delays[attempt])
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last_err = e
    raise last_err

def _extract(url):
    html = fetch(url)
    if "goods_detail" not in html:
        return None  # 登录页/验证码/异常页
    seen, out = set(), []
    for plat, iid in ITEM_RE.findall(html):
        if iid not in seen:
            seen.add(iid); out.append(iid)
    return out

def parse_search(platform):
    base = f"{BASE}/good_cate/{platform}/{KEYWORD}"
    ids = _extract(base + "?sort=created_time")
    if ids is None:
        return None
    if len(ids) >= 100:  # 防分页漏单
        more = _extract(base + "?sort=created_time&page=2")
        if more:
            ids += [i for i in more if i not in ids]
    return ids

def enrich(platform, iid, stamp):
    """Node 精确解析 NUXT 主商品数据"""
    info = {"platform": platform, "id": iid, "title": "", "price": "", "cny": "",
            "cond": "", "img": "", "content": "", "first_seen": stamp}
    try:
        import subprocess
        js = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nuuxt_extract.js")
        r = subprocess.run(["node", js, platform, iid], capture_output=True,
                           text=True, timeout=70, env={**os.environ})
        d = json.loads(r.stdout.strip().splitlines()[-1])
        for k in ("title", "price", "cny", "cond", "content"):
            info[k] = d.get(k, "")
        info["img"] = d.get("image", "")
        if d.get("sync_pending"):
            info["title"] = info["title"] or "（详情同步中，点击链接查看）"
    except Exception as e:
        info["err"] = str(e)[:60]
        info["title"] = info["title"] or "（详情同步中，点击链接查看）"
    info["pc"] = f"{BASE}/goods_detail/{platform}/{iid}"
    info["h5"] = f"https://mall.leyifan.com/#/subPackages/pagesOther/goods_details/index?id={iid}&curSiteValue={platform}"
    return info

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
        # 兼容v3旧结构
        if "platforms" not in s:
            s = {"platforms": {k: v for k, v in s.items() if isinstance(v, list)}, "pushed": {}}
        # 兼容旧 pushed 结构（纯时间戳数字 → dict 记录 {t, price}，便于价格对比）
        pushed = {}
        for k, v in (s.get("pushed") or {}).items():
            if isinstance(v, dict):
                pushed[k] = v
            else:
                pushed[k] = {"t": float(v), "price": None}
        s["pushed"] = pushed
        return s
    return {"platforms": {}, "pushed": {}}

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)

def main():
    if not COOKIE_TOKEN:
        print("[AUTH] LETAO_TOKEN 未配置"); return 2
    stamp = datetime.now(TZ_BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    state = load_state()
    plat_state = state["platforms"]
    pushed = state["pushed"]
    baseline = not plat_state
    now_ts = time.time()

    new_items, report = [], []
    test_item = os.environ.get("TEST_ITEM", "").strip()
    err_streak = state.get("err_streak", 0)          # 抓取异常（ERR）连续轮数
    enrich_fail = 0                                   # 本轮 enrich 失败的商品数
    if test_item:  # 手动测试模式
        m = re.search(r"goods_detail/([A-Za-z0-9-]+)/([A-Za-z0-9]+)", test_item)
        if not m:
            print("[TEST] TEST_ITEM 格式无效"); return 1
        new_items = [(m.group(1), m.group(2))]
        print(f"[TEST] 强制推送 {m.group(2)}")
    else:
        for p in PLATFORMS:
            try:
                ids = parse_search(p)
            except Exception as e:
                err_streak += 1
                report.append(f"[ERR] {p}: {e}"); continue
            if ids is None or len(ids) == 0:
                # 风控页/异常页：绝不清空状态；连续被拦则多渠道告警（6h节流）
                state["suspect_streak"] = state.get("suspect_streak", 0) + 1
                report.append(f"[SUSPECT] {p}: 返回异常(空/验证码)，保持原状态不推送（连续第{state['suspect_streak']}轮）")
                if state["suspect_streak"] >= 2 and now_ts - float(state.get("last_risk_warn", 0)) > 6 * 3600:
                    ok_chan = notifier.send_alert(
                        "cotopaxi 监控被风控拦截",
                        f"连续 {state['suspect_streak']} 轮抓取被乐淘验证码拦截，期间上新可能漏报。\n\n系统每5分钟自动重试，恢复后自动继续。")
                    if ok_chan:
                        state["last_risk_warn"] = now_ts
                        report.append(f"[WARN] 已发送风控告警（{'/'.join(ok_chan)}）")
                    else:
                        report.append("[WARN] 风控告警发送失败（无可用渠道）")
                continue
            state["suspect_streak"] = 0  # 成功一轮即清零
            err_streak = 0               # 抓取成功即清零
            old = set(plat_state.get(p, []))
            new = [i for i in ids if i not in old and i not in pushed]
            if not baseline and len(new) > ANOMALY_NEW_LIMIT:
                # 单轮新增异常多 → 状态异常，不推送，状态取并集防丢失
                plat_state[p] = sorted(set(old) | set(ids))
                report.append(f"[ANOMALY] {p}: 单轮新增{len(new)}条超出阈值，判定异常不推送，状态已并集保护")
                continue
            plat_state[p] = ids
            report.append(f"[OK] {p}: 在售 {len(ids)}，新增 {len(new)}")
            new_items += [(p, i) for i in new]

    # 抓取连续失败 >= 3 轮 → 多渠道告警 + 非 0 退出（让 run_loop/watchdog 感知并自动重试）
    state["err_streak"] = err_streak
    if err_streak >= 3:
        if now_ts - float(state.get("last_alert_warn", 0)) > 6 * 3600:
            notifier.send_alert(
                "cotopaxi 监控抓取持续失败",
                f"连续 {err_streak} 轮抓取乐淘失败（token 失效/网络异常/页面改版），期间上新无法监控。\n\n系统会自动重试，若持续请检查 LETAO_TOKEN。")
            state["last_alert_warn"] = now_ts
        save_state(state)
        print("\n".join(report))
        return 1

    save_state(state)
    print(f"=== letao monitor {stamp} baseline={baseline} ===")

    if new_items:
        import concurrent.futures
        items = []
        cands = new_items[:30]
        # 并发 enrich（4 线程）：及时性第一。价格对比/白名单判断都需要详情，
        # 串行会拖慢整轮（最坏逼近超时），并发按输入顺序收集结果，行为不变但更快
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            infos = list(ex.map(lambda t: (t, enrich(*t, stamp)), cands))
        for (p, i), info in infos:
            if info.get("err"):
                enrich_fail += 1
            # 品类过滤：只推包类，衣服/裤子/鞋帽等不推（大小写不敏感，英文型号也能命中）
            if not any(k.lower() in info["title"].lower() for k in BAG_KEYWORDS):
                note = f" [enrich失败: {info['err'][:40]}]" if info.get("err") else ""
                report.append(f"[FILTER] 跳过非包类: {info['title'][:30]}{note}")
                continue
            key = f"{p}:{i}"
            old_rec = pushed.get(key)
            old_ts = old_rec["t"] if isinstance(old_rec, dict) else float(old_rec or 0)
            old_price = old_rec.get("price") if isinstance(old_rec, dict) else None
            new_price = info.get("price", "")
            # 调价判定：历史有价格且与本轮不同 → 标记调价重推（用户认可的行为）
            price_changed = bool(old_price and new_price and old_price != new_price)
            # 推送条件：从未推过 / 距上次推送超过24h / 24h内但价格发生变化
            if old_ts and (now_ts - old_ts) <= PUSH_DEDUP_HOURS * 3600 and not price_changed:
                report.append(f"[DEDUP] 跳过 {i}（24h内已推送且价格未变）")
                continue
            info["push_type"] = "price" if price_changed else "new"
            if price_changed:
                info["old_price"] = old_price
            items.append(info)
            pushed[key] = {"t": now_ts, "price": new_price}
            tag = "（调价）" if price_changed else ""
            print(f"  + [{p}] {info['title'][:40]} | {info['price']}日元 约¥{info['cny']}{tag}")
        # 清理7天前的推送记录
        for k in list(pushed):
            if now_ts - float(pushed[k]["t"]) > 7 * 86400:
                del pushed[k]
        # enrich 连续失败 >= 3 轮 → 多渠道告警（详情解析失败会因标题占位被 FILTER，造成漏推）
        state["enrich_fail_streak"] = (state.get("enrich_fail_streak", 0) + 1) if enrich_fail else 0
        if state["enrich_fail_streak"] >= 3 and now_ts - float(state.get("last_alert_warn", 0)) > 6 * 3600:
            notifier.send_alert(
                "cotopaxi 商品详情解析持续失败",
                f"连续 {state['enrich_fail_streak']} 轮有商品详情解析失败，可能导致包类新品被误过滤漏推。\n\n请检查 nuuxt_extract.js 或乐淘页面结构。")
            state["last_alert_warn"] = now_ts
        # 关键：pushed（含价格历史）必须持久化，否则跨轮无法对比价格
        save_state(state)
        payload = {"time": stamp, "items": items}
        with open(ALERT_JSON, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        lines = [f"{stamp} 上新 {len(items)} 条:"]
        for it in items:
            tag = "[调价]" if it.get("push_type") == "price" else "[新上架]"
            price_info = f"{it.get('old_price','?')}→{it['price']}日元" if it.get("push_type") == "price" else f"{it['price']}日元"
            lines.append(f"  [{it['platform']}]{tag} {it['title'][:40]} {price_info} {it['pc']}")
        with open(ALERT_LOG, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
        print("\n".join(lines))
    elif baseline and not test_item:
        print("baseline done")
        if os.path.exists(ALERT_JSON): os.remove(ALERT_JSON)
    else:
        print("no new items")
        if os.path.exists(ALERT_JSON): os.remove(ALERT_JSON)

    # 统一在末尾打印完整报告（含 FILTER/DEDUP），避免过滤记录不可见
    print("\n".join(report))
    return 0

if __name__ == "__main__":
    sys.exit(main())
