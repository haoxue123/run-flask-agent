/**
 * 文件上传共享模块
 * 提供文件拖拽、选择、校验功能，供 index.html 和 training.html 共同使用。
 *
 * 依赖：DOM 中存在 id="files" 的 <input type="file"> 元素
 * 输出：window.COACH.upload = { showFiles, refreshData }
 */
(function () {
  'use strict';

  const UPLOAD = {};

  /** 在 #file-list 中展示已选文件列表 */
  function showFiles(files) {
    const fileList = document.getElementById('file-list');
    if (!fileList) return;
    fileList.innerHTML = '';
    for (let i = 0; i < files.length; i++) {
      const div = document.createElement('div');
      div.className = 'flex items-center gap-2';
      div.innerHTML = '<i data-lucide="file" class="w-3 h-3 text-primary"></i><span>' + files[i].name + '</span>';
      fileList.appendChild(div);
    }
    if (files.length > 0 && typeof lucide !== 'undefined') lucide.createIcons();
  }

  /** 收集表单数据构建 FormData */
  function buildFormData() {
    const fd = new FormData();
    const files = document.getElementById('files').files;
    for (let i = 0; i < files.length; i++) fd.append('files', files[i]);
    const fields = ['target_time', 'run_date', 'run_distance', 'run_duration',
                    'run_pace', 'run_hr', 'run_name', 'target_race', 'plan_duration'];
    fields.forEach(function (id) {
      const el = document.getElementById(id);
      if (el) fd.append(id, el.value || '');
    });
    return fd;
  }

  /** 更新数据（不消耗 AI 额度） */
  async function refreshData(e) {
    const fd = buildFormData();
    const btn = e && e.target ? e.target.closest('button') : document.querySelector('[onclick*="refreshData"]');
    if (btn) {
      btn.disabled = true;
      const original = btn.innerHTML;
      btn.innerHTML = '<i data-lucide="loader" class="w-4 h-4 animate-spin"></i><span>更新中...</span>';
      if (typeof lucide !== 'undefined') lucide.createIcons();
      try {
        const resp = await fetch('/api/data/refresh', { method: 'POST', body: fd });
        const data = await resp.json();
        if (data.success) {
          window.location.href = '/';
        } else {
          if (window.DS && window.DS.showToast) {
            window.DS.showToast(data.error || '更新失败', 'error');
          }
        }
      } catch (e) {
        if (window.DS && window.DS.showToast) {
          window.DS.showToast('网络错误，请重试', 'error');
        }
      } finally {
        btn.disabled = false;
        btn.innerHTML = original;
        if (typeof lucide !== 'undefined') lucide.createIcons();
      }
    }
  }

  /** 更新目标距离显示 */
  function updateTargetDist() {
    const val = (document.getElementById('target_race') || {}).value || '';
    const distMap = { '5K': '5 km', '10K': '10 km', '半马': '21.1 km', '全马': '42.2 km' };
    const distEl = document.getElementById('target_distance');
    if (distEl) distEl.value = distMap[val] || '';
  }

  // --- 初始化文件拖拽 ---
  function initDragDrop() {
    const fileInput = document.getElementById('files');
    if (!fileInput) return;
    const dropZone = fileInput.previousElementSibling;
    fileInput.addEventListener('change', function (e) { showFiles(e.target.files); });
    if (dropZone) {
      dropZone.addEventListener('dragover', function (e) { e.preventDefault(); this.classList.add('border-primary'); });
      dropZone.addEventListener('dragleave', function () { this.classList.remove('border-primary'); });
      dropZone.addEventListener('drop', function (e) {
        e.preventDefault();
        this.classList.remove('border-primary');
        const dt = new DataTransfer();
        for (let i = 0; i < e.dataTransfer.files.length; i++) {
          if (/\.(gpx|tcx|fit|csv)$/i.test(e.dataTransfer.files[i].name)) dt.items.add(e.dataTransfer.files[i]);
        }
        fileInput.files = dt.files;
        showFiles(dt.files);
      });
    }
  }

  // 页面加载后自动初始化
  document.addEventListener('DOMContentLoaded', function () {
    initDragDrop();
    updateTargetDist();
    startSyncPolling();
    if (window.location.search.indexOf('sync=1') !== -1) {
      const card = document.getElementById('sync-card');
      if (card && card.scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });

  /** 把 ISO 时间格式化为本地 MM-DD HH:MM */
  function formatSyncTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const pad = function (n) { return (n < 10 ? '0' : '') + n; };
    return pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
           pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  /** 触发一次 Garmin 同步（手动按钮或打开页面自动触发） */
  async function syncGarmin(source) {
    source = source || 'manual';
    const text = document.getElementById('sync-status-text');
    const btn = document.getElementById('sync-now-btn');
    const bar = document.getElementById('sync-status-bar');
    const spinner = document.getElementById('sync-spinner');
    if (!text) return;
    if (window.__syncing) return;  // 防重入
    window.__syncing = true;
    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.add('animate-spin');
    if (bar) bar.style.borderColor = 'var(--rc-border, #2a3342)';
    text.textContent = '正在从 Garmin Connect 同步最新数据…';
    try {
      const resp = await fetch('/api/data/sync_garmin', { method: 'POST' });
      const data = await resp.json();
      if (data.success) {
        text.textContent = '已同步 Garmin 最新数据（' + (data.count || 0) + ' 个文件）';
        if (bar) bar.style.borderColor = 'var(--rc-success, #2ecc71)';
        // 有新增文件则刷新页面以显示最新数据
        if (data.count > 0) {
          window.location.href = '/';
          return;
        }
      } else {
        text.textContent = '同步失败：' + (data.error || '未知错误') +
          '（可点“立即同步”重试，或手动上传文件）';
        if (bar) bar.style.borderColor = 'var(--rc-danger, #e74c3c)';
        // 登录/令牌相关问题 → 提示用浏览器登录
        if (/登录|令牌|token|playwright|mfa|验证|未配置/i.test(data.error || '')) {
          showLoginBtn();
        }
      }
    } catch (e) {
      text.textContent = '同步请求出错，请检查网络后重试';
      if (bar) bar.style.borderColor = 'var(--rc-danger, #e74c3c)';
    } finally {
      window.__syncing = false;
      if (btn) btn.disabled = false;
      if (spinner) spinner.classList.remove('animate-spin');
      if (btn) btn.style.display = '';
      if (typeof lucide !== 'undefined') lucide.createIcons();
    }
  }

  /** 显示/隐藏“登录 Garmin”按钮 */
  function showLoginBtn() {
    const loginBtn = document.getElementById('sync-login-btn');
    const nowBtn = document.getElementById('sync-now-btn');
    if (loginBtn) loginBtn.style.display = '';
    if (nowBtn) nowBtn.style.display = 'none';
  }

  /** 触发浏览器交互登录（MFA 在弹出的浏览器窗口输入） */
  async function garminLogin() {
    const text = document.getElementById('sync-status-text');
    const loginBtn = document.getElementById('sync-login-btn');
    if (!text) return;
    if (loginBtn) loginBtn.disabled = true;
    text.textContent = '正在调用系统默认浏览器并复用其日常 profile 登录 Garmin（请在弹出的窗口完成登录/MFA）…';
    try {
      const resp = await fetch('/api/data/garmin_login', { method: 'POST' });
      const data = await resp.json();
      if (data.success) {
        text.textContent = '浏览器登录成功，正在拉取最新数据…';
        // 登录成功后自动同步一次
        syncGarmin('manual');
      } else {
        text.textContent = '浏览器登录失败：' + (data.error || '未知错误');
      }
    } catch (e) {
      text.textContent = '登录请求出错，请检查后端是否运行';
    } finally {
      if (loginBtn) loginBtn.disabled = false;
      if (typeof lucide !== 'undefined') lucide.createIcons();
    }
  }

  /** 打开页面即启动同步进度轮询：书签一键同步后实时显示进度 */
  let _syncPollTimer = null;
  function startSyncPolling() {
    const helpBtn = document.getElementById('sync-help-btn');
    if (helpBtn && !helpBtn.dataset.bound) {
      helpBtn.dataset.bound = '1';
      helpBtn.addEventListener('click', showSyncHelp);
    }
    if (_syncPollTimer) clearInterval(_syncPollTimer);
    pollSyncProgress();  // 立即拉一次
    _syncPollTimer = setInterval(pollSyncProgress, 1500);
  }

  /** 拉取实时进度并渲染到同步状态卡片 */
  async function pollSyncProgress() {
    const card = document.getElementById('sync-card');
    if (!card) return;
    let data;
    try {
      const resp = await fetch('/api/data/garmin_sync_progress', { cache: 'no-store' });
      data = await resp.json();
    } catch (e) {
      renderSyncIdle('无法连接后端，请确认 Agent 已启动 (python app.py)');
      return;
    }
    if (!data || data.status === 'idle') {
      // 空闲：展示上一次同步状态（若后端有记录），否则提示用书签同步
      renderSyncIdleFromLegacy();
      return;
    }
    renderSync(data);
  }

  function renderSync(p) {
    const badge = document.getElementById('sync-badge');
    const wrap = document.getElementById('sync-progress-wrap');
    const fill = document.getElementById('sync-progress-fill');
    const stage = document.getElementById('sync-stage');
    const counts = document.getElementById('sync-counts');
    const detail = document.getElementById('sync-detail');
    const summary = document.getElementById('sync-summary');
    if (!badge) return;

    const status = p.status || 'idle';
    const labelMap = { idle: '空闲', running: '同步中', success: '已完成', failed: '失败' };
    badge.textContent = labelMap[status] || status;
    badge.className = 'sync-badge ' + status;

    const total = p.total || 0;
    const processed = p.processed || 0;
    let pct = 0;
    if (status === 'success' || status === 'failed') pct = 100;
    else if (total > 0) pct = Math.round((processed / total) * 100);
    if (fill) fill.style.width = pct + '%';

    if (status === 'running') {
      if (wrap) wrap.style.display = 'block';
      if (stage) stage.textContent = p.stage || '处理中…';
      if (counts) counts.textContent = total > 0 ? (processed + ' / ' + total) : (p.extracted_fit || 0) + ' 个 .fit';
      if (detail) detail.textContent = p.stage_detail || '';
      if (summary) summary.style.display = 'none';
    } else if (status === 'success') {
      if (wrap) wrap.style.display = 'none';
      if (stage) stage.textContent = '同步完成';
      if (counts) counts.textContent = (p.extracted_fit || 0) + ' 个 .fit';
      if (detail) detail.textContent = p.stage_detail || p.message || '';
      if (summary) {
        const s = p.profile_summary || {};
        const lines = [];
        if (s['最近跑步次数'] != null) lines.push('最近跑步次数：' + s['最近跑步次数']);
        if (s['最近一次跑步'] != null) lines.push('最近一次跑步：' + s['最近一次跑步']);
        summary.innerHTML = lines.length ? '画像已刷新 — ' + lines.join('；') : '画像已刷新';
        summary.style.display = 'block';
      }
    } else if (status === 'failed') {
      if (wrap) wrap.style.display = 'none';
      if (stage) stage.textContent = '同步失败';
      if (counts) counts.textContent = '';
      if (detail) detail.textContent = (p.error || p.stage_detail || '未知错误');
      if (summary) summary.style.display = 'none';
    }
  }

  /** 空闲态：优先展示后端记录的「上次成功同步」时间，否则引导用书签 */
  let _legacyChecked = false;
  async function renderSyncIdleFromLegacy() {
    const detail = document.getElementById('sync-detail');
    const badge = document.getElementById('sync-badge');
    if (_legacyChecked) return;
    _legacyChecked = true;
    if (badge) { badge.textContent = '空闲'; badge.className = 'sync-badge idle'; }
    if (detail) detail.textContent = '等待通过 Garmin Connect 网页书签同步数据…';
    try {
      const resp = await fetch('/api/data/sync_status');
      const data = await resp.json();
      const state = data.state || {};
      if (state.result === 'success' && state.updated_at) {
        if (detail) detail.textContent = '上次同步：' + formatSyncTime(state.updated_at) +
          (data.latest_run_date ? '，最新跑步 ' + data.latest_run_date : '');
      }
    } catch (e) { /* 忽略，保持默认提示 */ }
  }

  function renderSyncIdle(msg) {
    const detail = document.getElementById('sync-detail');
    const badge = document.getElementById('sync-badge');
    const wrap = document.getElementById('sync-progress-wrap');
    if (badge) { badge.textContent = '空闲'; badge.className = 'sync-badge idle'; }
    if (wrap) wrap.style.display = 'none';
    if (detail) detail.textContent = msg || '等待通过 Garmin Connect 网页书签同步数据…';
  }

  /** 帮助：说明如何用浏览器书签一键同步 */
  function showSyncHelp() {
    const msg = [
      '如何一键同步 Garmin 数据：',
      '1. 浏览器登录 Garmin Connect 活动页（connect.garmin.cn/modern/activities）',
      '2. 点击收藏栏里的「Garmin 一键同步」书签（首次需先添加该书签）',
      '3. 脚本会用你已登录的会话自动下载近 180 天跑步活动并推给本 Agent',
      '4. 返回本页，本卡片会实时显示解包 / 刷新画像进度',
      '',
      '注：因 Garmin 风控，仍需你手动点一下书签；Agent 无法替你登录。',
    ].join('\n');
    if (window.DS && window.DS.showToast) {
      window.DS.showToast('请见控制台说明，或按 F12 查看', 'info');
    }
    alert(msg);
  }

  UPLOAD.showFiles = showFiles;
  UPLOAD.buildFormData = buildFormData;
  UPLOAD.refreshData = refreshData;
  UPLOAD.updateTargetDist = updateTargetDist;
  UPLOAD.syncGarmin = syncGarmin;
  UPLOAD.garminLogin = garminLogin;
  UPLOAD.syncGarmin = syncGarmin;
  UPLOAD.loadSyncStatus = loadSyncStatus;

  // 导出到全局
  window.COACH = window.COACH || {};
  window.COACH.upload = UPLOAD;
})();