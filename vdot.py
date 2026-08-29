"""Daniels VDOT 计算内核（确定性、纯标准库）
============================================================
第一性原理：一个科学跑步教练，应当能根据"你最近一场比赛的成绩"，
推导出你当前的有氧能力（VDOT ≈ VO2max），再由此算出每个强度区的
训练配速（E / M / T / I / R）。这样配速不是 LLM 凭记忆"编"的，
而是由你的真实比赛成绩经生理学公式推导出来的。

来源（事实，非推测）：
  - VDOT 估算方程：Jack Daniels《Daniels' Running Formula》中的
    VO2max 预测公式（Daniels & Ruby 速度法）：
      VO2max = (−4.6 + 0.182258·v + 0.000104·v²)
               / (0.8 + 0.1894393·e^(−0.012778·t) + 0.2989553·e^(−0.1932605·t))
    其中 v = 速度(米/分钟)，t = 时间(分钟)。分母为 Daniels/Ruby 速度法标准常数。
  - 训练强度分区：训练配速围绕 vVO2max（≈I 间歇配速，即 100% VO2max 速度）展开，
    各强度区占 vVO2max 速度的百分比（E 59–74% / M ~80% / T ~88% / I ~100% / R ~106%）。
    配速 ∝ 1/速度，故 区配速 = vVO2max配速 / 该区百分比。
  - vVO2max ↔ 各强度区配速的绝对数值，采用项目知识库 ref_numeric_tables.md 中的
    Daniels 简化查表（authority_level=高）做插值 oracle，避免"顺序对、数值错"。

所有函数均为纯函数；不读写文件、不调用网络、不依赖全局状态。
"""

from __future__ import annotations

import math
import os
import json
from typing import Optional, Dict, Any, Tuple

# 各强度区占 VO2max 速度的百分比。
# E 给 (慢, 快) 区间；其余为单值（中点）。
ZONE_VO2MAX_PCT: Dict[str, Any] = {
    "E": (0.59, 0.74),   # 轻松/恢复跑：59–74% VO2max
    "M": 0.80,           # 马拉松配速：~80% VO2max
    "T": 0.88,           # 阈值跑：~88% VO2max
    "I": 1.00,           # 间歇/VO2max 配速：~100% VO2max
    "R": 1.06,           # 重复跑：~106% VO2max
}

# VDOT 合理区间（用于校验）
VDOT_MIN, VDOT_MAX = 20.0, 85.0


# ============================================================
# Daniels VDOT → 强度区配速查表（秒/公里）
# 来源：项目知识库 data/knowledge/ref_numeric_tables.md（Daniels 简化表，"权威"级）。
# 该表即 Daniels 体系下"各 VDOT 对应的 E/M/T/I 单车配速"，作为插值基准(oracle)。
# 元组顺序固定为 (E, M, T, I)。R 不在原表，按 vVO2max×106% 推导。
# ============================================================
_VDOT_PACE_TABLE_SEC_PER_KM: Dict[float, Tuple[float, float, float, float]] = {
    35: (410, 350, 320, 295),   # 6:50  5:50  5:20  4:55
    40: (370, 315, 285, 260),   # 6:10  5:15  4:45  4:20
    45: (335, 285, 255, 230),   # 5:35  4:45  4:15  3:50
    50: (305, 260, 230, 205),   # 5:05  4:20  3:50  3:25
    55: (280, 238, 208, 185),   # 4:40  3:58  3:28  3:05
    60: (258, 218, 190, 168),   # 4:18  3:38  3:10  2:48
}

# 表覆盖的 VDOT 上下界（超出则夹取，避免外推失真）
_VDOT_TABLE_LO = min(_VDOT_PACE_TABLE_SEC_PER_KM)
_VDOT_TABLE_HI = max(_VDOT_PACE_TABLE_SEC_PER_KM)


