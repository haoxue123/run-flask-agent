"""
训练计划可视化模块
====================

将结构化训练计划绘制为现代风格的配速图 PNG。

核心函数：
- plot_pace_chart()     绘制配速折线图（暗色主题）+ 距离柱状图

依赖：matplotlib, numpy
"""

import os
import glob
import matplotlib
import physiology
import safeio
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.patches import FancyBboxPatch
import numpy as np

# 中文字体
_CN_FONTS = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]

# 训练类型 → 颜色 + 标记
TYPE_STYLE = {
    "轻松跑":   {"color": "#4ADE80", "marker": "o", "label": "轻松跑"},
    "恢复跑":   {"color": "#34D399", "marker": "o", "label": "恢复跑"},
    "慢跑":     {"color": "#6EE7B7", "marker": "o", "label": "慢跑"},
    "节奏跑":   {"color": "#FB923C", "marker": "s", "label": "节奏跑"},
    "间歇跑":   {"color": "#F87171", "marker": "D", "label": "间歇跑"},
    "冲刺跑":   {"color": "#EF4444", "marker": "D", "label": "冲刺跑"},
    "长距离":   {"color": "#60A5FA", "marker": "P", "label": "长距离"},
    "LSD":      {"color": "#60A5FA", "marker": "P", "label": "长距离"},
    "马拉松配速": {"color": "#C084FC", "marker": "h", "label": "马拉松配速"},
    "比赛配速":  {"color": "#C084FC", "marker": "h", "label": "比赛配速"},
    "力量训练":  {"color": "#A78BFA", "marker": "^", "label": "力量训练"},
    "交叉训练":  {"color": "#94A3B8", "marker": "v", "label": "交叉训练"},
    "休息":      {"color": "#475569", "marker": "X", "label": "休息"},
}

# 设计 Token
BG        = "#0B0C0E"
CARD_BG   = "#141618"
ACCENT    = "#C6FF1F"
TEXT      = "#E8E8E6"
TEXT_SEC  = "#9C9C94"
TEXT_MUTED = "#5C5C54"
GRID      = "#1E2022"
BORDER    = "#2A2C2E"


def _classify(t):
    for k, v in TYPE_STYLE.items():
        if k in (t or ""):
            return k, v
    return "其他", {"color": "#64748B", "marker": "o", "label": t or "未知"}


def _pace_label(sec):
    """秒 → '6'30\"' 格式（统一委托 physiology.sec_to_pace_str，None 返回 '--'）"""
    s = physiology.sec_to_pace_str(sec)
    return s if s is not None else "--"


def _format_pace_axis(sec, _):
    """matplotlib 坐标轴格式器"""
    s = int(sec)
    return f"{s // 60}'{s % 60:02d}\""


