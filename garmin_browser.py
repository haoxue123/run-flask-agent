"""
浏览器自动化 Garmin 同步引擎（Playwright）
============================================================

背景（第一性原理）：
    Garmin 自 2026-03 起禁用了旧的 HTTP 级 SSO/OAuth 端点，并部署 Cloudflare
    bot 检测，requests / httpx / curl_cffi（即使伪装 Chrome TLS 指纹）一律被拦，
    返回 400/401/403。社区已验证：唯一可行的方案是在真实浏览器里发起请求——
    即 page.evaluate(fetch(...))，Cloudflare 看到真实浏览器上下文会放行。

本模块的做法：
    1. 用 Playwright 驱动 Chromium / 本机已装 Chrome 打开 Garmin SSO 登录页；
    2. 填账号密码（MFA 在可见浏览器里由用户手动输入）；
    3. 登录成功后把会话 Cookie 持久化到 data/.garmin_browser_cookies.json；
    4. 之后每次同步，活动列表与文件下载都通过 page.evaluate(fetch(...)) 在
       真实浏览器上下文执行，credentials:'include' 带上 Cookie，绕过 Cloudflare。

依赖：playwright（pip install playwright；本机有 Chrome 可省 chromium 下载）。
"""

import os
import json
import logging
import base64
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data"
)
# 浏览器会话 Cookie（已 gitignore）
BROWSER_COOKIE_PATH = os.path.join(DEFAULT_OUTPUT_DIR, ".garmin_browser_cookies.json")

# 复用 garmin_sync 的判定/命名逻辑，避免重复实现
from garmin_sync import _is_running_activity, _activity_filename, _get_credentials


def engine_available():
    """playwright 是否已安装（与能否联网登录无关）。"""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _domain():
    _, _, is_cn = _get_credentials()
    return "garmin.cn" if is_cn else "garmin.com"


def _launch(headless):
    """启动浏览器。优先用本机已装 Chrome（省去下载 chromium）。"""
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    context = None
    browser = None
    try:
        # 先尝试本机 Chrome（用户机器上通常已装）
        browser = p.chromium.launch(
            channel="chrome",
            headless=headless,
            args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled"],
        )
    except Exception as e:
        logger.info("未找到本机 Chrome，回退到 Playwright 自带 Chromium：%s", e)
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-first-run", "--disable-blink-features=AutomationControlled"],
        )
    context = browser.new_context(
        user_agent=ua,
        locale="zh-CN",
        viewport={"width": 1280, "height": 900},
    )
    # 去掉 webdriver 痕迹
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return p, browser, context


