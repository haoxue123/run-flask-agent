/**
 * 训练计划共享模块
 * 提供计划生成、渲染、保存/加载功能，供 index.html 和 training.html 共同使用。
 *
 * 依赖：window.PlanCalendar（来自 plan_calendar.js）、window.COACH.upload
 * 输出：window.COACH.plan = { generatePlan, loadPlan }
 */
(function () {
  'use strict';

  const PLAN = {};

  /** 生成训练计划 */
  async function generatePlan() {
    const btn = document.getElementById('generate-btn');
    const btnText = document.getElementById('generate-btn-text');
    const errorMsg = document.getElementById('error-msg');
    const errorText = document.getElementById('error-text');
    const result = document.getElementById('result');

    if (!btn) return;

    const fd = (window.COACH && window.COACH.upload)
      ? window.COACH.upload.buildFormData()
      : new FormData();

    btn.disabled = true;
    if (btnText) btnText.textContent = '生成中...';
    if (errorMsg) errorMsg.style.display = 'none';
    if (result) result.style.display = 'none';

    try {
      const resp = await fetch('/generate', { method: 'POST', body: fd });
      const data = await resp.json();
      if (data.success) {
        // 计划文本和图表
        const planTextEl = document.getElementById('plan-text');
        const chartImg = document.getElementById('chart-img');
        const sourceBadge = document.getElementById('source-badge');
        if (planTextEl) planTextEl.textContent = data.plan;
        if (chartImg) chartImg.src = data.chart_url;
        if (sourceBadge) sourceBadge.textContent = '数据源：' + data.source;

        // 月历渲染（优先使用结构化数据，缺失时回退文本解析）
        const days = (data.structured && data.structured.length)
            ? window.PlanCalendar.normalizeStructuredDays(data.structured)
            : window.PlanCalendar.parsePlan(data.plan);
        const calendar = document.getElementById('plan-calendar');
        if (calendar) window.PlanCalendar.renderCalendar(calendar, days);

        // 触发 plan-generated 事件（training.html 监听此事件做摘要和保存）
        document.dispatchEvent(new CustomEvent('plan-generated', {
          detail: { days: days, planText: data.plan, chartUrl: data.chart_url, source: data.source }
        }));

        if (result) result.style.display = (result.classList.contains('space-y-4') ? 'block' : 'grid');
        if (result) {
          const details = result.closest('details');
          if (details) details.open = true;
        }
      } else {
        if (errorText) errorText.textContent = data.error || '生成失败';
        if (errorMsg) errorMsg.style.display = 'flex';
      }
    } catch (e) {
      if (errorText) errorText.textContent = '请求失败：' + e.message;
      if (errorMsg) errorMsg.style.display = 'flex';
    } finally {
      btn.disabled = false;
      if (btnText) btnText.textContent = '生成训练计划';
      if (typeof lucide !== 'undefined') lucide.createIcons();
    }
  }

  /** 页面加载时恢复已保存计划 */
  function loadSavedPlan() {
    fetch('/api/plan/load')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.plan && data.plan.length > 0) {
          const result = document.getElementById('result');
          const calendar = document.getElementById('plan-calendar');
          if (calendar) window.PlanCalendar.renderCalendar(calendar, data.plan);
          if (result) result.style.display = (result.classList.contains('space-y-4') ? 'block' : 'grid');
          if (result) {
            const details = result.closest('details');
            if (details) details.open = true;
          }

          // 触发 plan-loaded 事件（training.html 监听此事件做摘要和完成率）
          document.dispatchEvent(new CustomEvent('plan-loaded', {
            detail: { days: data.plan, savedAt: (data.meta && data.meta.generated_at) || "" }
          }));
        }
      })
      .catch(function () {});
  }

  // 页面加载后自动恢复（仅 training.html 显式调用，避免首页无效请求）

  PLAN.generatePlan = generatePlan;
  PLAN.loadSavedPlan = loadSavedPlan;

  window.COACH = window.COACH || {};
  window.COACH.plan = PLAN;
})();