def plot_pace_chart(structured_plan, output_path="data/pace_chart.png"):
    """绘制现代暗色风格配速图。

    Args:
        structured_plan: list[dict]
            - day, weekday, type, target_pace_sec, distance_km, is_rest
        output_path: 图片保存路径

    Returns:
        str: 绝对路径
    """
    plt.rcParams["font.sans-serif"] = _CN_FONTS
    plt.rcParams["axes.unicode_minus"] = False

    # ---- 解析数据 ----
    days = []
    for i, item in enumerate(structured_plan):
        dn = item.get("day", i + 1)
        wd = item.get("weekday", "")
        tp = item.get("type", "休息")
        ps = item.get("target_pace_sec")
        dk = item.get("distance_km")
        ir = item.get("is_rest", tp == "休息")
        cat, sty = _classify(tp)
        label = f"第{dn}天" + (f"(周{wd})" if wd else "")
        days.append({
            "day": dn, "label": label, "type": tp,
            "pace_sec": ps, "dist_km": dk,
            "is_rest": ir, "category": cat,
            "color": sty["color"], "marker": sty["marker"],
            "cat_label": sty["label"],
        })

    if not days:
        raise ValueError("未能解析出任何训练日数据")

    total = len(days)
    run_days = [d for d in days if not d["is_rest"] and d["pace_sec"] is not None]
    rest_days = [d for d in days if d["is_rest"]]

    # ---- 图表尺寸 ----
    fig_w = max(14, total * 0.55)
    fig_h = 7.5
    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=BG)
    gs = fig.add_gridspec(1, 1, left=0.06, right=0.94, top=0.82, bottom=0.14)
    ax = fig.add_subplot(gs[0, 0], facecolor=CARD_BG)

    # ---- 配速区间背景 ----
    zones = [
        (0, 360,    "#14532D", "快 (<6'00\")"),
        (360, 420,  "#422006", "中 (6'00\"–7'00\")"),
        (420, 1600, "#450A0A", "慢 (>7'00\")"),
    ]
    for lo, hi, color, _ in zones:
        ax.axhspan(lo, hi, facecolor=color, alpha=0.35, zorder=0, linewidth=0)

    # ---- 网格 ----
    ax.grid(True, linestyle="-", alpha=0.12, color=GRID, zorder=1, linewidth=0.8)

    # ---- 距离柱状图 ----
    x_pos = np.arange(1, total + 1)
    bar_vals = [d["dist_km"] or 0 for d in days]
    bar_colors = [d["color"] if not d["is_rest"] else "#1E293B" for d in days]

    # 距离柱放在右侧 Y 轴（隐藏在背后，用透明度）
    ax2 = ax.twinx()
    bars = ax2.bar(x_pos, bar_vals, color=bar_colors, alpha=0.22,
                   zorder=2, edgecolor="none", width=0.72)
    ax2.set_ylim(0, max(bar_vals) * 2.2 if any(bar_vals) else 10)
    ax2.set_ylabel("距离 (km)", fontsize=10, color=TEXT_MUTED, fontweight=500,
                   labelpad=12)
    ax2.tick_params(axis="y", colors=TEXT_MUTED, labelsize=8)
    ax2.spines["right"].set_visible(True)
    ax2.spines["right"].set_color("#1E293B")

    # 距离值标注在柱顶
    for i, v in enumerate(bar_vals):
        if v > 0:
            ax2.annotate(f"{v:.1f}", (x_pos[i], v),
                         textcoords="offset points", xytext=(0, 5),
                         ha="center", fontsize=7.5, color=TEXT_MUTED,
                         fontweight=600, alpha=0.7)

    # ---- 配速折线 ----
    if len(run_days) >= 2:
        run_x = [d["day"] for d in run_days]
        run_y = [d["pace_sec"] for d in run_days]
        # 渐变填充
        ax.fill_between(run_x, run_y, alpha=0.18, color=ACCENT,
                        interpolate=True, zorder=3)
        # 主折线
        ax.plot(run_x, run_y, color=ACCENT, linewidth=2.8, zorder=4, alpha=0.9)

    # 按类型分组绘制散点
    from collections import defaultdict
    grouped = defaultdict(list)
    for d in run_days:
        grouped[(d["color"], d["marker"], d["cat_label"])].append(d)

    for (color, marker, label), pts in grouped.items():
        xs = [p["day"] for p in pts]
        ys = [p["pace_sec"] for p in pts]
        ax.scatter(xs, ys, color=color, marker=marker, s=130,
                   edgecolors=BG, linewidth=1.8, zorder=5, label=label)

    # 休息日标记
    if rest_days:
        ax.scatter([d["day"] for d in rest_days],
                   [max(d["pace_sec"] for d in run_days) + 30 if run_days else 450] * len(rest_days),
                   color="#475569", marker="X", s=90, zorder=5, label="休息",
                   edgecolors=BG, linewidth=1.2)

    # ---- 配速标注 ----
    if run_days:
        step = max(1, total // 14)
        for d in run_days:
            if d["day"] % step == 1 or d["day"] == 1 or d["day"] == total:
                ax.annotate(
                    _pace_label(d["pace_sec"]),
                    (d["day"], d["pace_sec"]),
                    textcoords="offset points", xytext=(0, 14),
                    ha="center", fontsize=8.5, color=ACCENT,
                    fontweight="bold", zorder=6
                )

    # ---- Y 轴（配速） ----
    if run_days:
        ymin = min(d["pace_sec"] for d in run_days) - 45
        ymax = max(d["pace_sec"] for d in run_days) + 45
    else:
        ymin, ymax = 300, 500
    ax.set_ylim(ymax, ymin)  # 反转：快在上
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_format_pace_axis))
    ax.set_ylabel("目标配速  /km", fontsize=11, color=TEXT, fontweight=600,
                  labelpad=14)
    ax.tick_params(axis="y", colors=TEXT_SEC, labelsize=9, pad=8)

    # ---- X 轴 ----
    ax.set_xlim(0.3, total + 0.7)
    ax.set_xticks(x_pos)

    # 智能标签间隔
    if total <= 10:
        tick_labels = [d["label"] for d in days]
    else:
        tick_labels = []
        for d in days:
            if d["day"] % max(1, total // 12) == 1 or d["day"] == total:
                tick_labels.append(d["label"])
            else:
                tick_labels.append("")
    ax.set_xticklabels(tick_labels, rotation=35, ha="right", fontsize=8.5,
                       color=TEXT_SEC)
    ax.tick_params(axis="x", colors=TEXT_SEC, pad=8)
    ax.set_xlabel("训练日", fontsize=10, color=TEXT_MUTED, fontweight=500,
                  labelpad=10)

    # ---- 边框 ----
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(BORDER)
        spine.set_linewidth(0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["left"].set_color(BORDER)
    ax.spines["bottom"].set_color(BORDER)

    # ---- 图例 ----
    unique_types = {}
    for d in days:
        key = d["cat_label"]
        if key not in unique_types:
            unique_types[key] = (d["color"], d["marker"])
    legend_handles = [
        plt.Line2D([0], [0], marker=m, color="w", markerfacecolor=c,
                   markersize=10, markeredgecolor=BG, markeredgewidth=1.5,
                   label=label, linestyle="none")
        for label, (c, m) in unique_types.items()
    ]
    leg = ax.legend(
        handles=legend_handles, loc="upper left",
        frameon=True, facecolor=CARD_BG, edgecolor=BORDER,
        fontsize=9, labelcolor=TEXT_SEC, ncol=min(5, len(legend_handles)),
        borderpad=0.8, handletextpad=0.6, columnspacing=0.8
    )

    # ---- 配速区间图例（右上角） ----
    zone_handles = [
        plt.Rectangle((0, 0), 1, 1, fc=c, alpha=0.4, ec="none")
        for _, _, c, _ in zones
    ]
    zone_labels = [z[3] for z in zones]
    ax2_leg = ax.legend(
        zone_handles, zone_labels,
        loc="upper right", frameon=True, facecolor=CARD_BG, edgecolor=BORDER,
        fontsize=8, title="配速区间", title_fontsize=8.5,
        labelcolor=TEXT_MUTED, borderpad=0.6, handletextpad=0.5
    )
    ax.add_artist(leg)

    # ---- 标题 ----
    run_count = len(run_days)
    rest_count = len(rest_days)
    total_dist = sum(d["dist_km"] or 0 for d in days)
    if run_days:
        avg_pace = int(sum(d["pace_sec"] for d in run_days) / len(run_days))
        meta = f"{total}天计划 · {run_count}训练/{rest_count}休息 · 总跑量 {total_dist:.1f}km · 均配速 {_pace_label(avg_pace)}"
    else:
        meta = f"{total}天计划 · {run_count}训练/{rest_count}休息"

    fig.suptitle("训练计划 · 配速概览", fontsize=20, fontweight=700,
                 color=TEXT, y=1.0, x=0.06, ha="left", va="bottom")
    ax.set_title(meta, fontsize=10.5, color=TEXT_MUTED, fontweight=400,
                 pad=18, loc="left")

    # ---- 保存 ----
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    plt.savefig(output_path, dpi=180, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close()

    abs_path = os.path.abspath(output_path)
    print(f"配速图已保存：{output_path}")
    _cleanup_old_charts(abs_path)
    return abs_path


def _cleanup_old_charts(latest_path, keep=5):
    chart_dir = os.path.dirname(os.path.abspath(latest_path))
    pattern = os.path.join(chart_dir, "pace_chart_*.png")
    existing = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    for old in existing[keep:]:
        # 非关键清理：删除失败（如沙箱 safe-delete 拦截）不应影响主流程
        safeio.safe_remove(old)


if __name__ == "__main__":
    structured = [
        {"day":  1, "weekday": "一", "type": "轻松跑", "target_pace_sec": 390, "distance_km": 3,   "is_rest": False},
        {"day":  2, "weekday": "二", "type": "休息",   "target_pace_sec": None, "distance_km": 0,   "is_rest": True},
        {"day":  3, "weekday": "三", "type": "间歇跑", "target_pace_sec": 340, "distance_km": 4,   "is_rest": False},
        {"day":  4, "weekday": "四", "type": "轻松跑", "target_pace_sec": 390, "distance_km": 3,   "is_rest": False},
        {"day":  5, "weekday": "五", "type": "休息",   "target_pace_sec": None, "distance_km": 0,   "is_rest": True},
        {"day":  6, "weekday": "六", "type": "长距离", "target_pace_sec": 410, "distance_km": 5,   "is_rest": False},
        {"day":  7, "weekday": "日", "type": "休息",   "target_pace_sec": None, "distance_km": 0,   "is_rest": True},
    ]
    path = plot_pace_chart(structured, output_path="data/pace_chart.png")
    print(f"图片绝对路径：{path}")