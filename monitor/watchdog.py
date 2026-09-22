# -*- coding: utf-8 -*-
"""cotopaxi 监控 · 存活兜底 watchdog（GitHub Actions cron 每 5 分钟）

检查主监控 run（monitor.yml 长跑 run）是否存活：
- 存在 queued / in_progress 的主 run → 正常，什么都不做
- 最近一个主 run 结束 < 15 分钟 → 接力间隙，继续等待
- 最近一个主 run 结束 >= 15 分钟且无新 run → workflow_dispatch 补触发长跑 run

环境变量: GH_TOKEN（GITHUB_TOKEN 或 PAT，需 actions:write 权限）
        GH_OWNER / GH_REPO（由 workflow 注入）
"""
import json
import os
import sys
import time
import urllib.request

STALE_MIN = 15
WORKFLOW_FILE = "monitor.yml"   # 主工作流文件名（长跑接力）


def gh(url, token, method="GET", body=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status == 204:
            return None
        return json.load(r)


def main():
    token = os.environ.get("GH_TOKEN", "")
    owner = os.environ.get("GH_OWNER", "")
    repo = os.environ.get("GH_REPO", "")
    if not (token and owner and repo):
        print("[warn] GH_TOKEN/GH_OWNER/GH_REPO 未配置，跳过")
        return 1
    api = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        # 注意：GitHub 的 workflow 查询参数在此仓库无效（返回全部 runs），
        # 必须按 name 过滤，否则会取到 watchdog 自身的 run，导致永远判定"正常"
        runs = gh(f"{api}/actions/runs?per_page=20", token)["workflow_runs"]
        runs = [r for r in runs if r.get("name") == "letao-monitor"]
    except Exception as e:
        print(f"[warn] 查询主 run 失败: {str(e)[:80]}")
        return 1
    if not runs:
        print("[warn] 未找到任何主 run，直接补触发")
        dispatch(api, token)
        return 0
    last = runs[0]
    status, conclusion = last.get("status"), last.get("conclusion")
    updated = last.get("updated_at", "")
    print(f"last main run: status={status} conclusion={conclusion} updated={updated}")
    if status in ("queued", "in_progress"):
        print("alive: 主 run 在跑/排队，无需干预")
        return 0
    # completed：按结束时间判断接力间隙
    age_min = STALE_MIN
    try:
        done_ts = time.mktime(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ"))
        age_min = (time.time() - done_ts) / 60
    except Exception:
        pass
    if age_min < STALE_MIN:
        print(f"ok: 主 run {age_min:.0f} 分钟前结束，处于接力间隙")
        return 0
    print(f"stale: 主 run 已结束 {age_min:.0f} 分钟且无新 run，补触发")
    dispatch(api, token)
    return 0


def dispatch(api, token):
    try:
        gh(f"{api}/actions/workflows/{WORKFLOW_FILE}/dispatches", token,
           method="POST", body={"ref": "main"})
        print("recovery run dispatched")
    except Exception as e:
        print(f"[warn] 补触发失败: {str(e)[:80]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
