"""
数据接入工具模块（文件导入 + 手动输入双模式）
============================================================
本模块是"工具使用（Tool Use）"Agentic 模式的具体实现，
替代了早期基于 Garmin API 的 garmin_tool.py。

什么是工具使用模式？
--------------------
AI 本身无法直接访问你的运动数据。工具使用模式就是：
由 Python 代码主动解析本地运动文件 / 读取手动配置，
把真实跑步数据取回来，再喂给 AI 做推理。
文件解析同样是合法的"工具使用"——Garmin API 只是手段之一。

双模式自动选择：
  - 文件模式（优先）：解析 data/ 目录下 GPX/TCX/FIT 运动文件
  - 手动模式（兜底）：读取 data/manual_input.json 配置

支持的运动文件格式：
  - GPX：GPS 交换格式（XML），所有平台通用
  - TCX：Training Center XML（Garmin 专有 XML）
  - FIT：Garmin 二进制格式（数据最丰富）

核心函数（与 garmin_tool 保持接口一致）：
    get_recent_runs(client=None, limit=5)   获取最近 N 次跑步（已清洗）
    build_runner_profile(client=None)       汇总数据，构建"跑者画像"喂给 AI
    get_data_source_info()                  返回当前数据源模式与降级说明
    format_pace(pace_s_per_km)              配速可读化
============================================================
"""

import os
import json
import threading
from datetime import datetime
from dotenv import load_dotenv

# 解析器导入
from parsers import parse_activity_file, _safe_int, _safe_float, _mean, _parse_pace_value


# ============================================================
# 常量定义
# ============================================================

# 数据目录：默认为本项目下的 data/ 文件夹
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 支持的运动文件扩展名
SUPPORTED_EXTS = (".gpx", ".tcx", ".fit", ".csv")

# 跑步记录的标准字段（与 garmin_tool 保持一致）
RUN_RECORD_KEYS = (
    "日期", "距离_km", "时长_分钟", "平均配速_秒每公里",
    "平均心率", "最大心率", "平均步频", "活动名称",
)

# 运行时缓存：最近一次构建画像时的数据源信息
_last_source_info = None

# 画像缓存：基于文件 mtime 的内存缓存，避免每次请求重建画像
# 结构: {"profile": dict, "files_mtime": {filepath: mtime}, "manual_mtime": float}
_profile_cache = None

# 缓存读写锁（threaded=True 下多线程并发访问全局缓存需加锁）
_profile_cache_lock = threading.Lock()


# ============================================================
# 通用小工具（已迁移到 parsers 包，此处仅为兼容重新导出）
# ============================================================

# _safe_int, _safe_float, _mean, _parse_pace_value 已从 parsers 导入


def _scan_activity_files(data_dir):
    """扫描数据目录及其子目录下所有支持的运动文件，返回排序后的路径列表。

    用 pathlib.rglob 单次遍历，按扩展名大小写不敏感过滤，避免多次 glob 调用。
    """
    if not os.path.isdir(data_dir):
        return []
    from pathlib import Path
    files = sorted(
        str(p) for p in Path(data_dir).rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    )
    return files


def _empty_health():
    """返回空的健康指标结构。"""
    return {
        "静息心率": None,
        "步数": None,
        "压力平均值": None,
        "睡眠时长_小时": None,
        "身体电量": None,
    }


def _empty_fitness():
    """返回空的体能指标结构。"""
    return {
        "VO2max": None,
        "健身年龄": None,
        "训练准备度": None,
        "训练状态": None,
    }


# ============================================================
# 文件解析层（已迁移到 parsers 包，通过 parse_activity_file 调度）
# ============================================================

# _parse_activity_file, _parse_gpx, _parse_tcx, _parse_fit, _parse_csv
# 已提取到 parsers/ 包中，通过 from parsers import parse_activity_file 使用


# ============================================================
# 手动加载层
# ============================================================

