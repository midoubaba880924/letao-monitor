# -*- coding: utf-8 -*-
"""cotopaxi 监控 · 长跑循环 runner（GitHub Actions 长跑接力）

在一个 workflow run 内每 2 分钟执行一轮 letao_monitor.py + notify.py，
每轮 commit+push 状态与心跳，接近 6h job 上限前主动退出（exit 0 避免被强杀）。
下一轮由 cron 触发的新 run 通过 concurrency 排队无缝接管。

- TEST_ITEM 非空 = 单轮测试模式（手动 dispatch 用，不循环）
- monitor 失败时跳过 notify（防止残留 alerts.json 被重复推送）
- git push 连续失败 >= 3 轮 或 监控/推送连续失败 >= 3 轮
  → 多渠道告警 + 主动退出（exit 1），释放 concurrency 让 watchdog 补触发重试（自愈）
"""
import os
import subprocess
import sys
import time

RUN_SECONDS = 359 * 60      # 每 run 最长约 5h59m，留 1 分钟余量，避免被 GitHub 6h 上限强杀
ROUND_SECONDS = 120         # 每轮 2 分钟（及时性基准）
MAX_PUSH_FAIL_STREAK = 3    # 连续 push 失败上限，超过则主动退出
MAX_WORK_FAIL_STREAK = 3    # 连续 监控/推送 失败上限，超过则告警 + 主动退出
BASE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE)
HEARTBEAT = os.path.join(BASE, "heartbeat")
LOOP_LOG = os.path.join(BASE, "loop.log")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOOP_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def git(cmd):
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=120)


def run_one_round():
    """跑一轮 监控 + 推送 + 状态/心跳提交。

    返回 (push_ok, work_ok)：
      push_ok = False  → 本轮 git push 失败
      work_ok = False  → 本轮监控或推送失败（monitor 失败时自动跳过 notify）
    任何单点失败不抛异常（不阻塞循环）。
    """
    work_ok = True
    # 1) 监控（失败 → 跳过推送，避免残留 alerts.json 被重复推送）
    try:
        r = subprocess.run([sys.executable, os.path.join(BASE, "letao_monitor.py")],
                           cwd=BASE, capture_output=True, text=True, timeout=150)
        out = (r.stdout or "").strip() + (r.stderr or "").strip()
        if out:
            print(out, flush=True)
        if r.returncode != 0:
            work_ok = False
            log(f"[warn] letao_monitor.py 返回码 {r.returncode}")
    except Exception as e:
        work_ok = False
        log(f"[warn] letao_monitor.py 执行异常: {str(e)[:80]}")
    # 2) 推送（仅当监控成功）
    if work_ok:
        try:
            r = subprocess.run([sys.executable, os.path.join(BASE, "notify.py")],
                               cwd=BASE, capture_output=True, text=True, timeout=150)
            out = (r.stdout or "").strip() + (r.stderr or "").strip()
            if out:
                print(out, flush=True)
            if r.returncode != 0:
                work_ok = False
                log(f"[warn] notify.py 返回码 {r.returncode}")
        except Exception as e:
            work_ok = False
            log(f"[warn] notify.py 执行异常: {str(e)[:80]}")
    else:
        print("[skip] monitor 失败，跳过 notify 防止重复推送", flush=True)
    # 心跳：每轮必变 → 强制 commit（供存活检测）
    try:
        with open(HEARTBEAT, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
    except Exception:
        pass
    # 提交状态 + 心跳 + 循环日志；push 失败计入连续失败数
    push_ok = True
    try:
        git(["git", "add", "monitor/seen_items.json", "monitor/alerts.log",
             "monitor/heartbeat", "monitor/loop.log"])
        git(["git", "-c", "user.name=monitor-bot", "-c",
             "user.email=bot@users.noreply.github.com", "commit",
             "-m", "chore: update monitor state [skip ci]"])
        r = git(["git", "pull", "--rebase", "origin", "main"])
        if r.returncode != 0:
            log(f"[warn] git pull --rebase 返回 {r.returncode}: {(r.stderr or '')[:100]}")
            git(["git", "rebase", "--abort"])  # 恢复干净工作区，下轮重试
            push_ok = False
        else:
            r = git(["git", "push"])
            if r.returncode != 0:
                log(f"[warn] git push 返回 {r.returncode}: {(r.stderr or '')[:100]}")
                push_ok = False
    except Exception as e:
        log(f"[warn] git 提交异常: {str(e)[:80]}")
        push_ok = False
    return push_ok, work_ok


def main():
    test_item = os.environ.get("TEST_ITEM", "").strip()
    if test_item:
        log("test mode: 单轮执行（TEST_ITEM 已设置）")
        run_one_round()
        return 0

    start = time.time()
    log(f"loop start, target {RUN_SECONDS // 60} min, round {ROUND_SECONDS}s")
    round_no = 0
    push_fail_streak = 0
    work_fail_streak = 0
    while time.time() - start < RUN_SECONDS:
        ok_push, ok_work = run_one_round()
        push_fail_streak = 0 if ok_push else push_fail_streak + 1
        work_fail_streak = 0 if ok_work else work_fail_streak + 1
        round_no += 1
        if push_fail_streak >= MAX_PUSH_FAIL_STREAK:
            log(f"连续 {push_fail_streak} 轮 git push 失败，主动退出释放 concurrency（自愈）")
            return 1
        if work_fail_streak >= MAX_WORK_FAIL_STREAK:
            try:
                import notify as notifier
                notifier.send_alert(
                    "cotopaxi 监控运行异常",
                    f"连续 {work_fail_streak} 轮监控/推送失败，系统将自动重启监控。\n\n若持续失败请检查 LETAO_TOKEN 与推送配置。")
            except Exception as e:
                log(f"[warn] 告警发送失败: {str(e)[:60]}")
            log(f"连续 {work_fail_streak} 轮监控/推送失败，主动退出释放 concurrency（自愈）")
            return 1
        target = start + round_no * ROUND_SECONDS
        remain = target - time.time()
        if remain > 0:
            time.sleep(remain)
    log(f"loop end after {round_no} rounds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
