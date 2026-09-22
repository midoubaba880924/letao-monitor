# -*- coding: utf-8 -*-
"""cotopaxi 监控 · 存活兜底 watchdog（GitHub Actions cron 每 5 分钟）

检查主监控 run（monitor.yml 长跑 run）是否存活：
- 无主 run → 补触发
- 主 run queued/in_progress 且心跳新鲜（< 8 分钟）→ 正常
- 主 run queued/in_progress 但心跳停滞（>= 8 分钟）→ 判为卡死，cancel 该 run 后补触发
- 主 run 异常结束（failure/cancelled）→ 立即补触发
- 主 run 正常结束（success）< 15 分钟 → 接力间隙等待；>= 15 分钟 → 补触发

环境变量: GH_TOKEN（GITHUB_TOKEN 或 PAT，需 actions:write 权限）
        GH_OWNER / GH_REPO（由 workflow 注入）
"""
import datetime
import json
import os
import sys
import time
import urllib.request

STALE_MIN = 15            # run 正常结束后等待接力间隙的分钟数
STALE_HEARTBEAT_MIN = 8   # run in_progress 但心跳停止的判定阈值（分钟）
WORKFLOW_FILE = "monitor.yml"   # 主工作流文件名（长跑接力）
# 停跑窗口：北京时间 0:30-6:30 = UTC 16:30-22:30（990-1349 分钟），此期间主 run 本就应结束，不补触发
PAUSE_START_MIN = 16 * 60 + 30
PAUSE_END_MIN = 22 * 60 + 30


def in_pause_window():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    mins = now_utc.hour * 60 + now_utc.minute
    return PAUSE_START_MIN <= mins < PAUSE_END_MIN


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


def last_heartbeat_age_min(api, token):
    """心跳文件最近提交距今分钟数；查询失败返回 None（保守，不据此误杀）"""
    try:
        commits = gh(f"{api}/commits?path=monitor/heartbeat&per_page=1", token)
        if not commits:
            print("[warn] 未查到心跳提交记录")
            return None
        hb_ts = commits[0]["commit"]["committer"]["date"].replace("Z", "+00:00")
        hb_dt = datetime.datetime.fromisoformat(hb_ts)
        age = (datetime.datetime.now(datetime.timezone.utc) - hb_dt).total_seconds() / 60
        return age
    except Exception as e:
        print(f"[warn] 查询心跳失败: {str(e)[:60]}")
        return None


def cancel_run(api, token, run_id):
    try:
        gh(f"{api}/actions/runs/{run_id}/cancel", token, method="POST")
        print(f"cancelled stuck run {run_id}")
        return True
    except Exception as e:
        print(f"[warn] cancel run {run_id} 失败: {str(e)[:80]}")
        return False


def dispatch(api, token):
    try:
        gh(f"{api}/actions/workflows/{WORKFLOW_FILE}/dispatches", token,
           method="POST", body={"ref": "main"})
        print("recovery run dispatched")
        return True
    except Exception as e:
        print(f"[warn] 补触发失败: {str(e)[:80]}")
        return False


def main():
    token = os.environ.get("GH_TOKEN", "")
    owner = os.environ.get("GH_OWNER", "")
    repo = os.environ.get("GH_REPO", "")
    if not (token and owner and repo):
        print("[warn] GH_TOKEN/GH_OWNER/GH_REPO 未配置，跳过")
        return 1
    if in_pause_window():
        print("停跑窗口（北京 0:30-6:30），主 run 正常不在线，跳过")
        return 0
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
    run_id = last.get("id")
    updated = last.get("updated_at", "")
    print(f"last main run: id={run_id} status={status} conclusion={conclusion} updated={updated}")

    if status in ("queued", "in_progress"):
        # 心跳感知：in_progress 但心跳长期未更新 = run 卡死（runner 失联/push 中断）
        hb_age = last_heartbeat_age_min(api, token)
        if hb_age is not None and hb_age >= STALE_HEARTBEAT_MIN:
            print(f"stuck: 主 run in_progress 但心跳 {hb_age:.0f} 分钟未更新，判为卡死")
            if cancel_run(api, token, run_id):
                dispatch(api, token)
                print(f"已 cancel 卡死 run {run_id} 并补触发")
            return 0
        print(f"alive: 主 run 在跑/排队，心跳正常({hb_age:.1f} 分钟前)" if hb_age is not None
              else "alive: 主 run 在跑/排队（心跳不可查，保守不干预）")
        return 0

    # completed：异常结束立即补触发，正常结束按接力间隙等待
    if conclusion in ("failure", "cancelled"):
        print(f"abnormal: 主 run 异常结束({conclusion})，立即补触发")
        dispatch(api, token)
        return 0
    age_min = STALE_MIN
    try:
        done_ts = time.mktime(time.strptime(updated, "%Y-%m-%dT%H:%M:%SZ"))
        age_min = (time.time() - done_ts) / 60
    except Exception:
        pass
    if age_min < STALE_MIN:
        print(f"ok: 主 run {age_min:.0f} 分钟前正常结束，处于接力间隙")
        return 0
    print(f"stale: 主 run 已结束 {age_min:.0f} 分钟且无新 run，补触发")
    dispatch(api, token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
