"""GPX 文件解析器（GPS 交换格式）"""
import os
from parsers import _safe_int, _mean


def parse_gpx(filepath):
    """解析 GPX 文件，使用 gpxpy 库提取移动距离、时间、心率、步频。"""
    import gpxpy

    with open(filepath, "r", encoding="utf-8") as f:
        gpx = gpxpy.parse(f)

    moving = gpx.get_moving_data()
    moving_time = moving.moving_time if moving else 0
    moving_distance = moving.moving_distance if moving else 0

    distance_km = round(moving_distance / 1000, 2) if moving_distance else 0
    duration_min = round(moving_time / 60, 1) if moving_time else 0
    pace = round(moving_time / distance_km, 1) if distance_km > 0 else 0

    first_time = None
    for trk in gpx.tracks:
        for seg in trk.segments:
            for pt in seg.points:
                if pt.time:
                    first_time = pt.time
                    break
            if first_time:
                break
        if first_time:
            break
    date_str = first_time.date().isoformat() if first_time else None

    hrs, cads = [], []
    for trk in gpx.tracks:
        for seg in trk.segments:
            for pt in seg.points:
                if pt.extensions:
                    for ext in pt.extensions:
                        for child in ext.iter():
                            local = child.tag.split("}")[-1].lower()
                            if local == "hr":
                                val = _safe_int(child.text)
                                if val:
                                    hrs.append(val)
                            elif local in ("cad", "cadence"):
                                val = _safe_int(child.text)
                                if val:
                                    cads.append(val)

    name = None
    if gpx.tracks and gpx.tracks[0].name:
        name = gpx.tracks[0].name
    else:
        name = os.path.splitext(os.path.basename(filepath))[0]

    return {
        "日期": date_str,
        "距离_km": distance_km,
        "时长_分钟": duration_min,
        "平均配速_秒每公里": pace,
        "平均心率": _mean(hrs) if hrs else None,
        "最大心率": max(hrs) if hrs else None,
        "平均步频": _mean(cads) if cads else None,
        "活动名称": name,
    }