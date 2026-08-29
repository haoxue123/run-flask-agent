"""
Garmin Connect 数据同步工具（基于 garth-ng）
============================================================
使用第三方 garth 库（OAuth 令牌持久化 + 中国区支持）从 Garmin Connect
拉取最近 N 次跑步活动，保存为 GPX/TCX/FIT 运动文件到 data/ 目录，
供 data_tool.py 的现有解析器直接消费。

相比早期 garminconnect 实现的关键改进：
  - OAuth1 令牌持久化（save/resume）：首次登录后令牌可管 ~1 年，
    之后只做无登录的令牌恢复 → 根治「反复登录被 Garmin 限流 429」。
  - garth.configure(domain="garmin.cn")：支持中国区账号 →
    根治中国区鉴权主机 DNS 不通。
  - 令牌存于 data/.garth/（已在 .gitignore，绝不提交）。

接入方式：
  1. 在 .env 填入 GARMIN_USERNAME / GARMIN_PASSWORD（中国区再加 GARMIN_IS_CN=true）
  2. 调用 garmin_sync.pull_recent_runs(count=5, fmt='tcx') 即可下载
  3. data_tool.get_recent_runs() / build_runner_profile() 自动扫描新文件

若账号开启两步验证（MFA）：服务器内无法交互输验证码，可改为在本机执行
  uvx garth login        # 或 python -c "import garth; garth.login(email, password)"
生成令牌后，把会话目录（默认 ~/.garth）或 GARTH_TOKEN 环境变量交给本应用。
"""

import os
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import safeio

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data"
)
# 令牌持久化目录（存 OAuth 令牌，已 gitignore）
DEFAULT_GARTH_HOME = os.path.join(DEFAULT_OUTPUT_DIR, ".garth")

# 常见 Garmin 跑步类型 typeKey；也兼容包含 "running" 或 "run" 的变体
_RUNNING_TYPE_KEYS = {
    "running", "street_running", "track_running", "trail_running",
    "treadmill_running", "virtual_run", "indoor_running", "ultra_run",
}

# 浏览器 UA：garth 默认用移动端 UA（GCM-iOS-...），Garmin 的 Cloudflare
# 会据此拦截并让 OAuth 登录返回 400。换成桌面浏览器 UA 即可绕过。
# （社区 2026-04 多人验证：普通登录与 MFA 流程均适用）
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 同步状态文件（让前端知道数据是否最新）
SYNC_STATE_PATH = os.path.join(DEFAULT_OUTPUT_DIR, "sync_state.json")


def _get_credentials():
    """从 .env 读取 Garmin 账号/密码/是否中国区。"""
    username = os.getenv("GARMIN_USERNAME", "").strip()
    password = os.getenv("GARMIN_PASSWORD", "").strip()
    is_cn = os.getenv("GARMIN_IS_CN", "true").lower() in ("1", "true", "yes", "y")
    return username, password, is_cn


def _is_running_activity(activity):
    """判断活动是否为跑步类型（兼容 connectapi 返回的 dict）。"""
    activity_type = activity.get("activityType")
    if isinstance(activity_type, dict):
        type_key = activity_type.get("typeKey", "").lower()
    else:
        type_key = str(activity_type).lower()
    if type_key in _RUNNING_TYPE_KEYS:
        return True
    if "running" in type_key or type_key == "run" or type_key.endswith("_run"):
        return True
    return False


def _activity_filename(activity, activity_id, fmt):
    """根据活动日期生成 YYYY-MM-DD_activityId.fmt 文件名。"""
    start_time = activity.get("startTimeLocal") or activity.get("startTimeGMT")
    date_str = ""
    if start_time:
        try:
            dt = datetime.strptime(start_time.split(".")[0], "%Y-%m-%d %H:%M:%S")
            date_str = dt.strftime("%Y-%m-%d")
        except ValueError:
            date_str = start_time[:10] if len(start_time) >= 10 else ""
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    ext = fmt.lower()
    if ext == "fit":
        ext = "zip"  # FIT 原始格式是 ZIP 内含 .fit
    return f"{date_str}_{activity_id}.{ext}"