def _load_manual_config(data_dir):
    """读取 data/manual_input.json，不存在返回 None。"""
    path = os.path.join(data_dir, "manual_input.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _manual_runs(cfg):
    """从手动配置解析跑步记录，配速支持字符串写法自动转换。"""
    runs = []
    for r in cfg.get("近期跑步数据", []):
        runs.append({
            "日期": r.get("日期"),
            "距离_km": r.get("距离_km"),
            "时长_分钟": r.get("时长_分钟"),
            "平均配速_秒每公里": _parse_pace_value(r.get("平均配速_秒每公里")),
            "平均心率": r.get("平均心率"),
            "最大心率": r.get("最大心率"),
            "平均步频": r.get("平均步频"),
            "活动名称": r.get("活动名称", "手动记录"),
        })
    # 过滤无日期的记录，按日期倒序
    runs = [r for r in runs if r.get("日期")]
    runs.sort(key=lambda r: r["日期"], reverse=True)
    return runs


def _manual_health(cfg):
    """从手动配置读取健康指标。"""
    health = cfg.get("健康指标", {})
    return {
        "静息心率": health.get("静息心率"),
        "步数": health.get("步数"),
        "压力平均值": health.get("压力平均值"),
        "睡眠时长_小时": health.get("睡眠时长_小时"),
        "身体电量": health.get("身体电量"),
    }


def _manual_fitness(cfg):
    """从手动配置读取体能指标。"""
    fitness = cfg.get("体能指标", {})
    return {
        "VO2max": fitness.get("VO2max"),
        "健身年龄": fitness.get("健身年龄"),
        "训练准备度": fitness.get("训练准备度"),
        "训练状态": fitness.get("训练状态"),
    }


def _resolve_target_time(cfg):
    """
    解析目标完赛时间，优先级：
    manual_input.json > .env TARGET_FINISH_TIME > 默认 06:00:00
    """
    if cfg:
        t = cfg.get("目标", {}).get("目标完赛时间")
        if t:
            return t
    load_dotenv()
    return os.getenv("TARGET_FINISH_TIME", "06:00:00")


# ============================================================
# 公共接口层
# ============================================================

def get_recent_runs(client=None, limit=5, data_dir=None, files=None):
    """
    获取最近 N 次跑步活动（已清洗）。

    参数：
        client:  仅向后兼容保留，不使用（原 garmin_tool 需要 Garmin client）
        limit:   获取最近几次（默认 5）
        data_dir: 数据目录，默认为项目下 data/
        files:   预扫描的文件列表，传入则跳过 _scan_activity_files 调用

    返回：
        list[dict]，每条跑步记录包含 8 个字段：
        日期、距离_km、时长_分钟、平均配速_秒每公里、
        平均心率、最大心率、平均步频、活动名称
    """
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR

    if files is None:
        files = _scan_activity_files(data_dir)

    runs = []
    for filepath in files:
        rec = parse_activity_file(filepath)
        if rec is None:
            continue
        # CSV 文件返回多条记录列表，其他格式返回单条 dict
        if isinstance(rec, list):
            runs.extend(r for r in rec if r.get("日期"))
        elif rec.get("日期"):
            runs.append(rec)

    # 去重（按 日期+距离+时长 组合键，避免重复文件导致同一条记录出现多次）
    seen = set()
    unique_runs = []
    for r in runs:
        key = (r.get("日期"), r.get("距离_km"), r.get("时长_分钟"))
        if key not in seen:
            seen.add(key)
            unique_runs.append(r)

    # 按日期倒序，取前 limit 条
    unique_runs.sort(key=lambda r: r["日期"], reverse=True)
    return unique_runs[:limit]


def build_runner_profile(client=None):
    """
    汇总所有数据，构建结构化的"跑者画像"。

    这是工具使用模式的最终产出——把分散的运动数据整合成一份
    精简、结构化的跑者档案，直接作为 LLM 的输入上下文。

    参数：
        client: 仅向后兼容保留，不使用

    返回：
        dict，跑者画像，结构与 garmin_tool 完全一致：
        {
            "目标": {"目标完赛时间": "06:00:00"},
            "基本信息": {"最近跑步次数": 5, "最近一次跑步日期": "..."},
            "近期跑步数据": [...],
            "健康指标": {...},
            "体能指标": {...}
        }
    """
    global _last_source_info, _profile_cache

    data_dir = DEFAULT_DATA_DIR
    os.makedirs(data_dir, exist_ok=True)

    # --- 扫描运动文件（单次扫描，缓存命中时复用）---
    activity_files = _scan_activity_files(data_dir)

    # --- 缓存检查：基于文件 mtime 判是否需重建 ---
    if _profile_cache is not None:
        with _profile_cache_lock:
            current_mtimes = {}
            for fp in activity_files:
                try:
                    current_mtimes[fp] = os.path.getmtime(fp)
                except OSError:
                    current_mtimes[fp] = 0
            manual_path = os.path.join(data_dir, "manual_input.json")
            current_manual_mtime = os.path.getmtime(manual_path) if os.path.isfile(manual_path) else 0

            # 比对：文件列表和 mtime 都一致则命中缓存
            cached_mtimes = _profile_cache.get("files_mtime", {})
            if (set(current_mtimes.keys()) == set(cached_mtimes.keys()) and
                current_mtimes == cached_mtimes and
                _profile_cache.get("manual_mtime") == current_manual_mtime):
                return _profile_cache["profile"]

    print("正在构建跑者画像...")
    # 尝试加载手动配置
    manual_cfg = _load_manual_config(data_dir)

    if activity_files:
        # 文件模式：解析运动文件获取跑步数据
        print(f"  模式：文件导入（找到 {len(activity_files)} 个运动文件）")
        recent_runs = get_recent_runs(limit=5, data_dir=data_dir, files=activity_files)

        # 健康指标/体能指标：运动文件无法获取，尝试用 manual_input.json 补充
        if manual_cfg:
            health = _manual_health(manual_cfg)
            fitness = _manual_fitness(manual_cfg)
            health_source = "已从 manual_input.json 补充"
            fitness_source = "已从 manual_input.json 补充"
        else:
            health = _empty_health()
            fitness = _empty_fitness()
            health_source = "运动文件不含日常健康数据，已置 None（可在 manual_input.json 补充）"
            fitness_source = "运动文件不含 VO2max 等高级指标，已置 None"

        mode = "文件导入"

    elif manual_cfg:
        # 手动模式：全部从 manual_input.json 读取
        print("  模式：手动输入（从 manual_input.json 读取）")
        recent_runs = _manual_runs(manual_cfg)
        health = _manual_health(manual_cfg)
        fitness = _manual_fitness(manual_cfg)
        health_source = "来自 manual_input.json"
        fitness_source = "来自 manual_input.json"
        mode = "手动输入"

    else:
        # 无数据源
        raise FileNotFoundError(
            "未找到任何数据源。请：\n"
            "  1. 将 GPX/TCX/FIT 运动文件放入 data/ 目录；或\n"
            "  2. 复制 data/manual_input.example.json 为 data/manual_input.json 并填写。"
        )

    # 解析目标信息
    target_time = _resolve_target_time(manual_cfg)
    target_race = None
    target_distance = None
    if manual_cfg:
        target_race = (manual_cfg.get("目标") or {}).get("目标比赛")
        target_distance = (manual_cfg.get("目标") or {}).get("目标距离")

    # 组装跑者画像
    profile = {
        "目标": {
            "目标完赛时间": target_time,
            "目标比赛": target_race,
            "目标距离": target_distance,
        },
        "基本信息": {
            "最近跑步次数": len(recent_runs),
            "最近一次跑步日期": recent_runs[0]["日期"] if recent_runs else None,
        },
        "近期跑步数据": recent_runs,
        "健康指标": health,
        "体能指标": fitness,
    }

    # 缓存数据源信息
    _last_source_info = {
        "模式": mode,
        "运动文件数": len(activity_files),
        "文件列表": [os.path.basename(f) for f in activity_files],
        "健康指标说明": health_source,
        "体能指标说明": fitness_source,
    }

    # 更新画像缓存：记录当前文件 mtime
    cache_mtimes = {}
    for fp in activity_files:
        try:
            cache_mtimes[fp] = os.path.getmtime(fp)
        except OSError:
            cache_mtimes[fp] = 0
    manual_path = os.path.join(data_dir, "manual_input.json")
    cache_manual_mtime = os.path.getmtime(manual_path) if os.path.isfile(manual_path) else 0
    with _profile_cache_lock:
        _profile_cache = {
            "profile": profile,
            "files_mtime": cache_mtimes,
            "manual_mtime": cache_manual_mtime,
        }

    print("跑者画像构建完成！")
    return profile


def get_data_source_info(data_dir=None):
    """
    返回当前数据源的模式与降级说明（供 Notebook 展示）。

    返回：
        dict，包含：
        {
            "模式": "文件导入" / "手动输入" / "无数据",
            "运动文件数": 3,
            "文件列表": ["run1.gpx", "run2.tcx"],
            "健康指标说明": "...",
            "体能指标说明": "..."
        }
    """
    if data_dir is None:
        data_dir = DEFAULT_DATA_DIR

    # 优先返回缓存的信息（build_runner_profile 调用后）
    if _last_source_info:
        return _last_source_info

    # 独立扫描一次
    files = _scan_activity_files(data_dir)
    manual = _load_manual_config(data_dir)

    if files:
        mode = "文件导入"
        file_list = [os.path.basename(f) for f in files]
        if manual:
            health_note = "已从 manual_input.json 补充"
            fitness_note = "已从 manual_input.json 补充"
        else:
            health_note = "运动文件不含日常健康数据，已置 None（可在 manual_input.json 补充）"
            fitness_note = "运动文件不含 VO2max 等高级指标，已置 None"
    elif manual:
        mode = "手动输入"
        file_list = []
        health_note = "来自 manual_input.json"
        fitness_note = "来自 manual_input.json"
    else:
        mode = "无数据"
        file_list = []
        health_note = "无任何数据源"
        fitness_note = "无任何数据源"

    return {
        "模式": mode,
        "运动文件数": len(files),
        "文件列表": file_list,
        "健康指标说明": health_note,
        "体能指标说明": fitness_note,
    }


def format_pace(pace_s_per_km):
    """将秒/公里转为 '6'15\"/km' 这样的人类可读格式。"""
    if not pace_s_per_km or pace_s_per_km == 0:
        return "未知"
    minutes = int(pace_s_per_km // 60)
    seconds = int(pace_s_per_km % 60)
    return f"{minutes}'{seconds:02d}\"/km"


def invalidate_profile_cache():
    """清除画像缓存，强制下次 build_runner_profile() 重新构建。
    
    在数据上传/手动输入/计划生成后调用，确保缓存失效。
    """
    global _profile_cache, _last_source_info
    with _profile_cache_lock:
        _profile_cache = None
        _last_source_info = None


# ============================================================
# 模块自测：直接运行本文件可快速验证
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("数据接入工具模块自测（文件+手动双模式）")
    print("=" * 50)

    # 显示数据源信息
    print("\n--- 数据源信息 ---")
    info = get_data_source_info()
    print(json.dumps(info, ensure_ascii=False, indent=2))

    # 获取最近跑步记录
    print("\n--- 最近跑步记录 ---")
    try:
        runs = get_recent_runs(limit=5)
        if runs:
            for run in runs:
                pace = format_pace(run.get("平均配速_秒每公里"))
                print(f"  {run['日期']} | {run['距离_km']}km | {pace} | 心率{run.get('平均心率')}")
        else:
            print("  未找到跑步记录")
    except Exception as e:
        print(f"  错误：{e}")

    # 构建完整跑者画像
    print("\n--- 完整跑者画像 ---")
    try:
        profile = build_runner_profile()
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    except FileNotFoundError as e:
        print(f"  {e}")
        print("\n  提示：请将运动文件放入 data/ 目录，")
        print("  或复制 data/manual_input.example.json 为 data/manual_input.json 并填写。")
