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
import re
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


def _has_local_session():
    """本地是否已有可用的 Garmin 会话（浏览器 Cookie 或 garth 令牌）。

    无头模式无法交互输入两步验证（MFA）验证码，若本地也无会话可 resume，
    自动登录必然卡死或失败。此函数用于在发起无头同步前快速判断，
    避免前端「立即同步」在无会话环境下空等 60s。
    """
    if os.path.isfile(BROWSER_COOKIE_PATH) and os.path.getsize(BROWSER_COOKIE_PATH) > 0:
        return True
    garth_home = os.getenv("GARTH_HOME", os.path.join(DEFAULT_OUTPUT_DIR, ".garth"))
    if os.path.isdir(garth_home) and bool(os.listdir(garth_home)):
        return True
    return False


def _detect_default_browser():
    """检测 Windows 系统默认浏览器，返回 (channel, executable_path)。

    channel 用于 Playwright 官方支持通道（chrome/msedge）;
    executable_path 用于其它 Chromium 内核浏览器（如 Tabbit、360 等）。
    优先使用用户日常浏览器可降低 Garmin SSO 风控概率（已登录态、历史 Cookie、
    扩展/指纹都更接近真实用户），也避免硬弹 Chrome 带来的突兀感。
    """
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
            0,
            winreg.KEY_READ,
        ) as key:
            progid, _ = winreg.QueryValueEx(key, "ProgId")
        progid = str(progid)
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            f"{progid}\\shell\\open\\command",
            0,
            winreg.KEY_READ,
        ) as key:
            cmd, _ = winreg.QueryValueEx(key, None)
        # 提取命令行中的第一个可执行文件路径（通常被双引号包裹）
        m = re.search(r'"([^"]+\.exe)"', str(cmd), re.IGNORECASE)
        if not m:
            m = re.search(r'(\S+\.exe)', str(cmd), re.IGNORECASE)
        if m:
            exe = m.group(1)
            base = os.path.basename(exe).lower()
            if base in ("msedge.exe",):
                return "msedge", None
            if base in ("chrome.exe",):
                return "chrome", None
            # 其它可执行文件一律尝试用 executable_path 启动（需为 Chromium 内核）
            return None, exe
    except Exception as e:  # noqa: BLE001
        logger.debug("检测系统默认浏览器失败：%s", e)
    return None, None


def _guess_user_data_dir(exe):
    r"""根据浏览器可执行文件路径或名称猜测其 User Data 目录。

    常见浏览器路径：
        Chrome: %LOCALAPPDATA%\Google\Chrome\User Data
        Edge:   %LOCALAPPDATA%\Microsoft\Edge\User Data
        Tabbit: <exe 父目录的父目录>\User Data
    """
    if not exe:
        return None
    exe_lower = exe.lower()
    local_appdata = os.path.expandvars("%LOCALAPPDATA%")
    candidates = []
    if "tabbit" in exe_lower:
        # C:\Users\...\AppData\Local\Tabbit\Application\Tabbit Browser.exe
        # -> C:\Users\...\AppData\Local\Tabbit\User Data
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(exe)), "User Data"))
    elif "chrome" in exe_lower:
        candidates.append(os.path.join(local_appdata, "Google", "Chrome", "User Data"))
    elif "msedge" in exe_lower or "edge" in exe_lower:
        candidates.append(os.path.join(local_appdata, "Microsoft", "Edge", "User Data"))
    # 通用兜底：exe 所在目录的兄弟目录 User Data
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(exe)), "User Data"))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def _count_files(root):
    try:
        n = 0
        for _, _, files in os.walk(root):
            n += len(files)
        return n
    except Exception:  # noqa: BLE001
        return 0


def _is_profile_invalid(clone_dir):
    """判断复制后的 profile 是否有效：缺少 Default/ 或 Local State 即视为无效。"""
    has_default = os.path.isdir(os.path.join(clone_dir, "Default"))
    has_local_state = os.path.isfile(os.path.join(clone_dir, "Local State"))
    return not (has_default and has_local_state)


