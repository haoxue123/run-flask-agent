"""TCX 文件解析器（Garmin Training Center XML）"""
import os
import xml.etree.ElementTree as ET
from parsers import _safe_int, _safe_float, _mean


def _findall_local(element, local_name):
    """按 local name 匹配元素，绕开命名空间差异。"""
    return [e for e in element.iter() if e.tag.split("}")[-1] == local_name]


def parse_tcx(filepath):
    """解析 TCX 文件，使用标准库 xml.etree.ElementTree。"""
    tree = ET.parse(filepath)
    root = tree.getroot()

    activities = _findall_local(root, "Activity")
    if not activities:
        return None
    activity = activities[0]

    total_time_s = 0
    for t in _findall_local(activity, "TotalTimeSeconds"):
        val = _safe_float(t.text)
        if val:
            total_time_s += val

    all_dists = [_safe_float(d.text) for d in _findall_local(activity, "DistanceMeters")]
    all_dists = [d for d in all_dists if d is not None]
    total_dist_m = max(all_dists) if all_dists else 0

    distance_km = round(total_dist_m / 1000, 2) if total_dist_m else 0
    duration_min = round(total_time_s / 60, 1) if total_time_s else 0
    pace = round(total_time_s / distance_km, 1) if distance_km > 0 else 0

    avg_hr = None
    max_hr = None
    avg_hr_elements = _findall_local(activity, "AverageHeartRateBpm")
    if avg_hr_elements:
        val_elements = _findall_local(avg_hr_elements[0], "Value")
        if val_elements:
            avg_hr = _safe_int(val_elements[0].text)
    max_hr_elements = _findall_local(activity, "MaximumHeartRateBpm")
    if max_hr_elements:
        val_elements = _findall_local(max_hr_elements[0], "Value")
        if val_elements:
            max_hr = _safe_int(val_elements[0].text)

    if avg_hr is None or max_hr is None:
        tp_hrs = []
        for v in _findall_local(activity, "Value"):
            val = _safe_int(v.text)
            if val:
                tp_hrs.append(val)
        if tp_hrs:
            avg_hr = avg_hr or _mean(tp_hrs)
            max_hr = max_hr or max(tp_hrs)

    cads = [_safe_int(t.text) for t in _findall_local(activity, "RunCadence")]
    cads = [c for c in cads if c is not None]
    avg_cad = _mean(cads) if cads else None

    id_elements = _findall_local(activity, "Id")
    date_str = None
    if id_elements and id_elements[0].text:
        date_str = id_elements[0].text[:10]

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