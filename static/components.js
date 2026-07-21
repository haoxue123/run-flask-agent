/**
 * COACH Design System v2 — Reusable Components
 * ============================================================
 * Exports: renderSkeleton, showToast, renderEmptyState
 */

(function() {
  'use strict';

  // ============================================================
  // Skeleton — loading placeholder
  // ============================================================
  function renderSkeleton(type) {
    var html = '';
    if (type === 'card') {
      html = '<div class="ds-skeleton" style="height:120px;width:100%"></div>';
    } else if (type === 'list') {
      for (var i = 0; i < 3; i++) {
        html += '<div class="ds-skeleton" style="height:16px;width:100%;margin-bottom:8px"></div>';
      }
    } else if (type === 'chart') {
      html = '<div class="ds-skeleton" style="height:200px;width:100%"></div>';
    } else {
      html = '<div class="ds-skeleton" style="height:60px;width:100%"></div>';
    }
    return html;
  }

  // ============================================================
  // Toast — notification banner
  // ============================================================
  function showToast(message, type) {
    type = type || 'success';
    var existing = document.querySelector('.ds-toast');
    if (existing) existing.remove();

    var toast = document.createElement('div');
    toast.className = 'ds-toast ds-toast--' + type;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(function() {
      toast.classList.add('ds-toast--out');
      setTimeout(function() { toast.remove(); }, 300);
    }, 3000);
  }

  // ============================================================
  // EmptyState — empty data placeholder
  // ============================================================
  function renderEmptyState(icon, title, desc, actionLabel, actionUrl) {
    var html = '<div class="ds-empty-state">';
    if (icon) {
      html += '<i data-lucide="' + icon + '"></i>';
    }
    html += '<h3>' + (title || '暂无数据') + '</h3>';
    if (desc) {
      html += '<p>' + desc + '</p>';
    }
    if (actionLabel && actionUrl) {
      html += '<a href="' + actionUrl + '" class="ds-btn inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium" style="background:var(--ds-button-primary-bg);color:var(--ds-button-primary-text)">' + actionLabel + '</a>';
    }
    html += '</div>';
    return html;
  }

  // ============================================================
  // Export to global
  // ============================================================
  window.DS = {
    renderSkeleton: renderSkeleton,
    showToast: showToast,
    renderEmptyState: renderEmptyState
  };
})();