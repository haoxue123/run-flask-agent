"""运动文件解析器包 — 统一入口与共享工具函数"""
import os

from parsers.ingest import ingest_directory, ingest_fit_stream


def _safe_int(value):
    """安全转整数，失败返回 None。"""
    if value is None:
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _safe_float(value):
    """安全转浮点，失败返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _mean(values):
    """计算平均值，空列表返回 None。"""
    values = [v for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _parse_pace_value(value):
    """将配速值统一转为秒/公里整数。支持整数或 '6:17' 格式字符串。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).replace("'", ":").replace('"', "").replace("''", ":")
    parts = [p for p in s.split(":") if p != ""]
    if len(parts) == 2:
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            return None
    if len(parts) == 1:
        try:
            return int(float(parts[0]))
        except ValueError:
            return None
    return None


def parse_activity_file(filepath):
    """根据文件扩展名调度对应的解析器。单个文件解析失败返回 None。"""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".gpx":
            from parsers.gpx import parse_gpx
            return parse_gpx(filepath)
        elif ext == ".tcx":
            from parsers.tcx import parse_tcx
            return parse_tcx(filepath)
        elif ext == ".fit":
            from parsers.fit import parse_fit
            return parse_fit(filepath)
        elif ext == ".csv":
            from parsers.csv import parse_csv
            return parse_csv(filepath)
    except Exception as e:
        print(f"  [警告] 解析失败 {os.path.basename(filepath)}: {e}")
        return None
    return None