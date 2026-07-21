"""FIT 文件解析器（Garmin 二进制格式）"""
import os
from parsers import _mean


def parse_fit(filepath):
    """解析 FIT 文件，使用 fitparse 库。优先 session 汇总，无 session 时从 record 聚合。"""
    from fitparse import FitFile

    fitfile = FitFile(filepath)
    fitfile.parse()

    session = None
    for msg in fitfile.get_messages("session"):
        session = msg
        break

    if session:
        fields = {}
        for field in session.fields:
            if field.value is not None:
                fields[field.name] = field.value
        distance_m = fields.get("total_distance", 0) or 0
        duration_s = fields.get("total_timer_time") or fields.get("total_elapsed_time") or 0
        avg_hr = fields.get("avg_heart_rate")
        max_hr = fields.get("max_heart_rate")
        avg_cad = fields.get("avg_cadence") or fields.get("avg_running_cadence")
        avg_speed = fields.get("avg_speed") or fields.get("enhanced_avg_speed")
        start_time = fields.get("start_time")
    else:
        dists, hrs, cads, times, speeds = [], [], [], [], []
        for rec in fitfile.get_messages("record"):
            values = rec.get_values()
            if values.get("distance") is not None:
                dists.append(values["distance"])
            if values.get("heart_rate") is not None:
                hrs.append(values["heart_rate"])
            if values.get("cadence") is not None:
                cads.append(values["cadence"])
            if values.get("timestamp") is not None:
                times.append(values["timestamp"])
            if values.get("speed") is not None:
                speeds.append(values["speed"])

        distance_m = max(dists) if dists else 0
        if len(times) >= 2:
            duration_s = (times[-1] - times[0]).total_seconds()
        else:
            duration_s = 0
        avg_hr = _mean(hrs) if hrs else None
        max_hr = max(hrs) if hrs else None
        avg_cad = _mean(cads) if cads else None
        avg_speed = _mean(speeds) if speeds else None
        start_time = times[0] if times else None

    distance_km = round(distance_m / 1000, 2) if distance_m else 0
    duration_min = round(duration_s / 60, 1) if duration_s else 0

    if avg_speed and avg_speed > 0:
        pace = round(1000 / avg_speed, 1)
    elif distance_km > 0:
        pace = round(duration_s / distance_km, 1)
    else:
        pace = 0

    date_str = None
    if start_time and hasattr(start_time, "date"):
        date_str = start_time.date().isoformat()

    name = os.path.splitext(os.path.basename(filepath))[0]

    return {
        "日期": date_str,
        "距离_km": distance_km,
        "时长_分钟": duration_min,
        "平均配速_秒每公里": pace,
        "平均心率": avg_hr,
        "最大心率": max_hr,
        "平均步频": avg_cad,
        "活动名称": name,
    }