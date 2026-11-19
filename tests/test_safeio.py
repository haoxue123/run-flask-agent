"""
safeio 回归测试
================
锁定核心不变量：在启用 safe-delete 护栏（无回收站、删除失败即抛 OSError）的
Windows 沙箱运行时里，JSON 落盘与清理都不能因「删除」而中断。

这些测试写到系统临时目录，避免污染项目 data/ 目录。
"""

import os
import json
import tempfile

import safeio


def _tmp_path(name):
    return os.path.join(tempfile.gettempdir(), f"safeio_test_{name}.json")


def test_write_new_file():
    p = _tmp_path("new")
    safeio.safe_remove(p)
    safeio.safe_write_json(p, {"k": 1, "list": [1, 2, 3]})
    with open(p, "r", encoding="utf-8") as f:
        assert json.load(f)["k"] == 1
    safeio.safe_remove(p)


def test_overwrite_creates_bak_with_old_value():
    p = _tmp_path("over")
    safeio.safe_remove(p)
    safeio.safe_remove(p + ".bak")
    safeio.safe_write_json(p, {"v": 1})
    safeio.safe_write_json(p, {"v": 2})  # 覆盖写
    with open(p, "r", encoding="utf-8") as f:
        assert json.load(f)["v"] == 2
    # .bak 应保留旧值
    assert os.path.isfile(p + ".bak")
    with open(p + ".bak", "r", encoding="utf-8") as f:
        assert json.load(f)["v"] == 1
    safeio.safe_remove(p)
    safeio.safe_remove(p + ".bak")


def test_safe_remove_never_raises():
    # 不存在的文件：不抛
    safeio.safe_remove(_tmp_path("nope"))
    # 存在的文件：即使被护栏拦截，也不抛
    p = _tmp_path("exist")
    safeio.safe_write_json(p, {"x": 1})
    safeio.safe_remove(p)  # 若护栏拦截，这里必须静默吞掉


def test_purge_stray_temp_files_no_raise():
    # 扫描系统临时目录不应抛错（即使删不掉）
    safeio.purge_stray_temp_files(tempfile.gettempdir())
