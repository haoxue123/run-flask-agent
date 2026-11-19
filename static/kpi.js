/**
 * KPI 图表模块
 * 渲染就绪度半圆 gauge、配速趋势图、心率趋势图（仅 index.html 使用）。
 *
 * 依赖：Chart.js（CDN），profileData 全局变量（由 Jinja2 注入）
 * 输出：window.COACH.kpi = { renderReadinessChart, renderCharts }
 */
(function () {
  'use strict';

  const KPI = {};

  function getThemeColor(name, fallback) {
    const style = getComputedStyle(document.documentElement);
    return style.getPropertyValue(name).trim() || fallback;
  }

  /** 渲染就绪度环形图 */
  function renderReadinessChart(readiness) {
    const ctx = document.getElementById('readinessChart');
    if (!ctx) return;
    if (!readiness || readiness === '-') {
      ctx.parentElement.innerHTML = '<div class="text-sm text-muted-foreground py-8 text-center">暂无就绪度数据</div>';
      return;
    }
    const primary = getThemeColor('--rc-primary', '#167657');
    const mutedTrack = getThemeColor('--rc-muted', 'rgba(255,255,255,0.08)');
    const mutedText = getThemeColor('--rc-muted-foreground', '') || getThemeColor('--rc-text-secondary', '#8A8F98');
    const chart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: ['就绪', '待恢复'],
        datasets: [{
          data: [parseInt(readiness), 100 - parseInt(readiness)],
          backgroundColor: [primary, mutedTrack],
          borderWidth: 0
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        rotation: -90,
        circumference: 180,
        cutout: '78%',
        plugins: { legend: { display: false }, tooltip: { enabled: false } }
      },
      plugins: [{
        id: 'centerText',
        afterDraw: function (chart) {
          const { ctx, width, height } = chart;
          var numSize = chart.height >= 120 ? 64 : 40;
          ctx.save();
          ctx.font = 'bold ' + numSize + 'px Inter Tight, Inter, sans-serif';
          ctx.fillStyle = primary;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(readiness, width / 2, height * 0.62);
          ctx.font = '13px Inter, sans-serif';
          ctx.fillStyle = mutedText;
          ctx.fillText('就绪度', width / 2, height * 0.62 + 30);
          ctx.restore();
        }
      }]
    });
    registerChart(chart);
  }

  /** 渲染趋势图（配速 + 心率） */
  function renderCharts(profileData) {
    if (!profileData || profileData.length < 2) return;

    const primary = getThemeColor('--rc-primary', '#167657');
    const chart2 = getThemeColor('--rc-chart-2', '#EB6834');
    const muted = getThemeColor('--rc-muted-foreground', '') || getThemeColor('--rc-text-secondary', '#8A8F98');
    const gridColor = getThemeColor('--rc-chart-grid', 'rgba(255,255,255,0.06)');
    const sorted = profileData.slice().sort(function (a, b) {
      return (a['日期'] || '').localeCompare(b['日期'] || '');
    });

    const labels = sorted.map(function (r) { return r['日期'] ? r['日期'].slice(5) : ''; });
    const paces = sorted.map(function (r) {
      const sec = r['平均配速_秒每公里'];
      return sec != null ? (sec / 60).toFixed(2) : null;
    });
    const hrs = sorted.map(function (r) { return r['平均心率'] || null; });

    // 配速图
    const paceCtx = document.getElementById('paceChart');
    if (paceCtx) {
      if (profileData.length < 2) {
        paceCtx.parentElement.innerHTML = '<div class="text-sm text-muted-foreground py-8 text-center">数据不足（需至少 2 次跑步）</div>';
      } else {
        var paceChart = new Chart(paceCtx, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [{
              label: '配速 (min/km)',
              data: paces,
              borderColor: primary,
              backgroundColor: hexToRgba(primary, 0.08),
              fill: true, tension: 0.4, pointRadius: 3,
              pointBackgroundColor: primary, borderWidth: 2, spanGaps: true
            }]
          },
          options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              y: { reverse: true, grid: { color: gridColor },
                   ticks: { color: muted, font: { size: 11 }, callback: function (v) { return v.toFixed(1) + "'"; } } },
              x: { grid: { display: false }, ticks: { color: muted, font: { size: 11 } } }
            }
          }
        });
        registerChart(paceChart);
      }
    }

    // 心率图
    const hrCtx = document.getElementById('hrChart');
    if (hrCtx) {
      if (profileData.length < 2) {
        hrCtx.parentElement.innerHTML = '<div class="text-sm text-muted-foreground py-8 text-center">数据不足（需至少 2 次跑步）</div>';
      } else {
        var hrChart = new Chart(hrCtx, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [{
              label: '心率 (bpm)', data: hrs,
              borderColor: chart2, backgroundColor: hexToRgba(chart2, 0.08),
              fill: true, tension: 0.4, pointRadius: 3,
              pointBackgroundColor: chart2, borderWidth: 2, spanGaps: true
            }]
          },
          options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              y: { min: 100, grid: { color: gridColor },
                   ticks: { color: muted, font: { size: 11 } } },
              x: { grid: { display: false }, ticks: { color: muted, font: { size: 11 } } }
            }
          }
        });
        registerChart(hrChart);
      }
    }
  }

  KPI.renderReadinessChart = renderReadinessChart;
  KPI.renderCharts = renderCharts;

  window.COACH = window.COACH || {};
  window.COACH.kpi = KPI;
})();