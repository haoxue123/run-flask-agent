"""
生理学核心模块（确定性内核）
============================================================
马拉松训练 Agent 的"科学地基"。所有配速、负荷、体能的数字都从这里来，
而不是由 LLM 凭记忆"编"出来。

设计原则（第一性原理）：
  1. 纯标准库，无外部依赖 → 任何环境都能跑，不会破坏现有工程。
  2. 确定性函数 → 同样输入永远同样输出，可单测、可审计。
  3. 公式均来自运动生理学经典模型，常量在本文件顶部集中定义并标注出处。

所实现的模型：
  - 配速互转：pace_str_to_sec / sec_to_pace_str
  - 训练冲量 TRIMP：banister_trimp（基于心率）/ trimp_from_duration（无心率近似）
  - 训练负荷 CTL/ATL/TSB：指数加权滑动平均（TrainingPeaks / Banister）
  - 体能-疲劳模型 Banister Fitness-Fatigue：超量恢复曲线，用于减量(taper)洞察
  - 最大摄氧量 VO2max：Uth 公式（心率法）
  - 训练强度分区：workout_type → 阈值配速百分比（Jack Daniels 体系）

所有函数均为纯函数；不读写文件、不调用网络、不依赖全局状态。
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Tuple

import vdot as _vdot


# ============================================================
# 常量（集中定义，便于校准与审计）
# ============================================================

# 指数加权时间常数（天）：CTL=体能(42d)，ATL=疲劳(7d)。TrainingPeaks 标准。
CTL_TIME_CONSTANT = 42.0
ATL_TIME_CONSTANT = 7.0

# Banister 体能-疲劳模型默认参数（经典文献取值，可调）。
# τ1=体能衰减慢(42d)，τ2=疲劳衰减快(7d)；k2>k1 表示疲劳短期拖累表现。
BANISTER_TAU_FITNESS = 42.0
BANISTER_TAU_FATIGUE = 7.0
BANISTER_K_FITNESS = 1.0
BANISTER_K_FATIGUE = 2.0
BANISTER_BASE = 0.0

# Uth–Sørensen–Overgaard–Pedersen 公式常数：VO2max ≈ 15.3 × (最大心率/静息心率)
UTM_VO2MAX_CONST = 15.3

# 训练类型 → 相对阈值配速的"速度百分比"（Jack Daniels 强度分区，近似）。
# 含义：该类型训练的目标速度 = 阈值速度 × fraction。
#   fraction < 1 表示比阈值慢（配速秒数更大）；> 1 表示更快。
# 这里用"速度百分比"而非"配速百分比"，因为速度(1/配速)与强度线性相关。
INTENSITY_BY_TYPE = {
    "恢复跑": 0.60,
    "恢复": 0.60,
    "轻松跑": 0.68,
    "慢跑": 0.68,
    "轻松": 0.68,
    "长距离": 0.72,
    "lsd": 0.72,
    "马拉松配速": 0.80,
    "马拉松": 0.80,
    "节奏跑": 0.88,
    "比赛配速": 0.95,
    "比赛": 0.95,
    "间歇跑": 1.00,
    "间歇": 1.00,
    "冲刺跑": 1.08,
    "冲刺": 1.08,
}
# 非跑步类型（无跑步配速）
NON_RUNNING_TYPES = {"力量训练", "交叉训练", "休息", "拉伸", "瑜伽"}


# ============================================================
# 配速工具
# ============================================================

def pace_str_to_sec(pace: Optional[str]) -> Optional[int]:
    """将人类可读配速转为秒/公里。

    支持格式：
      - '6:17' / '6'17"' / '6:17"/km' / '377'（纯整数秒）
    解析失败返回 None。
    """
    if pace is None:
        return None
    if isinstance(pace, (int, float)):
        return int(pace)
    s = str(pace)
    # 先把各种引号/撇号统一成冒号，再只保留数字与冒号
    s = s.replace("'", ":").replace('"', ":").replace("′", ":").replace("″", ":")
    s = "".join(ch for ch in s if ch.isdigit() or ch == ":")
    if not s:
        return None
    parts = [p for p in s.split(":") if p != ""]
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 1:
            return int(float(parts[0]))
    except ValueError:
        return None
    return None


def sec_to_pace_str(sec: Optional[float]) -> Optional[str]:
    """将秒/公里转为 '6'17"' 格式。None 返回 None。"""
    if sec is None:
        return None
    sec = int(round(sec))
    if sec <= 0:
        return None
    minutes = sec // 60
    seconds = sec % 60
    return f"{minutes}'{seconds:02d}\""


