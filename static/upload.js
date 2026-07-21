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
  });

  UPLOAD.showFiles = showFiles;
  UPLOAD.buildFormData = buildFormData;
  UPLOAD.refreshData = refreshData;
  UPLOAD.updateTargetDist = updateTargetDist;

  // 导出到全局
  window.COACH = window.COACH || {};
  window.COACH.upload = UPLOAD;
})();