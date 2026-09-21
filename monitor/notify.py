# -*- coding: utf-8 -*-
"""读取 alerts.json，组装富卡片推送到所有已配置渠道（微信富卡片 + 钉钉/企微/Bark 文本）"""
import os, sys, json, urllib.request, urllib.parse

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
    footer = '<p style="color:#888;font-size:12px">GitHub 云端监控 · 每2.5分钟检查 Mercari</p>'
    return "".join(cards) + footer

def post_json(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def send_wxpusher(items, summary):
    body = {
        "appToken": os.environ["WXPUSHER_TOKEN"],
        "summary": summary[:20], "contentType": 2,
        "uids": json.loads(os.environ.get("WXPUSHER_UIDS", "[]")),
    }
    d = post_json("https://wxpusher.zjiecode.com/api/send/message", body)
    print("wxpusher:", d.get("code"), d.get("msg"))

def send_dingtalk(items, summary):
    # 钉钉 markdown 消息；机器人关键词需包含 cotopaxi
    text = ""
    for it in items[:5]:
        price = f'{it["price"]}日元(约¥{it["cny"]})' if it.get("price") else ""
        text += f"### 🆕 [{it.get('title','')}]({it['h5']})\n\n💰 **{price}** 🛒 {it['platform']}\n\n[手机购买页]({it['h5']}) · [电脑页]({it['pc']})\n\n"
    body = {"msgtype": "markdown",
            "markdown": {"title": "cotopaxi上新", "text": f"## {summary}\n\n" + text}}
    d = post_json(os.environ["DING_WEBHOOK"], body)
    print("dingtalk:", d.get("errcode"), d.get("errmsg"))

def send_wecom(items, summary):
    text = summary + "\n" + "\n".join(f"[{it['platform']}] {it.get('title','')[:30]} {it.get('price','')}日元\n{it['h5']}" for it in items[:5])
    d = post_json(os.environ["WECOM_WEBHOOK"], {"msgtype": "text", "text": {"content": "cotopaxi 上新\n" + text}})
    print("wecom:", d.get("errcode"), d.get("errmsg"))

def send_bark(items, summary):
    it = items[0]
    u = os.environ["BARK_URL"].rstrip("/") + "/" + urllib.parse.quote(summary) + "/" + urllib.parse.quote(it.get("h5", "") or it.get("pc", ""))
    urllib.request.urlopen(urllib.request.Request(u), timeout=15)
    print("bark sent")

CHANNELS = [
    ("WXPUSHER_TOKEN", send_wxpusher),
    ("DING_WEBHOOK", send_dingtalk),
    ("WECOM_WEBHOOK", send_wecom),
    ("BARK_URL", send_bark),
]

if __name__ == "__main__":
    aj = sys.argv[1] if len(sys.argv) > 1 else "monitor/alerts.json"
    if not os.path.exists(aj):
        print("no alerts.json, nothing to push"); sys.exit(0)
    data = json.load(open(aj, encoding="utf-8"))
    items = data.get("items", [])
    if not items:
        print("no items"); sys.exit(0)

    n = len(items)
    # 全量分批：每5条一组，每组都推到所有已配置渠道，确保一条不漏
    groups = [items[i:i + 5] for i in range(0, n, 5)]
    ok_channels = []
    for name, fn in CHANNELS:
        if not os.environ.get(name):
            continue
        try:
            for gi, chunk in enumerate(groups, 1):
                s = f"cotopaxi上新 {gi}/{len(groups)}组"
                if chunk[0].get("price"):
                    s += f" | {chunk[0]['price']}日元起"
                fn(chunk, s)
            ok_channels.append(name)
        except Exception as e:
            print(name, "failed:", str(e)[:80])
    print(f"pushed {n} item(s) x {len(groups)} group(s) via {ok_channels or '无已配置渠道'}")
