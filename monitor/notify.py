# -*- coding: utf-8 -*-
"""发送 alerts.log 中最新一条告警到通知渠道（按已配置的 Secrets 自动选择）"""
import os, sys, json, urllib.request, ssl, smtplib
from email.mime.text import MIMEText

def send_bark(alert, cfg):
    url = cfg.rstrip("/") + "/" + urllib.parse.quote("cotopaxi上新") + "/" + urllib.parse.quote(alert[:180]) + "?group=letao"
    urllib.request.urlopen(urllib.request.Request(url), timeout=15)

def send_wecom(alert, cfg):
    body = json.dumps({"msgtype": "text", "text": {"content": "cotopaxi 上新提醒\n" + alert}}).encode()
    req = urllib.request.Request(cfg, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15)

def send_dingtalk(alert, cfg):
    body = json.dumps({"msgtype": "text", "text": {"content": "cotopaxi 上新提醒\n" + alert}}).encode()
    req = urllib.request.Request(cfg, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15)

def send_serverchan(alert, cfg):
    # cfg = Server酱 SendKey；第一条为标题，其余为内容
    lines = alert.split("\n")
    title = lines[0][:32]
    desp = "\n\n".join(lines[1:]) or title
    data = urllib.parse.urlencode({"title": title, "desp": desp}).encode()
    req = urllib.request.Request(f"https://sctapi.ftqq.com/{cfg}.send", data=data)
    urllib.request.urlopen(req, timeout=15)

def send_wxpusher(alert, cfg):
    # cfg = WxPusher appToken
    lines = alert.split("\n")
    summary = lines[0][:20]
    content = "\n".join(lines[1:]) or summary
    body = json.dumps({
        "appToken": cfg,
        "content": alert,
        "summary": summary,
        "contentType": 1,
        "uids": json.loads(os.environ.get("WXPUSHER_UIDS", "[]")),
    }).encode()
    req = urllib.request.Request("https://wxpusher.zjiecode.com/api/send/message",
                                 data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15)

def send_email(alert, cfg):
    host, port, user, pwd, to = cfg.split("|")
    msg = MIMEText(alert, "plain", "utf-8")
    msg["Subject"] = "cotopaxi 上新提醒"
    msg["From"], msg["To"] = user, to
    s = smtplib.SMTP_SSL(host, int(port), timeout=20)
    s.login(user, pwd)
    s.sendmail(user, [to], msg.as_string())
    s.quit()

CHANNELS = [
    ("SERVERCHAN_KEY", send_serverchan),
    ("WXPUSHER_TOKEN", send_wxpusher),
    ("BARK_URL", send_bark),
    ("WECOM_WEBHOOK", send_wecom),
    ("DING_WEBHOOK", send_dingtalk),
    ("EMAIL_SMTP", send_email),  # 格式: 主机|端口|账号|密码|收件人
]

if __name__ == "__main__":
    alert_file = sys.argv[1] if len(sys.argv) > 1 else "monitor/alerts.log"
    if not os.path.exists(alert_file):
        print("no alert file"); sys.exit(0)
    with open(alert_file, encoding="utf-8") as f:
        blocks = [b.strip() for b in f.read().split("\n\n") if b.strip()]
    if not blocks:
        print("no alerts"); sys.exit(0)
    alert = blocks[-1]  # 最新一条
    ok = False
    for name, fn in CHANNELS:
        cfg = os.environ.get(name)
        if cfg:
            try:
                fn(alert, cfg)
                print(f"notified via {name}")
                ok = True
            except Exception as e:
                print(f"{name} failed: {e}")
    if not ok:
        print("no channel configured; alert content:\n" + alert)