# ============================================================
# 训练冲量 TRIMP
# ============================================================

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def banister_trimp(duration_min: float,
                   avg_hr: Optional[float],
                   rest_hr: Optional[float],
                   max_hr: Optional[float]) -> Optional[float]:
    """Banister 指数型 TRIMP（基于心率）。

    TRIMP = 时长(min) × HRr × 0.64 × e^(1.92 × HRr)
    其中 HRr = (平均心率 − 静息心率) / (最大心率 − 静息心率)，裁剪到 [0,1]。

    任一心率缺失返回 None（调用方应回退到 trimp_from_duration）。
    """
    if not duration_min or duration_min <= 0:
        return None
    if avg_hr is None or rest_hr is None or max_hr is None:
        return None
    denom = max_hr - rest_hr
    if denom <= 0:
        return None
    hr_ratio = _clamp01((avg_hr - rest_hr) / denom)
    return duration_min * hr_ratio * 0.64 * math.exp(1.92 * hr_ratio)


def trimp_from_duration(duration_min: float, intensity_factor: float = 0.5) -> float:
    """无心率时的 TRIMP 近似：时长 × 强度因子(0~1)。

    intensity_factor 可由训练类型估算（见 intensity_factor_for_type）。
    """
    if not duration_min or duration_min <= 0:
        return 0.0
    return float(duration_min) * _clamp01(intensity_factor)


def intensity_factor_for_type(workout_type: Optional[str]) -> float:
    """训练类型 → 强度因子（用于无心率 TRIMP 近似与负荷估算）。

    返回 0.0~1.0；休息/无类型返回 0.0。
    """
    if not workout_type:
        return 0.0
    t = workout_type.strip()
    if t in NON_RUNNING_TYPES:
        return 0.0
    # 模糊匹配：包含任一关键字即采用其强度
    low = t.lower()
    for key, frac in INTENSITY_BY_TYPE.items():
        if key.lower() in low:
            # 将"速度百分比"映射回一个 0~1 的强度因子（≈ (frac-0.55)/0.55 居中化，简单取 frac*0.6）
            return _clamp01(frac * 0.6)
    return 0.5  # 未知跑步类型，取保守中等强度


# ============================================================
# 训练负荷 CTL / ATL / TSB（指数加权）
# ============================================================

def _ewma_alpha(time_constant: float) -> float:
    """EWMA 平滑系数 alpha = 1 − e^(−1/τ)。"""
    return 1.0 - math.exp(-1.0 / max(time_constant, 1e-6))


def update_load(prev: float, daily_load: float, time_constant: float) -> float:
    """单步 EWMA 更新：load_t = load_{t-1} + alpha × (daily − load_{t-1})。"""
    alpha = _ewma_alpha(time_constant)
    return prev + alpha * (daily_load - prev)


def compute_load_trajectory(daily_loads: Iterable[float],
                            init_ctl: float = 0.0,
                            init_atl: float = 0.0
                            ) -> List[dict]:
    """给定逐日负荷序列，计算每条记录的 CTL/ATL/TSB（形式/状态）。

    返回列表，每项为 {ctl, atl, tsb}。TSB = CTL − ATL（正值=状态好/已恢复）。
    """
    ctl, atl = float(init_ctl), float(init_atl)
    out: List[dict] = []
    for load in daily_loads:
        ctl = update_load(ctl, load, CTL_TIME_CONSTANT)
        atl = update_load(atl, load, ATL_TIME_CONSTANT)
        tsb = ctl - atl
        out.append({"ctl": ctl, "atl": atl, "tsb": tsb})
    return out


# ============================================================
# Banister 体能-疲劳模型（超量恢复 / 减量洞察）
# ============================================================

