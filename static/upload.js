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
    loadSyncStatus();
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
    text.textContent = '正在打开浏览器登录 Garmin（请在弹出的窗口完成登录/MFA）…';
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

  /** 打开页面时检查同步状态；若不新鲜则自动同步一次 */
  async function loadSyncStatus() {
    const text = document.getElementById('sync-status-text');
    const btn = document.getElementById('sync-now-btn');
    const bar = document.getElementById('sync-status-bar');
    if (!text) return;
    if (btn) btn.style.display = '';
    try {
      const resp = await fetch('/api/data/sync_status');
      const data = await resp.json();
      const state = data.state || {};
      const result = state.result;
      if (result === 'success') {
        const t = formatSyncTime(state.updated_at);
        text.textContent = '数据更新于 ' + t +
          (data.latest_run_date ? '，最新跑步 ' + data.latest_run_date : '') +
          (data.fresh ? '（已是最新）' : '（可点“立即同步”刷新）');
        if (bar) bar.style.borderColor = data.fresh
          ? 'var(--rc-success, #2ecc71)' : 'var(--rc-border, #2a3342)';
      } else if (result === 'failed') {
        text.textContent = '上次同步失败：' + (state.error || '未知错误') +
          '（可点“立即同步”重试，或手动上传文件）';
        if (bar) bar.style.borderColor = 'var(--rc-danger, #e74c3c)';
        if (/登录|令牌|token|playwright|mfa|验证|未配置/i.test(state.error || '')) {
          showLoginBtn();
        }
      } else {
        text.textContent = '尚未同步过 Garmin，点击下方“立即同步”拉取最新数据';
      }
      // 不新鲜（超过 30 分钟或从未成功）→ 打开即自动同步一次
      if (!data.fresh) {
        syncGarmin('auto');
      }
    } catch (e) {
      text.textContent = '无法获取同步状态（后端可能未启动）';
    }
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