def _get_garth():
    """获取已认证的 garth 客户端：优先复用持久化令牌，否则登录并保存。

    返回 garth 模块对象（已登录/已恢复会话）。异常统一转为 RuntimeError。
    """
    try:
        import garth
    except ImportError as e:
        raise RuntimeError(
            "未安装 garth 库。请运行：\n  pip install garth-ng"
        ) from e

    username, password, is_cn = _get_credentials()
    if is_cn:
        garth.configure(domain="garmin.cn")

    # 关键修复：覆盖默认移动端 UA（GCM-iOS-...），避免 Cloudflare 拦截
    # 导致 OAuth 登录返回 400。必须在 login/resume 之前设置。
    try:
        garth.client.session.headers.update({"User-Agent": _BROWSER_UA})
    except Exception as e:  # noqa: BLE001
        logger.warning("设置浏览器 UA 失败（将使用 garth 默认）：%s", e)

    home = os.getenv("GARTH_HOME", DEFAULT_GARTH_HOME)

    # 1) 优先恢复已保存的令牌会话（无登录，避免 429）
    try:
        garth.resume(home)
        logger.info("已恢复 Garmin 持久化会话（%s）", home)
        return garth
    except Exception as e:
        logger.debug("恢复会话失败，将尝试登录：%s", e)

    # 2) 否则用账号密码登录，并保存令牌供下次复用
    if not username or not password:
        raise RuntimeError(
            "未配置 Garmin 账号。请在 .env 中设置：\n"
            "  GARMIN_USERNAME=你的邮箱\n"
            "  GARMIN_PASSWORD=你的密码"
        )
    try:
        garth.login(username, password)
    except Exception as e:
        # MFA 需要交互输码，服务器内无法处理
        if "mfa" in str(e).lower() or "verify" in str(e).lower():
            raise RuntimeError(
                "账号开启了两步验证，服务器内无法交互输入验证码。\n"
                "请在本机执行 `uvx garth login`（或 "
                "python -c \"import garth; garth.login('邮箱','密码')\"）\n"
                "生成令牌后，把会话目录（默认 ~/.garth）或 GARTH_TOKEN 交给本应用。"
            ) from e
        raise RuntimeError(f"Garmin Connect 登录失败：{e}") from e

    try:
        garth.save(home)
        logger.info("已保存 Garmin 会话令牌至 %s", home)
    except Exception as e:
        logger.warning("保存 Garmin 会话令牌失败（下次需重新登录）：%s", e)

    return garth


def pull_recent_runs(count=5, output_dir=None, fmt="tcx"):
    """
    从 Garmin Connect 拉取最近 N 次跑步活动并保存为运动文件。

    参数：
        count:      最近活动数（默认 5）。实际会从 Garmin 多取一些再过滤出跑步。
        output_dir: 文件保存目录，默认项目根目录下的 data/
        fmt:        下载格式，'tcx' | 'gpx' | 'fit'。默认 'tcx'。

    返回：list[str]，已下载文件的完整路径。
    """
    if fmt not in {"tcx", "gpx", "fit"}:
        raise ValueError(f"不支持的下载格式 '{fmt}'，仅支持 tcx/gpx/fit")

    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    # 浏览器引擎优先：garth 的 HTTP 传输已被 Garmin Cloudflare 封禁（400/401）。
    # 浏览器引擎用真实浏览器 fetch，可绕过 bot 检测。失败再回退 garth（仅当本地有令牌）。
    try:
        import garmin_browser
        if garmin_browser.engine_available():
            logger.info("使用浏览器引擎同步 Garmin 数据")
            return garmin_browser.pull_recent_runs_browser(
                count=count, output_dir=output_dir, fmt=fmt, headless=True
            )
    except Exception as e:  # noqa: BLE001
        # 浏览器引擎已是绕过 Cloudflare 的正确路径，其报错（MFA/凭据）已足够清晰，
        # 不要再误导性地回退到已被 Garmin 封禁的 garth HTTP OAuth 路径。
        # 仅当本地已存有 garth 持久化令牌时才回退（resume 可免登录恢复会话）。
        garth_token_dir = os.getenv("GARTH_HOME", DEFAULT_GARTH_HOME)
        has_garth_token = os.path.isdir(garth_token_dir) and bool(os.listdir(garth_token_dir))
        if has_garth_token:
            logger.warning("浏览器引擎同步失败，尝试用本地 garth 令牌恢复：%s", e)
        else:
            logger.error("浏览器引擎同步失败（Garmin 账号需两步验证或凭据有误）：%s", e)
            raise RuntimeError(str(e)) from e

    garth = _get_garth()

    # 多请求一些活动，确保过滤后仍有 count 条跑步记录
    fetch_limit = max(count * 3, 10)
    try:
        activities = garth.connectapi(
            "/activitylist-service/activities",
            params={"start": 0, "limit": fetch_limit},
        )
    except Exception as e:
        raise RuntimeError(f"获取活动列表失败：{e}") from e

    if not activities:
        return []

    downloaded = []
    errors = []

    for activity in activities:
        if not _is_running_activity(activity):
            continue
        activity_id = activity.get("activityId")
        if not activity_id:
            continue

        filename = _activity_filename(activity, activity_id, fmt)
        filepath = os.path.join(output_dir, filename)

        try:
            # 走 Connect API 原始下载端点（garth.download 返回字节）
            data = garth.download(
                f"/download-service/files/activity/{activity_id}?format={fmt}"
            )
            with open(filepath, "wb") as fb:
                fb.write(data)
            downloaded.append(filepath)
            logger.info("已下载 Garmin 跑步活动: %s", filename)
        except Exception as e:
            logger.warning("下载活动 %s 失败: %s", activity_id, e)
            errors.append({"activity_id": activity_id, "error": str(e)})
            continue

        if len(downloaded) >= count:
            break

    if not downloaded and errors:
        raise RuntimeError(f"全部 {len(errors)} 条活动下载失败，请检查日志。")

    return downloaded


