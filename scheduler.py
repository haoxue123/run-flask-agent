"""
后台自动同步调度
================
用 APScheduler 在 Flask 应用内做 Garmin 数据的自动同步：
  - 应用启动时立即同步一次（让你"打开即见最新数据"）
  - 之后每 SYNC_INTERVAL_HOURS 小时刷新一次

同步失败（如网络不通 / 令牌失效 / 沙箱限流）仅记日志，绝不阻断应用启动或页面访问。
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler

import garmin_sync

logger = logging.getLogger(__name__)

SYNC_INTERVAL_HOURS = 6          # 定时刷新间隔
SYNC_COUNT = 6                   # 每次拉取的跑步活动数

_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def run_sync_once():
    """执行一次同步。所有异常内部消化，绝不影响调用方。"""
    try:
        files = garmin_sync.pull_recent_runs(count=SYNC_COUNT, fmt="tcx")
        logger.info("Garmin 自动同步完成：新增/更新 %d 个运动文件", len(files))
        return files
    except Exception as e:
        logger.warning("Garmin 自动同步跳过（不影响应用）：%s", e)
        return []


def init_scheduler():
    """在 app.py 启动时调用：注册定时任务、启动调度器，并立即同步一次。"""
    if _scheduler.running:
        return
    _scheduler.add_job(
        run_sync_once,
        trigger="interval",
        hours=SYNC_INTERVAL_HOURS,
        id="garmin_auto_sync",
        replace_existing=True,
        next_run_time=None,   # 不立即触发；启动钩子下方已单独调用一次
    )
    _scheduler.start()
    logger.info("Garmin 自动同步调度已启动（每 %d 小时）", SYNC_INTERVAL_HOURS)
    # 应用启动即拉一次最新数据
    run_sync_once()


def shutdown_scheduler():
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