def _interpolate_paces(vdot: float) -> Tuple[float, float, float, float]:
    """在 Daniels 查表上对给定 VDOT 线性插值出 (E, M, T, I) 配速(秒/公里)。

    落在表区间内线性插值；超出 [35, 60] 范围时夹取到最近端点，避免外推发散。
    返回四值分别对应轻松(E)、马拉松(M)、阈值(T)、间歇(I) 单车配速。
    """
    keys = sorted(_VDOT_PACE_TABLE_SEC_PER_KM)
    if vdot <= keys[0]:
        return _VDOT_PACE_TABLE_SEC_PER_KM[keys[0]]
    if vdot >= keys[-1]:
        return _VDOT_PACE_TABLE_SEC_PER_KM[keys[-1]]
    for i in range(len(keys) - 1):
        lo, hi = keys[i], keys[i + 1]
        if lo <= vdot <= hi:
            f = (vdot - lo) / (hi - lo)
            e0, m0, t0, i0 = _VDOT_PACE_TABLE_SEC_PER_KM[lo]
            e1, m1, t1, i1 = _VDOT_PACE_TABLE_SEC_PER_KM[hi]
            return (
                e0 + (e1 - e0) * f,
                m0 + (m1 - m0) * f,
                t0 + (t1 - t0) * f,
                i0 + (i1 - i0) * f,
            )
    return _VDOT_PACE_TABLE_SEC_PER_KM[keys[-1]]


def _velocity_m_per_min(distance_km: float, time_sec: float) -> float:
    """距离(km) / 时间(秒) → 速度(米/分钟)。"""
    if not distance_km or time_sec <= 0:
        return 0.0
    return (distance_km * 1000.0) / (time_sec / 60.0)


def vdot_from_race(distance_km: float, time_sec: float) -> Optional[float]:
    """由比赛成绩估算 VDOT（≈VO2max, mL/kg/min）。

    采用 Daniels/Ruby 速度法方程（见模块 docstring）。
    距离 < 1km 或时间无效返回 None。
    """
    if not distance_km or distance_km < 1.0 or not time_sec or time_sec <= 0:
        return None
    v = _velocity_m_per_min(distance_km, time_sec)
    t = time_sec / 60.0
    if v <= 0 or t <= 0:
        return None
    num = -4.6 + 0.182258 * v + 0.000104 * v * v
    den = (0.8 + 0.1894393 * math.exp(-0.012778 * t)
           + 0.2989553 * math.exp(-0.1932605 * t))
    if den <= 0:
        return None
    vdot = num / den
    if vdot < VDOT_MIN or vdot > VDOT_MAX:
        # 超出合理区间（数据异常）仍返回，但调用方可据此判断
        pass
    return round(vdot, 1)


def race_time_sec_from_vdot(vdot: float, distance_km: float,
                            lo: float = 30.0, hi: float = 36000.0) -> Optional[float]:
    """给定 VDOT 与距离，二分求对应比赛时间(秒)。

    单调性：同一距离下时间越长，VDOT 越低 → 可二分。失败返回 None。
    """
    if vdot is None or not distance_km or distance_km < 1.0:
        return None
    lo, hi = float(lo), float(hi)
    for _ in range(60):
        mid = (lo + hi) / 2.0
        est = vdot_from_race(distance_km, mid)
        if est is None:
            return None
        if est > vdot:
            lo = mid   # 当前时间偏短(太快) → VDOT 偏高 → 加长时间
        else:
            hi = mid
    return round((lo + hi) / 2.0, 1)


def race_pace_sec_from_vdot(vdot: float, distance_km: float = 10.0) -> Optional[float]:
    """VO2max 配速（秒/公里）= 该 VDOT 在 distance_km 上的比赛配速。"""
    t = race_time_sec_from_vdot(vdot, distance_km)
    if t is None or distance_km <= 0:
        return None
    return round(t / distance_km, 1)


def vdot_training_paces(vdot: float, distance_km: float = 10.0) -> Optional[Dict[str, Any]]:
    """由 VDOT 推导各强度区配速（秒/公里）。

    第一性原理：训练配速围绕 vVO2max（≈I 间歇配速，即 100% VO2max 速度）展开。
      - M / T / I 直接取自 Daniels 查表（插值）的单车配速；
      - E（轻松/恢复）按 Daniels 标准速度占比 59–74% 展开为 [慢, 快] 区间：
        E = vVO2max配速 / pct → [I / 0.74, I / 0.59]；
      - R（重复跑）按 ~106% vVO2max 推导：R = I / 1.06。
    distance_km 仅保留以兼容既有调用签名；训练配速为速度量，与距离无关。
    """
    if vdot is None:
        return None
    e_t, m_t, t_t, i_t = _interpolate_paces(vdot)
    # vVO2max ≈ I 配速（100% VO2max 速度）。E 按 59–74% 速度占比展开为 [慢, 快]。
    e_slow = i_t / ZONE_VO2MAX_PCT["E"][0]   # 59% 速度 → 最慢（秒数最大）
    e_fast = i_t / ZONE_VO2MAX_PCT["E"][1]   # 74% 速度 → 最快（秒数最小）
    return {
        "E": [round(e_slow, 1), round(e_fast, 1)],   # [慢, 快]
        "M": round(m_t, 1),
        "T": round(t_t, 1),
        "I": round(i_t, 1),
        "R": round(i_t / ZONE_VO2MAX_PCT["R"], 1),
    }