def garmin_login(headless=False, timeout=300):
    """触发浏览器交互登录（MFA 在可见浏览器输入），成功写 Cookie。

    返回 True 表示登录成功；依赖 playwright，未安装时抛 RuntimeError。
    """
    try:
        import garmin_browser
    except ImportError as e:
        raise RuntimeError(
            "未安装 playwright。请运行：\n"
            "  pip install playwright\n"
            "  playwright install chromium   # 或本机有 Chrome 时可省"
        ) from e
    if not garmin_browser.engine_available():
        raise RuntimeError("playwright 未安装，无法发起浏览器登录")
    return garmin_browser.browser_login(headless=headless, timeout=timeout)


def get_sync_status():
    """返回当前 Garmin 同步配置状态（不触发登录）。"""
    username, password, is_cn = _get_credentials()
    try:
        import garth  # noqa: F401
        available = True
    except ImportError:
        available = False
    return {
        "configured": bool(username and password),
        "username_prefix": username[:3] + "***" if username else "",
        "is_cn": is_cn,
        "library_installed": available,
    }


# ============================================================
# 同步状态持久化（让前端知道数据是否最新）
# ============================================================
def write_sync_state(state):
    """把同步状态写入 data/sync_state.json（best-effort，不抛错）。"""
    payload = dict(state)
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        safeio.safe_write_json(SYNC_STATE_PATH, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("写入同步状态失败：%s", e)


def read_sync_state():
    """读取同步状态；文件不存在/损坏时返回空 dict。"""
    try:
        if os.path.isfile(SYNC_STATE_PATH):
            with open(SYNC_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:  # noqa: BLE001
        pass
    return {}


def record_sync_success(files, source="auto"):
    """记录一次成功同步。files: 已下载文件路径列表。"""
    write_sync_state({
        "result": "success",
        "source": source,
        "count": len(files),
        "files": [os.path.basename(p) for p in files],
    })


def record_sync_failure(error, source="auto"):
    """记录一次失败同步。"""
    write_sync_state({
        "result": "failed",
        "source": source,
        "error": str(error),
    })


# ============================================================
# 模块自测
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("Garmin Connect 同步模块自测（garth）")
    print("=" * 50)
    status = get_sync_status()
    print("\n--- 配置状态 ---")
    print(f"库已安装: {status['library_installed']}")
    print(f"账号已配置: {status['configured']}")
    print(f"中国区: {status['is_cn']}")
    if status["configured"]:
        print(f"账号前缀: {status['username_prefix']}")
    else:
        print("提示：在 .env 中配置 GARMIN_USERNAME 和 GARMIN_PASSWORD 后再运行")
        print("跳过真实下载测试")
        exit(0)

    try:
        print("\n--- 尝试下载最近 2 条跑步活动（TCX） ---")
        files = pull_recent_runs(count=2, fmt="tcx")
        for f in files:
            print(f"  已保存: {f}")
    except Exception as e:
        print(f"  失败: {e}")