def _save_cookies(context):
    try:
        cookies = context.cookies()
        with open(BROWSER_COOKIE_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info("已保存浏览器会话 Cookie 到 %s", BROWSER_COOKIE_PATH)
    except Exception as e:  # noqa: BLE001
        logger.warning("保存浏览器 Cookie 失败：%s", e)


def _load_cookies(context):
    if not os.path.isfile(BROWSER_COOKIE_PATH):
        return False
    try:
        with open(BROWSER_COOKIE_PATH, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        if not cookies:
            return False
        context.add_cookies(cookies)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("读取浏览器 Cookie 失败（将重新登录）：%s", e)
        return False


def _session_valid(page, domain):
    """用一次轻量 connectapi 探测 Cookie 是否仍有效。"""
    try:
        ok = page.evaluate(
            """(domain) => fetch(`https://connectapi.${domain}/userprofile-service/socialProfile`, {credentials:'include'})
                .then(r => r.ok).catch(() => false)""",
            domain,
        )
        return bool(ok)
    except Exception:  # noqa: BLE001
        return False


def _safe_query(page, selector):
    """导航中调用 query_selector 会抛 'Execution context was destroyed'，
    这里安全地返回 None，交给上层下一轮重试。"""
    try:
        return page.query_selector(selector)
    except Exception:  # noqa: BLE001
        return None


def _safe_text(el):
    try:
        return (el.inner_text() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def browser_login(headless=False, timeout=300):
    """
    启动（可见）浏览器完成 Garmin 登录，并把 Cookie 持久化。
    MFA 由用户在弹出的浏览器窗口里手动输入；本函数轮询直到登录成功或超时。
    返回 True 表示登录成功。
    """
    username, password, is_cn = _get_credentials()
    if not username or not password:
        raise RuntimeError(
            "未配置 Garmin 账号。请在 .env 设置 GARMIN_USERNAME / GARMIN_PASSWORD"
        )
    domain = "garmin.cn" if is_cn else "garmin.com"

    p, browser, context = _launch(headless=headless)
    try:
        page = context.new_page()
        sso = f"https://sso.{domain}/sso/signin"
        logger.info("打开 Garmin 登录页：%s", sso)
        page.goto(sso, wait_until="domcontentloaded", timeout=60000)

        # 填表（兼容 garmin.cn / garmin.com 的表单字段名）
        page.wait_for_selector('input[name="username"]', timeout=30000)
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
        # 提交后 SSO 会跳转，等首屏稳定再开始轮询，避免立即查元素撞上导航
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:  # noqa: BLE001
            pass

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                url = page.url
                # 登录成功：跳回 connect 域或 SSO 内嵌页
                if "connect.garmin" in url or _session_valid(page, domain):
                    _save_cookies(context)
                    logger.info("浏览器登录成功，Cookie 已保存")
                    return True
                # MFA：用户需在弹出的浏览器里输入验证码，这里只等待
                if "verifyMFA" in url or _safe_query(page, 'input[name="mfa-code"]'):
                    logger.info("检测到两步验证，请在浏览器窗口中输入验证码…")
                    time.sleep(3)
                    continue
                # 登录失败提示
                err = _safe_query(page, ".error-text, .alert-error, [class*='error']")
                if err:
                    msg = _safe_text(err)
                    if msg:
                        raise RuntimeError(f"Garmin 登录失败：{msg}")
            except Exception as e:  # noqa: BLE001
                # 页面正在跳转、执行上下文被销毁，下一轮重试
                logger.debug("登录轮询中页面跳转，重试：%s", e)
                time.sleep(2)
                continue
            time.sleep(3)

        raise RuntimeError("浏览器登录超时（MFA 未输入或登录未完成）")
    finally:
        browser.close()
        p.stop()


def pull_recent_runs_browser(count=5, output_dir=None, fmt="tcx", headless=True):
    """
    通过真实浏览器拉取最近 N 次跑步活动并保存为运动文件。
    返回已下载文件完整路径列表。
    """
    if fmt not in {"tcx", "gpx", "fit"}:
        raise ValueError(f"不支持的下载格式 '{fmt}'，仅支持 tcx/gpx/fit")
    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    username, password, is_cn = _get_credentials()
    domain = "garmin.cn" if is_cn else "garmin.com"
    api_base = f"https://connectapi.{domain}"

    p, browser, context = _launch(headless=headless)
    try:
        # 1) 复用已保存 Cookie，必要时重新登录
        if _load_cookies(context):
            probe = context.new_page()
            if _session_valid(probe, domain):
                logger.info("复用已保存的浏览器会话")
            else:
                logger.info("Cookie 失效，重新浏览器登录")
                browser_login_page = context.new_page()
                _do_login(browser_login_page, username, password, domain)
                _save_cookies(context)
        else:
            if not username or not password:
                raise RuntimeError("未配置 Garmin 账号且本地无 Cookie，无法同步")
            logger.info("本地无 Cookie，开始浏览器登录")
            _do_login(context.new_page(), username, password, domain)
            _save_cookies(context)

        page = context.new_page()

        # 2) 列出活动（多取一些再过滤跑步）
        fetch_limit = max(count * 3, 10)
        listing = page.evaluate(
            """(args) => {
                const u = new URL(args.url);
                Object.entries(args.params).forEach(([k,v]) => u.searchParams.set(k, v));
                return fetch(u.toString(), {credentials:'include', headers:{'nk':'NT','Accept':'application/json'}})
                    .then(r => r.ok ? r.json() : Promise.reject(new Error('list ' + r.status)));
            }""",
            {
                "url": f"{api_base}/activitylist-service/activities",
                "params": {"start": 0, "limit": fetch_limit},
            },
        )

        if not listing:
            return []

        downloaded = []
        for activity in listing:
            if not _is_running_activity(activity):
                continue
            activity_id = activity.get("activityId")
            if not activity_id:
                continue
            filename = _activity_filename(activity, activity_id, fmt)
            filepath = os.path.join(output_dir, filename)
            try:
                b64 = page.evaluate(
                    """(args) => fetch(args.url, {credentials:'include', headers:{'Accept':'application/octet-stream'}})
                        .then(r => { if(!r.ok) throw new Error('dl ' + r.status); return r.arrayBuffer(); })
                        .then(bytes => {
                            let bin=''; const chunk=0x8000; const arr=new Uint8Array(bytes);
                            for(let i=0;i<arr.length;i+=chunk){ bin+=String.fromCharCode.apply(null, arr.subarray(i,i+chunk)); }
                            return btoa(bin);
                        })""",
                    {
                        "url": f"{api_base}/download-service/files/activity/{activity_id}?format={fmt}",
                    },
                )
                data = base64.b64decode(b64)
                if not data:
                    logger.warning("活动 %s 下载内容为空，跳过", activity_id)
                    continue
                with open(filepath, "wb") as fb:
                    fb.write(data)
                downloaded.append(filepath)
                logger.info("已下载 Garmin 跑步活动: %s (%d bytes)", filename, len(data))
            except Exception as e:  # noqa: BLE001
                logger.warning("下载活动 %s 失败: %s", activity_id, e)
                continue
            if len(downloaded) >= count:
                break

        return downloaded
    finally:
        browser.close()
        p.stop()


def _do_login(page, username, password, domain, timeout=300):
    """内部登录流程（被 pull_recent_runs_browser 复用，headless 时若遇 MFA 会抛错）。"""
    sso = f"https://sso.{domain}/sso/signin"
    page.goto(sso, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector('input[name="username"]', timeout=30000)
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:  # noqa: BLE001
        pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            url = page.url
            if "connect.garmin" in url or _session_valid(page, domain):
                return True
            if "verifyMFA" in url or _safe_query(page, 'input[name="mfa-code"]'):
                # 无头模式下无法交互输入 MFA → 抛错，提示改用可见浏览器登录
                raise RuntimeError(
                    "检测到两步验证，但当前为无头模式无法输入验证码。"
                    "请调用 /api/data/garmin_login（可见浏览器）完成登录。"
                )
            err = _safe_query(page, ".error-text, .alert-error, [class*='error']")
            if err:
                msg = _safe_text(err)
                if msg:
                    raise RuntimeError(f"Garmin 登录失败：{msg}")
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug("登录轮询中页面跳转，重试：%s", e)
            time.sleep(2)
            continue
        time.sleep(3)
    raise RuntimeError("浏览器登录超时")


if __name__ == "__main__":
    print("=" * 50)
    print("Garmin 浏览器同步自测")
    print("=" * 50)
    if not engine_available():
        print("未安装 playwright，请先：pip install playwright && playwright install chromium")
    else:
        files = pull_recent_runs_browser(count=3, headless=False)
        print(f"已下载 {len(files)} 个文件：")
        for f in files:
            print("  -", f)