def banister_performance(day_index: int,
                         training_loads: List[float],
                         day_offsets: Optional[List[int]] = None,
                         base: float = BANISTER_BASE,
                         k1: float = BANISTER_K_FITNESS,
                         k2: float = BANISTER_K_FATIGUE,
                         tau1: float = BANISTER_TAU_FITNESS,
                         tau2: float = BANISTER_TAU_FATIGUE) -> float:
    """Banister Fitness-Fatigue 模型：在 day_index 天的表现指数。

    perf = base + k1·F(day) − k2·A(day)
      F(day) = Σ w_i · e^(−(day−t_i)/τ1)   （体能，衰减慢）
      A(day) = Σ w_i · e^(−(day−t_i)/τ2)   （疲劳，衰减快）

    training_loads: 逐次训练负荷 w_i（按时间顺序）。
    day_offsets:    各次训练对应的"天序号"t_i；缺省为 0,1,2,...。

    用途：模拟减量(taper)后表现回升（疲劳先消退→表现短暂提升）。
    注意：这是"洞察/解释"工具，不参与硬性决策，失败不应影响主流程。
    """
    if day_offsets is None:
        day_offsets = list(range(len(training_loads)))
    fitness = 0.0
    fatigue = 0.0
    for w, t in zip(training_loads, day_offsets):
        if w <= 0:
            continue
        dt = day_index - t
        if dt < 0:
            continue
        fitness += w * math.exp(-dt / tau1)
        fatigue += w * math.exp(-dt / tau2)
    return base + k1 * fitness - k2 * fatigue


# ============================================================
# 最大摄氧量 VO2max（心率法）
# ============================================================

def estimate_vo2max_from_hr(rest_hr: Optional[float],
                            max_hr: Optional[float]) -> Optional[float]:
    """Uth 公式：VO2max ≈ 15.3 × (最大心率 / 静息心率)。

    需要静息与最大心率；任一缺失返回 None。
    """
    if not rest_hr or not max_hr or rest_hr <= 0 or max_hr <= 0:
        return None
    return UTM_VO2MAX_CONST * (max_hr / rest_hr)


# ============================================================
# 阈值配速估计与强度分区
# ============================================================

def estimate_threshold_pace(recent_paces_sec: Iterable[Optional[float]],
                            recent_distances_km: Iterable[Optional[float]]
                            ) -> Tuple[Optional[int], str]:
    """从近期跑步数据估计阈值配速（秒/公里）。

    方法（数据驱动，第一性原理）：
      取"距离 ≥ 4km 的可持续跑"中的最快配速作为近期最佳持续配速，
      将其视为约等于阈值的 ~93%（即阈值略快），得到阈值配速估算。

    返回 (阈值配速_sec, 方法描述)；数据不足返回 (None, 原因)。
    """
    paces = list(recent_paces_sec)
    dists = list(recent_distances_km)
    pairs = [(p, d) for p, d in zip(paces, dists)
             if p and isinstance(p, (int, float)) and p > 0]

    if not pairs:
        return None, "无可用配速数据"

    # 优先用距离 ≥ 4km 的持续跑
    sustained = [(p, d) for p, d in pairs if d and d >= 4.0]
    pool = sustained if sustained else pairs
    best_sec = min(p for p, _ in pool)

    if sustained:
        # 近期最佳持续配速视为阈值的约 95%（保守：阈值更快一点）
        threshold = int(round(best_sec * 0.95))
        method = "基于近期≥4km最佳配速(×0.95)"
    else:
        threshold = int(round(best_sec * 0.93))
        method = "基于近期最佳配速(×0.93,样本无≥4km)"
    return threshold, method


def target_pace_for_type(threshold_pace_sec: Optional[int],
                         workout_type: Optional[str]) -> Optional[int]:
    """根据阈值配速与训练类型，计算该类型的建议配速（秒/公里）。

    速度百分比法：目标速度 = 阈值速度 × fraction → 目标配速 = 阈值配速 / fraction。
    fraction 见 INTENSITY_BY_TYPE。非跑步类型返回 None。

    仅做"建议"，不覆盖 LLM 原配速。
    """
    if threshold_pace_sec is None or not workout_type:
        return None
    t = workout_type.strip()
    if t in NON_RUNNING_TYPES:
        return None
    low = t.lower()
    frac = None
    for key, f in INTENSITY_BY_TYPE.items():
        if key.lower() in low:
            frac = f
            break
    if frac is None:
        return None
    # 配速 ∝ 1/速度；阈值配速(秒) / 速度百分比 = 目标配速(秒)
    return int(round(threshold_pace_sec / frac))


# ============================================================
# Daniels VDOT 接入：比赛成绩 → 全强度区配速
# ============================================================

# 训练类型关键字 → VDOT 强度区（与 _vdot.ZONE_VO2MAX_PCT 对齐）
_WORKOUT_TO_ZONE = [
    (("恢复", "恢复跑"), "E"),
    (("轻松", "慢跑", "长距离", "lsd"), "E"),
    (("马拉松", "马拉松配速"), "M"),
    (("节奏", "阈值"), "T"),
    (("间歇",), "I"),
    (("比赛", "比赛配速"), "I"),
    (("冲刺",), "R"),
]