def _copy_profile(source, dest, full_copy=False):
    """复制浏览器 profile 中真正必要的文件到项目临时目录。

    从第一性原理看，绕过 Garmin SSO 风控只需要：
      - Cookies / Login Data（登录态、保存的密码）
      - Local Storage / Session Storage（站点本地数据）
      - Preferences / Secure Preferences（浏览器设置、证书等）
      - Network / TransportSecurity（TLS 状态）
    不需要复制整个 profile（Cache、GPUCache、Code Cache、Service Worker、
    IndexedDB、扩展二进制等可能占 1GB+）。

    参数：
        full_copy: 若精简复制仍被风控，可设 .env GARMIN_BROWSER_FULL_PROFILE_COPY=1
                   回退到完整复制。
    """
    import shutil

    # 顶层必须文件（体积很小）
    top_files = {
        "Local State",
        "Last Version",
        "First Run",
    }
    # Default profile 下必须文件/目录
    default_whitelist = {
        "Cookies",
        "Cookies-journal",
        "Login Data",
        "Login Data-journal",
        "Local Storage",
        "Session Storage",
        "Preferences",
        "Secure Preferences",
        "Network",
        "TransportSecurity",
        "Extension State",
        "Affiliation Database",
        "Affiliation Database-journal",
        "History",
        "History-journal",
        "Visited Links",
        "Top Sites",
        "Shortcuts",
        "Bookmarks",
        "Favicons",
        "Favicons-journal",
    }
    # 无论 full_copy 如何都跳过的大体积/无意义目录
    always_skip_dirs = {
        "Cache",
        "Code Cache",
        "GPUCache",
        "blob_storage",
        "Service Worker",
        "IndexedDB",
        "databases",
        "File System",
        "Webrtc Video Stats",
        "optimization_guide_model_and_features_store",
        "optimization_guide_prediction_model_download_store",
        "hyphen-data",
        "pnacl",
        "MEIPreload",
        "BrowserMetrics",
        "Crashpad",
        "default_partner_content",
        "GrShaderCache",
        "ShaderCache",
        "OnDeviceHeadSuggestModel",
        "Subresource Filter",
        "Web Applications",
        "Session Storage" if full_copy else "",  # Session Storage 通常很小，保留
    }
    always_skip_dirs.discard("")

    os.makedirs(dest, exist_ok=True)
    copied = updated = skipped = 0

    for root, dirs, files in os.walk(source):
        rel = os.path.relpath(root, source)
        rel_parts = rel.split(os.sep)
        current_dir = rel_parts[-1] if rel_parts else ""
        parent_dir = rel_parts[-2] if len(rel_parts) >= 2 else ""

        # 跳过黑名单目录
        dirs[:] = [d for d in dirs if d not in always_skip_dirs]

        # 决定当前目录是否复制
        should_copy = full_copy
        if not should_copy:
            if rel in (".", ""):
                should_copy = True  # User Data 顶层，只复制白名单文件
            elif rel.lower() == "default":
                should_copy = True  # Default profile 目录
            elif parent_dir.lower() == "default" and current_dir in default_whitelist:
                should_copy = True
            elif current_dir == "Local Storage" and parent_dir.lower() == "default":
                should_copy = True
            elif current_dir == "Session Storage" and parent_dir.lower() == "default":
                should_copy = True
            elif current_dir == "Network" and parent_dir.lower() == "default":
                should_copy = True

        if not should_copy:
            continue

        dst_dir = os.path.join(dest, rel) if rel not in (".", "") else dest
        os.makedirs(dst_dir, exist_ok=True)

        for f in files:
            # 顶层只复制白名单
            if rel in (".", "") and f not in top_files and not full_copy:
                continue
            # Default profile 下只复制白名单
            if rel.lower() == "default" and f not in default_whitelist and not full_copy:
                continue

            src_file = os.path.join(root, f)
            dst_file = os.path.join(dst_dir, f)
            try:
                if os.path.exists(dst_file):
                    if (
                        os.path.getmtime(src_file) > os.path.getmtime(dst_file)
                        or os.path.getsize(src_file) != os.path.getsize(dst_file)
                    ):
                        shutil.copy2(src_file, dst_file, follow_symlinks=False)
                        updated += 1
                else:
                    shutil.copy2(src_file, dst_file, follow_symlinks=False)
                    copied += 1
            except Exception:  # noqa: BLE001
                skipped += 1

    mode = "完整" if full_copy else "精简"
    logger.info("已%s复制浏览器 profile：新增 %d，更新 %d，跳过 %d", mode, copied, updated, skipped)
    return dest


