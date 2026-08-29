"""
后台自动同步调度
================
用 APScheduler 在 Flask 应用内做 Garmin 数据的自动同步：
  - 应用启动时立即同步一次（让你"打开即见最新数据"）
  - 之后每 SYNC_INTERVAL_HOURS 小时刷新一次

同步失败（如网络不通 / 令牌失效 / 沙箱限流）仅记日志，绝不阻断应用启动或页面访问。
"""
import logging
import os
from apscheduler.schedulers.background import BackgroundScheduler

import garmin_sync

logger = logging.getLogger(__name__)

SYNC_INTERVAL_HOURS = 6          # 定时刷新间隔
SYNC_COUNT = 6                   # 每次拉取的跑步活动数

_scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _has_local_session():
    """判断本地是否已存在可复用的 Garmin 会话（浏览器 Cookie 或 garth 令牌）。"""
    base = os.path.dirname(os.path.abspath(__file__))
    cookie_path = os.path.join(base, "data", ".garmin_browser_cookies.json")
    if os.path.isfile(cookie_path) and os.path.getsize(cookie_path) > 0:
        return True
    garth_home = os.getenv(
        "GARTH_HOME", os.path.join(base, "data", ".garth")
    )
    if os.path.isdir(garth_home) and os.listdir(garth_home):
        return True
    return False


def run_sync_once():
    """执行一次同步。所有异常内部消化，绝不影响调用方。"""
    try:
        # 无本地会话时跳过自动同步，避免无谓的 Garmin 登录尝试（可能触发风控）；
        # 首次需用户在本机（有显示器）通过“登录 Garmin”完成一次 MFA 验证。
        if not _has_local_session():
            logger.info("无本地 Garmin 会话，跳过启动自动同步（请先在本机完成一次登录）")
            return []
        files = garmin_sync.pull_recent_runs(count=SYNC_COUNT, fmt="tcx")
        garmin_sync.record_sync_success(files, source="auto")
        # 同步成功可能带来新文件，必须让跑者画像缓存失效，否则页面读到旧数据
        try:
            import data_tool
            data_tool.invalidate_profile_cache()
        except Exception:
            pass
        logger.info("Garmin 自动同步完成：新增/更新 %d 个运动文件", len(files))
        return files
    except Exception as e:
        # 启动时的自动同步失败不写 sync_state，避免首页一打开就显示红条；
        # 错误仍记日志，手动点击“立即同步”时仍会把失败写入 sync_state。
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
