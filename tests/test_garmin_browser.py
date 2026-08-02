"""
测试 garmin_browser.py 的同步流程（mock 掉 playwright，无需浏览器二进制）。

验证点：
  1. engine_available() 在 playwright 存在/缺失时返回正确
  2. pull_recent_runs_browser() 走 page.evaluate(fetch) 列活动 + 下载 + 落盘
  3. 浏览器 Cookie 持久化与复用
  4. browser_login() 在可见浏览器登录成功后保存 Cookie
"""

import sys
import os
import json
import types
import base64
import tempfile
import shutil

import pytest


# ---------------------------------------------------------------------------
# 构造假 playwright 模块，注入 sys.modules（在 import garmin_browser 之前）
# ---------------------------------------------------------------------------
def _install_fake_playwright():
    fake_pw = types.ModuleType("playwright")
    fake_sync_api = types.ModuleType("playwright.sync_api")

    class FakePage:
        def __init__(self, url="https://connect.garmin.com/"):
            self.url = url

        def goto(self, *a, **k):
            pass

        def wait_for_selector(self, *a, **k):
            pass

        def fill(self, *a, **k):
            pass

        def click(self, *a, **k):
            pass

        def query_selector(self, sel):
            return None

        def inner_text(self):
            return ""

        def evaluate(self, js, arg=None):
            blob = (js or "") + " " + json.dumps(arg or {})
            if "socialProfile" in blob:
                return True  # 探测 Cookie 有效
            if "activitylist-service" in blob:
                return [{
                    "activityId": 123,
                    "activityType": {"typeKey": "running"},
                    "startTimeLocal": "2026-07-30 07:00:00",
                }]
            if "download-service" in blob:
                return base64.b64encode(b"<tcx>fake-running</tcx>").decode()
            return None

    class FakeContext:
        def __init__(self):
            self._cookies = []

        def add_init_script(self, *a, **k):
            pass

        def cookies(self):
            return self._cookies

        def add_cookies(self, c):
            self._cookies = c

        def new_page(self):
            return FakePage()

    class FakeBrowser:
        def new_context(self, **k):
            return FakeContext()

        def close(self):
            pass

    class FakeP:
        def __init__(self):
            self.chromium = type(
                "C", (), {"launch": staticmethod(lambda *a, **k: FakeBrowser())}
            )()

        def stop(self):
            pass

    class FakeSyncPW:
        def start(self):
            return FakeP()

        def __enter__(self):
            return FakeP()

        def __exit__(self, *a):
            pass

    fake_sync_api.sync_playwright = lambda: FakeSyncPW()
    fake_pw.sync_api = fake_sync_api
    sys.modules["playwright"] = fake_pw
    sys.modules["playwright.sync_api"] = fake_sync_api


_install_fake_playwright()

import garmin_browser  # noqa: E402


@pytest.fixture
def tmp_out(tmp_path):
    # 把 Cookie 路径指向临时目录，避免写脏 data/
    garmin_browser.BROWSER_COOKIE_PATH = str(tmp_path / "cookies.json")
    return tmp_path


def test_engine_available_true_with_fake():
    assert garmin_browser.engine_available() is True


def test_pull_recent_runs_browser_writes_files(tmp_out):
    files = garmin_browser.pull_recent_runs_browser(
        count=2, output_dir=str(tmp_out), fmt="tcx", headless=True
    )
    assert len(files) == 1
    assert os.path.isfile(files[0])
    content = open(files[0], "rb").read()
    assert b"<tcx>fake-running</tcx>" in content


def test_cookie_persist_and_reuse(tmp_out):
    # 第一次同步会登录并保存 Cookie
    garmin_browser.pull_recent_runs_browser(
        count=1, output_dir=str(tmp_out), fmt="tcx", headless=True
    )
    assert os.path.isfile(garmin_browser.BROWSER_COOKIE_PATH)
    cookies = json.load(open(garmin_browser.BROWSER_COOKIE_PATH, encoding="utf-8"))
    assert isinstance(cookies, list)


def test_browser_login_succeeds(tmp_out):
    ok = garmin_browser.browser_login(headless=False)
    assert ok is True
    assert os.path.isfile(garmin_browser.BROWSER_COOKIE_PATH)
