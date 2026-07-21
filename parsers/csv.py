"""CSV 文件解析器（佳明 Connect 导出格式）"""
import os
import csv
from datetime import datetime
from parsers import _safe_int, _safe_float, _parse_pace_value


def _col_index(headers, keywords):
    """在表头中查找包含任一关键字的列索引（忽略大小写）。"""
    for kw in keywords:
        for i, h in enumerate(headers):
            if kw.lower() in h.lower():
                return i
    return None


def _parse_duration(val):
    """将 HH:MM:SS 或 MM:SS 转为分钟数（秒数支持小数如 19.0）。"""
    if not val:
        return None
    parts = val.strip().split(":")
    if len(parts) == 3:
        return int(parts[0]) * 60 + int(parts[1]) + float(parts[2]) / 60
    elif len(parts) == 2:
        return int(parts[0]) + float(parts[1]) / 60
    return None


def parse_csv(filepath):
    """解析佳明 CSV 导出文件，返回跑步记录列表。"""
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader, [])
        if not headers:
            return None

        idx_date = _col_index(headers, ["date", "日期"])
        idx_title = _col_index(headers, ["title", "标题", "活动名称"])
        idx_distance = _col_index(headers, ["distance", "距离"])
        idx_time = _col_index(headers, ["time", "时间", "duration", "时长"])
        idx_avg_hr = _col_index(headers, ["avg hr", "平均心率", "average heart rate"])
        idx_max_hr = _col_index(headers, ["max hr", "最大心率", "maximum heart rate"])
        idx_pace = _col_index(headers, ["avg pace", "平均配速", "平均速度", "average pace"])
        idx_cadence = _col_index(headers, ["cadence", "步频", "run cadence"])

        runs = []
        for row in reader:
            if not row or all(c.strip() == "" for c in row):
                continue

            date_str = None
            if idx_date is not None and idx_date < len(row):
                raw = row[idx_date].strip()
                if raw:
                    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
                        try:
                            date_str = datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                            break
                        except ValueError:
                            continue
                    if not date_str:
                        date_str = raw[:10]

            distance_km = None
            if idx_distance is not None and idx_distance < len(row):
                val = _safe_float(row[idx_distance])
                if val:
                    distance_km = round(val, 2)

            duration_min = None
            if idx_time is not None and idx_time < len(row):
                duration_min = _parse_duration(row[idx_time])

            avg_hr = None
            if idx_avg_hr is not None and idx_avg_hr < len(row):
                avg_hr = _safe_int(row[idx_avg_hr])
            max_hr = None
            if idx_max_hr is not None and idx_max_hr < len(row):
                max_hr = _safe_int(row[idx_max_hr])

            pace = None
            if idx_pace is not None and idx_pace < len(row):
                pace = _parse_pace_value(row[idx_pace])

            avg_cad = None
            if idx_cadence is not None and idx_cadence < len(row):
                avg_cad = _safe_int(row[idx_cadence])

            name = os.path.splitext(os.path.basename(filepath))[0]
            if idx_title is not None and idx_title < len(row):
                t = row[idx_title].strip()
                if t:
                    name = t

            runs.append({
                "日期": date_str,
                "距离_km": distance_km,
                "时长_分钟": duration_min,
                "平均配速_秒每公里": pace,
                "平均心率": avg_hr,
                "最大心率": max_hr,
                "平均步频": avg_cad,
                "活动名称": name,
            })

    return runs if runs else None