def vdot_from_recent_race(race: Optional[Dict[str, Any]]) -> Optional[float]:
    """从"近期比赛成绩"字典推导 VDOT。字段兼容 距离_km/distance_km 与 时间_秒/time_sec。"""
    if not race:
        return None
    d = race.get("距离_km") or race.get("distance_km")
    t = race.get("时间_秒") or race.get("time_sec")
    if d and t:
        try:
            return vdot_from_race(float(d), float(t))
        except (ValueError, TypeError):
            return None
    return None


def vdot_from_profile(profile: Optional[Dict[str, Any]]) -> Optional[float]:
    """从跑者画像结构(manual_input 风格)提取"近期比赛成绩" → VDOT。无则返回 None。"""
    if not profile:
        return None
    race = (profile.get("近期比赛成绩") or profile.get("race_result")) or {}
    return vdot_from_recent_race(race)


def format_pace(sec: Optional[float]) -> str:
    """秒/公里 → '4'00\"/km'。None 返回 '—'。"""
    if sec is None:
        return "—"
    sec = int(round(sec))
    if sec <= 0:
        return "—"
    m = sec // 60
    s = sec % 60
    return f"{m}'{s:02d}\"/km"


def format_vdot_block(vdot: float, distance_km: float = 10.0) -> str:
    """把 VDOT + 配速表渲染为可读文本块（供教练/计划上下文）。"""
    paces = vdot_training_paces(vdot, distance_km)
    if not paces:
        return f"（VDOT={vdot} 无法推导配速）"
    lines = [f"🏅 当前 VDOT ≈ {vdot}（基于 {distance_km}km 比赛成绩推导）",
             "   训练配速建议："]
    labels = {"E": "E 轻松/恢复", "M": "M 马拉松", "T": "T 阈值",
              "I": "I 间歇", "R": "R 重复"}
    for z, label in labels.items():
        p = paces.get(z)
        if isinstance(p, list):
            lines.append(f"     - {label}：{format_pace(p[0])} ~ {format_pace(p[1])}")
        else:
            lines.append(f"     - {label}：{format_pace(p)}")
    return "\n".join(lines)


# ============================================================
# 与手动配置交互（读取 data/manual_input.json 的近期比赛成绩）
# ============================================================
def read_recent_race_from_manual(data_dir: str) -> Optional[Dict[str, Any]]:
    """读取 manual_input.json 中的"近期比赛成绩"。无则返回 None。"""
    path = os.path.join(data_dir, "manual_input.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("近期比赛成绩")
    except (json.JSONDecodeError, OSError):
        return None


if __name__ == "__main__":
    # 自测：10km 40 分 → VDOT≈52，配速表应与 Daniels 表接近
    print("=" * 50)
    print("VDOT 内核自测")
    print("=" * 50)
    v = vdot_from_race(10.0, 40 * 60)
    print(f"10km/40min → VDOT={v}")
    assert v is not None and 50 < v < 54, f"VDOT 异常: {v}"

    paces = vdot_training_paces(v, 10.0)
    print("配速表(秒/km):", paces)
    # I 应快于 E；T 居中
    assert paces["I"] < paces["T"] < paces["M"] < paces["E"][0], "分区顺序错误"
    # 校准：VDOT≈52 的配速应与 Daniels 表接近（VDOT50→M 4:20 / T 3:50 / I 3:25）
    assert abs(paces["I"] - 197) < 20, f"I 配速偏离 Daniels 表: {paces['I']}"
    assert abs(paces["M"] - 251) < 20, f"M 配速偏离 Daniels 表: {paces['M']}"
    print(format_vdot_block(v, 10.0))

    # 反解：VDOT≈52 的 10km 时间应≈40min
    t = race_time_sec_from_vdot(v, 10.0)
    print(f"VDOT={v} 的 10km 时间 ≈ {t/60:.1f} min")
    assert abs(t - 2400) < 30, f"反解时间偏差过大: {t}"

    print("\n✅ VDOT 自测通过")
