# -*- coding: utf-8 -*-
"""读取 alerts.json，组装富卡片推送（商品图+价格+直达链接）"""
import os, sys, json, urllib.request

def build_html(items):
    cards = []
    for it in items[:5]:
        img = f'<img src="{it["img"]}" width="180" style="border-radius:8px"/>' if it.get("img") else ""
        price = f'{it["price"]} 日元' if it.get("price") else "见详情"
        cny = f'（约 ¥{it["cny"]}）' if it.get("cny") else ""
        cond = f'🏷️ 成色：{it["cond"]}<br/>' if it.get("cond") else ""
        cards.append(f"""
<div style="border:1px solid #eee;border-radius:10px;padding:12px;margin:10px 0;font-size:14px">
{img}
<p style="margin:8px 0"><b>🆕 {it.get("title","(标题解析失败)")}</b></p>
<p style="margin:4px 0">💰 <b>{price}</b> {cny}<br/>{cond}🛒 平台：{it["platform"]}</p>
<p style="margin:8px 0">
📱 <a href="{it['h5']}">手机购买页</a> ｜ 💻 <a href="{it['pc']}">电脑页</a>
</p></div>""")
    footer = '<p style="color:#888;font-size:12px">GitHub 云端监控 · 每30分钟检查6个平台</p>'
    return "".join(cards) + footer

def send_wxpusher_html(html, summary):
    body = json.dumps({
        "appToken": os.environ["WXPUSHER_TOKEN"],
        "summary": summary[:20],
        "content": html, "contentType": 2,
        "uids": json.loads(os.environ.get("WXPUSHER_UIDS", "[]")),
    }).encode()
    req = urllib.request.Request("https://wxpusher.zjiecode.com/api/send/message",
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    print("wxpusher:", d.get("code"), d.get("msg"))

def send_text_fallback(alert):
    """其他渠道走纯文本"""
    if os.environ.get("BARK_URL"):
        u = os.environ["BARK_URL"].rstrip("/") + "/" + urllib.parse.quote("cotopaxi上新") + "/" + urllib.parse.quote(alert[:150])
        urllib.request.urlopen(urllib.request.Request(u), timeout=15); print("bark sent")
    if os.environ.get("WECOM_WEBHOOK"):
        b = json.dumps({"msgtype": "text", "text": {"content": "cotopaxi 上新\n" + alert}}).encode()
        urllib.request.urlopen(urllib.request.Request(os.environ["WECOM_WEBHOOK"], data=b, headers={"Content-Type": "application/json"}), timeout=15)
        print("wecom sent")
    if os.environ.get("DING_WEBHOOK"):
        b = json.dumps({"msgtype": "text", "text": {"content": "cotopaxi 上新\n" + alert}}).encode()
        urllib.request.urlopen(urllib.request.Request(os.environ["DING_WEBHOOK"], data=b, headers={"Content-Type": "application/json"}), timeout=15)
        print("dingtalk sent")

if __name__ == "__main__":
    aj = sys.argv[1] if len(sys.argv) > 1 else "monitor/alerts.json"
    if not os.path.exists(aj):
        print("no alerts.json, nothing to push"); sys.exit(0)
    data = json.load(open(aj, encoding="utf-8"))
    items = data.get("items", [])
    if not items:
        print("no items"); sys.exit(0)
    sent = False
    if os.environ.get("WXPUSHER_TOKEN"):
        try:
            n = len(items)
            # 全量分批：每5条一条消息，确保一条不漏
            for i in range(0, n, 5):
                chunk = items[i:i + 5]
                head = f"cotopaxi上新 {i + 1}-{i + len(chunk)}/{n}条"
                if chunk[0].get("price"):
                    head += f" | {chunk[0]['price']}日元起"
                send_wxpusher_html(build_html(chunk), head)
            sent = True
            print(f"pushed {n} items in {(n + 4) // 5} message(s)")
        except Exception as e:
            print("wxpusher failed:", e)
    if not sent:
        try:
            send_text_fallback(alert_text)
        except Exception as e:
            print("fallback failed:", e)