def _launch(headless):
    r"""启动浏览器。优先使用系统默认浏览器并复用其日常 profile，可 .env 强制指定。

    环境变量：
        GARMIN_BROWSER_CHANNEL=msedge|chrome|chromium
        GARMIN_BROWSER_EXECUTABLE=C:\Path\To\Browser.exe
        GARMIN_BROWSER_USER_DATA_DIR=C:\Users\...\User Data
    """
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    # 强 stealth 启动参数：Cloudflare 在 POST 阶段会检测这些开关
    stealth_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        # 关键：禁用 Chromium 暴露自动化的子功能
        "--disable-features=IsolateOrigins,site-per-process,AutomationControlled",
        # 看起来更像普通 Chrome
        "--disable-infobars",
        "--disable-notifications",
        "--disable-popup-blocking",
        "--no-sandbox",
    ]

    # 1) .env 强制指定；2) 检测系统默认浏览器；3) 兜底 chrome -> msedge -> chromium
    configured_channel = (os.getenv("GARMIN_BROWSER_CHANNEL") or "").strip().lower() or None
    configured_exe = (os.getenv("GARMIN_BROWSER_EXECUTABLE") or "").strip() or None
    configured_user_data = (os.getenv("GARMIN_BROWSER_USER_DATA_DIR") or "").strip() or None
    default_channel, default_exe = _detect_default_browser()

    # 构建候选启动列表（每个元素：type, value, name）
    raw_attempts = []
    if configured_exe:
        raw_attempts.append(("exe", configured_exe, f".env executable ({configured_exe})"))
    if configured_channel:
        raw_attempts.append(("channel", configured_channel, f".env channel ({configured_channel})"))
    if default_exe:
        raw_attempts.append(("exe", default_exe, f"系统默认浏览器 ({default_exe})"))
    if default_channel and default_channel != configured_channel:
        raw_attempts.append(("channel", default_channel, f"系统默认 channel ({default_channel})"))
    for fallback in ("chrome", "msedge"):
        if fallback not in {configured_channel, default_channel}:
            raw_attempts.append(("channel", fallback, f"兜底 {fallback}"))

    browser = None
    context = None
    is_persistent = False
    last_err = None
    for kind, value, name in raw_attempts:
        try:
            logger.info("尝试启动浏览器：%s (headless=%s)", name, headless)
            kwargs = {
                "headless": headless,
                "args": stealth_args,
            }
            exe = value if kind == "exe" else None
            channel = value if kind == "channel" else None
            if exe:
                kwargs["executable_path"] = exe
            else:
                kwargs["channel"] = channel

            # 可见模式下，尝试复用浏览器日常 profile（这是绕过 Garmin 风控的关键）
            # 但若 .env 设了 GARMIN_BROWSER_SKIP_PROFILE_COPY=1，则用空 profile 启动
            # （适用于：profile 复制总崩溃 / 想用 Playwright 全新 profile + 用户手动登录）
            skip_profile = os.getenv("GARMIN_BROWSER_SKIP_PROFILE_COPY", "").strip().lower() in ("1", "true", "yes")
            user_data_dir = None
            if not headless and not skip_profile:
                user_data_dir = configured_user_data or _guess_user_data_dir(exe or value)
            if user_data_dir and os.path.isdir(user_data_dir):
                clone_dir = os.path.join(DEFAULT_OUTPUT_DIR, ".garmin_browser_profile_clone")
                full_copy = os.getenv("GARMIN_BROWSER_FULL_PROFILE_COPY", "").strip().lower() in ("1", "true", "yes")
                try:
                    _copy_profile(user_data_dir, clone_dir, full_copy=full_copy)
                except Exception as e:  # noqa: BLE001
                    logger.warning("复制浏览器 profile 失败（将尝试不复制启动）：%s", e)
                    clone_dir = user_data_dir
                # 若复制后 clone 仍无效（只有零碎 tmp 文件），降级到空 profile
                if _is_profile_invalid(clone_dir):
                    logger.warning("clone 目录无效（含 %d 个零碎文件），降级用空 profile", _count_files(clone_dir))
                    clone_dir = os.path.join(DEFAULT_OUTPUT_DIR, ".garmin_browser_empty_profile")
                    os.makedirs(clone_dir, exist_ok=True)
                context = p.chromium.launch_persistent_context(
                    clone_dir,
                    **kwargs,
                    user_agent=ua,
                    locale="zh-CN",
                    viewport={"width": 1280, "height": 900},
                )
                is_persistent = True
                logger.info("浏览器启动成功（persistent context 复用 profile）：%s", name)
                break

            browser = p.chromium.launch(**kwargs)
            context = browser.new_context(
                user_agent=ua,
                locale="zh-CN",
                viewport={"width": 1280, "height": 900},
            )
            is_persistent = False
            logger.info("浏览器启动成功：%s", name)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.info("启动 %s 失败：%s", name, e)

    if context is None:
        names = [name for _, _, name in raw_attempts]
        raise RuntimeError(
            f"无法启动任何本机浏览器（已尝试 {names}）。"
            f"请确认已安装 Chrome/Edge/Tabbit 或设置 .env GARMIN_BROWSER_*。原始错误：{last_err}"
        )

    # 强 stealth init scripts：覆盖 Playwright 默认的所有"自动化痕迹"
    # 第一性原理：Cloudflare 在 POST 前会在浏览器里跑 JS 检测 navigator.*、
    # window.chrome、document.hasFocus() 等，缺一项就标 bot
    context.add_init_script("""
        // 1. webdriver 主特征
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

        // 2. plugins 必须非空（Playwright 默认空数组，一眼假）
        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                const arr = [
                    {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
                    {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
                    {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''},
                ];
                arr.length = 3;
                return arr;
            }
        });

        // 3. languages 真实用户是多个
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en']
        });

        // 4. window.chrome 存在且有 runtime
        window.chrome = window.chrome || {};
        window.chrome.runtime = window.chrome.runtime || {};
        window.chrome.app = window.chrome.app || {isInstalled: false};
        window.chrome.csi = window.chrome.csi || function(){};
        window.chrome.loadTimes = window.chrome.loadTimes || function(){};

        // 5. permissions query 默认行为修复
        const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                Promise.resolve({state: Notification.permission}) :
                originalQuery(parameters)
            );
        }

        // 6. WebGL 真实 vendor（避免被识别为 SwiftShader）
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, parameter);
        };
    """)
    return p, browser, context, is_persistent


