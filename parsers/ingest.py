"""流式运动文件摄入管线（FIT / GPX / TCX / CSV）
============================================================
设计目标（第一性原理）：
  数据接入是一等公民，但真实文件可能又多又大（多年活动批量上传）。
  核心约束：
    1. 原始文件绝不全量载入内存 —— 逐文件处理 + 逐 record 生成器，使跨文件内存恒定。
    2. 原始大文件不进 ChromaDB；只把"瘦 JSON"（关键字段 + 汇总）落盘 data/uploads/。
    3. 解析失败单文件隔离，不影响整批。

说明：fitparse 对单文件本身会有一定缓冲；本管线通过"逐文件处理 + 生成器"，
保证跨大量文件时内存不随文件数线性增长。若需单文件零拷贝，可换底层解析器。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional


def _summarize(records: List[dict]) -> Dict[str, Any]:
    """从流式读取的记录聚合出摘要（与 parse_fit 口径一致，便于复用）。"""
    dists = [r["distance_m"] for r in records if r.get("distance_m") is not None]
    hrs = [r["heart_rate"] for r in records if r.get("heart_rate") is not None]
    spds = [r["speed_mps"] for r in records if r.get("speed_mps") is not None]
    cads = [r["cadence"] for r in records if r.get("cadence") is not None]
    alts = [r["altitude_m"] for r in records if r.get("altitude_m") is not None]

    t0 = next((r["timestamp"] for r in records if r.get("timestamp") is not None), None)
    t1 = next((r["timestamp"] for r in reversed(records) if r.get("timestamp") is not None), None)

    distance_km = round(max(dists) / 1000.0, 2) if dists else 0.0
    duration_s = round((t1 - t0).total_seconds(), 1) if (t0 and t1) else 0.0
    avg_speed = sum(spds) / len(spds) if spds else None
    pace = None
    if avg_speed:
        pace = round(1000.0 / avg_speed, 1)
    elif distance_km > 0 and duration_s:
        pace = round(duration_s / distance_km, 1)

    return {
        "distance_km": distance_km,
        "duration_s": duration_s,
        "avg_heart_rate": round(sum(hrs) / len(hrs), 1) if hrs else None,
        "max_heart_rate": max(hrs) * 1 if hrs else None,
        "avg_cadence": round(sum(cads) / len(cads), 1) if cads else None,
        "avg_altitude_m": round(sum(alts) / len(alts), 1) if alts else None,
        "avg_pace_sec_per_km": pace,
        "point_count": len(records),
    }


def ingest_fit_stream(filepath: str, out_dir: str, downsample: int = 0) -> Dict[str, Any]:
    """流式摄入单个 FIT 文件：逐 record 读取，写出瘦 JSON 到 out_dir。

    downsample > 0 时仅保留每 N 个点（应对超长活动，默认全保留）。
    返回 {"path", "summary", "kept_points"}；解析失败返回 {"error", "path"}。
    瘦 JSON 落在 data/uploads/（已被 .gitignore 排除，不会被提交）。
    """
    from parsers.fit import iter_fit_records

    try:
        kept: List[dict] = []
        for i, rec in enumerate(iter_fit_records(filepath)):
            if downsample and i % max(1, downsample) != 0:
                continue
            kept.append(rec)
        summary = _summarize(kept)
        name = os.path.splitext(os.path.basename(filepath))[0]
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, name + ".json")
        payload = {"source": name, "format": "fit", "summary": summary, "records": kept}
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, default=str)
        return {"path": out_path, "summary": summary, "kept_points": len(kept)}
    except Exception as e:  # noqa: BLE001 — 单文件隔离，避免整批中断
        return {"error": str(e), "path": filepath}


def ingest_directory(fit_dir: str, out_dir: str, downsample: int = 0) -> List[Dict[str, Any]]:
    """批量摄入目录下所有 .fit 文件（逐文件流式）。返回每个文件的摄入结果列表。"""
    results: List[Dict[str, Any]] = []
    if not os.path.isdir(fit_dir):
        return results
    for fn in sorted(os.listdir(fit_dir)):
        if fn.lower().endswith(".fit"):
            results.append(ingest_fit_stream(os.path.join(fit_dir, fn), out_dir, downsample))
    return results