def zone_for_workout_type(workout_type: Optional[str]) -> Optional[str]:
    """把训练类型映射到 VDOT 强度区（E/M/T/I/R）。无法识别返回 None。"""
    if not workout_type:
        return None
    t = workout_type.strip().lower()
    for keys, zone in _WORKOUT_TO_ZONE:
        for k in keys:
            if k in t:
                return zone
    return None


def target_pace_for_type_vdot(vdot: Optional[float],
                              workout_type: Optional[str],
                              distance_km: float = 10.0) -> Optional[int]:
    """由 VDOT 推导某训练类型的建议配速（秒/公里）。

    流程：训练类型 → 强度区 → vdot_training_paces 中该区配速。
    E 区为范围，取慢端作为保守建议。
    无 VDOT 或类型无法识别返回 None（调用方回退到 target_pace_for_type）。
    """
    if vdot is None or not workout_type:
        return None
    zone = zone_for_workout_type(workout_type)
    if zone is None:
        return None
    paces = _vdot.vdot_training_paces(vdot, distance_km)
    if not paces:
        return None
    p = paces.get(zone)
    if isinstance(p, list):
        return int(round(p[0]))
    return int(round(p))


def vdot_block_from_profile(profile: Optional[dict]) -> Optional[str]:
    """从跑者画像(含近期比赛成绩)生成 VDOT 配速文本块，供教练/计划上下文。无则返回 None。"""
    if not profile:
        return None
    v = _vdot.vdot_from_profile(profile)
    if v is None:
        return None
    race = (profile.get("近期比赛成绩") or profile.get("race_result")) or {}
    dist = (race or {}).get("距离_km") or (race or {}).get("distance_km") or 10.0
    try:
        return _vdot.format_vdot_block(v, float(dist))
    except (ValueError, TypeError):
        return None


# ============================================================
# 模块自测
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("生理学核心模块自测")
    print("=" * 50)

    # 配速互转
    assert pace_str_to_sec("6:17") == 377
    assert pace_str_to_sec("6'17\"") == 377
    assert pace_str_to_sec(377) == 377
    assert sec_to_pace_str(377) == "6'17\""
    print("✓ 配速互转")

    # TRIMP（心率法）：一次 60min、HRr=0.8 的训练
    tr = banister_trimp(60, 160, 60, 185)
    print(f"  TRIMP(60min,HRr=0.8) = {tr:.1f}（应为正值）")
    assert tr is not None and tr > 0

    # EWMA/CTL：连续 7 天每日负荷 50
    traj = compute_load_trajectory([50.0] * 7)
    print(f"  连续7天负荷50 → CTL={traj[-1]['ctl']:.1f}, ATL={traj[-1]['atl']:.1f}, TSB={traj[-1]['tsb']:.1f}")
    assert traj[-1]["ctl"] < 50  # EWMA 未达满值（τ=42）
    assert traj[-1]["atl"] > traj[-1]["ctl"]  # 短期 ATL 上升更快

    # Banister：减量后表现应高于训练中（疲劳先消退）
    loads = [50.0] * 20
    perf_during = banister_performance(19, loads, day_offsets=list(range(20)))
    perf_taper = banister_performance(25, loads, day_offsets=list(range(20)))
    print(f"  Banister 表现：训练中={perf_during:.1f}, 减量后={perf_taper:.1f}")
    assert perf_taper > perf_during  # 减量后表现回升

    # VO2max（心率法）
    vo2 = estimate_vo2max_from_hr(60, 190)
    print(f"  VO2max(静息60/最大190) = {vo2:.1f}")
    assert vo2 is not None and 40 < vo2 < 70

    # 阈值配速与分区
    thr, method = estimate_threshold_pace([400, 410, 420], [5, 4, 3])
    print(f"  阈值配速={sec_to_pace_str(thr)}（{method}）")
    assert thr is not None
    easy = target_pace_for_type(thr, "轻松跑")
    interval = target_pace_for_type(thr, "间歇跑")
    print(f"  轻松跑建议配速={sec_to_pace_str(easy)}, 间歇跑建议配速={sec_to_pace_str(interval)}")
    assert easy is not None and interval is not None
    assert interval < easy  # 间歇应快于轻松

    print("\n✅ 全部自测通过")