def _save_cookies(context):
    try:
        cookies = context.cookies()
        with open(BROWSER_COOKIE_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logger.info("已保存浏览器会话 Cookie 到 %s", BROWSER_COOKIE_PATH)
    except Exception as e:  # noqa: BLE001
        logger.warning("保存浏览器 Cookie 失败：%s", e)


def _dump_diagnostics(page, tag: str):
    """失败时把当前页 HTML + 截图保存到 data/ 便于诊断。

    命名规则：garmin_{tag}_{timestamp}.html / .png
    """
    import datetime as _dt
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(DEFAULT_OUTPUT_DIR, f"garmin_{tag}_{ts}")
    try:
        html = page.content()
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(html)
        page.screenshot(path=base + ".png", full_page=True)
        logger.warning("已保存失败现场：%s.html / .png", base)
    except Exception as e:  # noqa: BLE001
        logger.warning("保存失败现场失败：%s", e)


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


def _detect_mfa(page, url):
    """鲁棒检测 Garmin 两步验证（MFA）页面，兼容中英文与不同字段命名。

    返回 True 表示当前处于 MFA 验证步骤（需要人工输入验证码）。
    """
    if not url:
        url = ""
    if "mfa" in url.lower() or "verify" in url.lower() or "otp" in url.lower():
        return True
    try:
        for sel in (
            'input[name*="mfa" i]', 'input[name*="otp" i]', 'input[name*="code" i]',
            'input[id*="mfa" i]', 'input[id*="otp" i]', 'input[id*="code" i]',
        ):
            if page.query_selector(sel):
                return True
    except Exception:  # noqa: BLE001
        pass
    try:
        txt = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:  # noqa: BLE001
        txt = ""
    if txt:
        for kw in ("验证码", "两步验证", "动态码", "安全码", "手机验证码",
                   "verification code", "one-time", "2-step", "two-step", "authenticator"):
            if kw.lower() in txt.lower():
                return True
    return False


def browser_login(headless=False, timeout=300):
    """
    启动（可见）浏览器完成 Garmin 登录，并把 Cookie 持久化。

    关键设计（第一性原理）：
      Cloudflare 在表单 POST 阶段会专门检测"自动化提交"行为——
      Playwright 的 page.fill() + page.click() 即便浏览器是真实 Chrome，
      提交速度、点击坐标、focus 事件序列都和人类不同，命中风控 → "An
      unexpected error has occurred."。
      正确做法：headless=False 模式下只打开页面，**不自动填表/不自动点**，
      让用户自己输入、自己点击、自己输两步验证码，Cloudflare 看到的是真
      人行为。cookie 一旦保存，后续 headless=True 复用即可零摩擦。

    参数：
      headless: False=可见浏览器让用户完成首登；True=已有 cookie 时静默
      timeout:  等待登录完成的秒数（默认 300=5 分钟）
    返回 True 表示登录成功。
    """
    username, password, is_cn = _get_credentials()
    if not username or not password:
        raise RuntimeError(
            "未配置 Garmin 账号。请在 .env 设置 GARMIN_USERNAME / GARMIN_PASSWORD"
        )
    domain = "garmin.cn" if is_cn else "garmin.com"

    p, browser, context, is_persistent = _launch(headless=headless)
    try:
        page = context.new_page()
        sso = f"https://sso.{domain}/sso/signin"
        logger.info("打开 Garmin 登录页：%s", sso)

        # ====== 关键：headless 模式必须已有 cookie，无则报错 ======
        if headless:
            if not _has_local_session():
                raise RuntimeError(
                    "无头模式需要先有浏览器 Cookie。请先运行可见登录：\n"
                    "  python -c \"import garmin_browser; garmin_browser.browser_login(headless=False)\""
                )
            # 已有 cookie：直接尝试访问 connect，验证是否已登录
            page.goto(f"https://connect.{domain}", wait_until="domcontentloaded", timeout=30000)
            if "connect.garmin" in page.url and _session_valid(page, domain):
                logger.info("复用本地 Cookie 已登录")
                return True
            # cookie 失效了：fall through 到可见模式
            logger.warning("本地 Cookie 已失效，降级到可见登录")

        # ====== 可见模式：不自动填表，让用户手动操作 ======
        try:
            page.goto(sso, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"无法打开 Garmin 登录页（请确认网络可访问 {sso}）：{e}"
            ) from e

        if not headless:
            logger.info(
                "请在弹出的浏览器窗口里手动完成登录（输入账号密码 + 两步验证码，"
                "然后点 Sign in）。脚本会一直等待直到你登录成功或超时。"
            )

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                url = page.url
                # 登录成功：跳回 connect 域
                if "connect.garmin" in url or _session_valid(page, domain):
                    _save_cookies(context)
                    logger.info("浏览器登录成功，Cookie 已保存")
                    return True
                # MFA：可见模式等待用户输入验证码；无头模式不应走到这里
                if _detect_mfa(page, url):
                    if headless:
                        raise RuntimeError("检测到两步验证（MFA），无头模式无法处理")
                    logger.info("检测到两步验证，请在浏览器窗口中输入验证码…")
                    time.sleep(3)
                    continue
                # 错误页：保存 HTML + 截图便于诊断
                err = _safe_query(page, ".error-text, .alert-error, [class*='error']")
                if err:
                    msg = _safe_text(err)
                    if msg:
                        _dump_diagnostics(page, "login_error")
                        raise RuntimeError(f"Garmin 登录失败：{msg}")
            except RuntimeError:
                raise
            except Exception as e:  # noqa: BLE001
                # 页面正在跳转、执行上下文被销毁，下一轮重试
                logger.debug("登录轮询中页面跳转，重试：%s", e)
                time.sleep(2)
                continue
            time.sleep(2)

        # 超时：保存现场便于诊断
        _dump_diagnostics(page, "login_timeout")
        raise RuntimeError(
            f"浏览器登录超时（{timeout}s 内未完成）。已保存页面快照到 data/garmin_login_*.html，"
            "请打开看看卡在哪一步。"
        )
    finally:
        if context:
            context.close()
        if browser:
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

    # 无头模式 + 本地无会话：必然卡在 MFA 或网络，直接给出清晰指引，
    # 避免前端「立即同步」空等整整 60s（page.goto 默认超时）。
    if headless and not _has_local_session():
        raise RuntimeError(
            "本地尚无 Garmin 会话，无法在无头模式下自动登录"
            "（账号开启两步验证时更需人工输入验证码）。\n"
            "请先在本机（有显示器）点击「登录 Garmin」按钮，或访问 POST /api/data/garmin_login "
            "完成一次可见浏览器登录；登录成功后会话会自动保存，之后自动同步即可复用，无需再次输入。"
        )

    username, password, is_cn = _get_credentials()
    domain = "garmin.cn" if is_cn else "garmin.com"
    api_base = f"https://connectapi.{domain}"

    p, browser, context, is_persistent = _launch(headless=headless)
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
        if context:
            context.close()
        if browser:
            browser.close()
        p.stop()


def _do_login(page, username, password, domain, timeout=300):
    """内部登录流程（被 pull_recent_runs_browser 复用，headless 时若遇 MFA 会抛错）。"""
    sso = f"https://sso.{domain}/sso/signin"
    try:
        page.goto(sso, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector('input[name="username"]', timeout=30000)
        page.fill('input[name="username"]', username)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"浏览器登录页加载/填写失败（网络不可达或登录表单字段变更）：{e}"
        ) from e
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
            if _detect_mfa(page, url):
                # 无头模式下无法交互输入 MFA → 抛错，提示改用可见浏览器登录
                raise RuntimeError(
                    "检测到两步验证（MFA），但当前为无头模式无法输入验证码。"
                    "请调用 /api/data/garmin_login（可见浏览器）或在本机运行 "
                    "python -c \"import garmin_browser; garmin_browser.browser_login(headless=False)\" 完成登录。"
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
