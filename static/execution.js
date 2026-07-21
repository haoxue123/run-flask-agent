/**
 * 训练日执行追踪面板
 * 弹出 modal 显示当日计划详情 + 实际执行表单，提交到 /api/plan/update_day
 */
(function() {
    'use strict';

    var currentDayData = null;

    /**
     * 将秒数转换为 mm:ss 格式
     * @param {number} sec
     * @returns {string}
     */
    function secToPace(sec) {
        if (sec == null || isNaN(sec)) return '';
        var m = Math.floor(sec / 60);
        var s = Math.round(sec % 60);
        return m + ':' + (s < 10 ? '0' : '') + s;
    }

    /**
     * 将 mm:ss 格式转换为秒数
     * @param {string} paceStr
     * @returns {number|null}
     */
    function paceToSec(paceStr) {
        if (!paceStr) return null;
        var parts = paceStr.split(':');
        if (parts.length !== 2) return null;
        var m = parseInt(parts[0], 10);
        var s = parseInt(parts[1], 10);
        if (isNaN(m) || isNaN(s)) return null;
        return m * 60 + s;
    }

    /**
     * 打开执行面板
     * @param {Object} dayData - 训练日数据（含计划信息和实际执行数据）
     */
    function openExecutionPanel(dayData) {
        currentDayData = dayData || {};
        var panel = document.getElementById('execution-panel');
        if (!panel) return;

        var isRest = currentDayData.is_rest || currentDayData.type === '休息';
        var completed = currentDayData.completed || false;

        // 填充计划详情
        var planDetailEl = document.getElementById('exec-plan-detail');
        if (planDetailEl) {
            var html = '';
            html += '<div class="exec-detail-row"><span class="exec-detail-label">训练日</span><span class="exec-detail-value">第 ' + (currentDayData.day || '-') + ' 天</span></div>';
            html += '<div class="exec-detail-row"><span class="exec-detail-label">类型</span><span class="exec-detail-value">' + (currentDayData.type || '休息') + '</span></div>';
            if (isRest) {
                html += '<div class="exec-detail-row"><span class="exec-detail-label">说明</span><span class="exec-detail-value">休息日，无需训练</span></div>';
            } else {
                if (currentDayData.distance_km != null) {
                    html += '<div class="exec-detail-row"><span class="exec-detail-label">计划距离</span><span class="exec-detail-value">' + currentDayData.distance_km + ' km</span></div>';
                }
                if (currentDayData.target_pace_sec != null) {
                    html += '<div class="exec-detail-row"><span class="exec-detail-label">目标配速</span><span class="exec-detail-value">' + secToPace(currentDayData.target_pace_sec) + ' /km</span></div>';
                }
                if (currentDayData.duration_min != null) {
                    html += '<div class="exec-detail-row"><span class="exec-detail-label">计划时长</span><span class="exec-detail-value">' + currentDayData.duration_min + ' 分钟</span></div>';
                }
                if (currentDayData.heart_rate_zone) {
                    html += '<div class="exec-detail-row"><span class="exec-detail-label">心率区间</span><span class="exec-detail-value">' + currentDayData.heart_rate_zone + '</span></div>';
                }
                if (currentDayData.note) {
                    html += '<div class="exec-detail-row"><span class="exec-detail-label">备注</span><span class="exec-detail-value">' + currentDayData.note + '</span></div>';
                }
            }
            planDetailEl.innerHTML = html;
        }

        // 填充实际执行表单
        var actualDistEl = document.getElementById('exec-actual-distance');
        var actualPaceEl = document.getElementById('exec-actual-pace');
        var actualDurationEl = document.getElementById('exec-actual-duration');
        var feelingEl = document.getElementById('exec-feeling');

        if (actualDistEl) {
            actualDistEl.value = currentDayData.actual_distance_km != null ? currentDayData.actual_distance_km : '';
        }
        if (actualPaceEl) {
            actualPaceEl.value = currentDayData.actual_pace_sec != null ? secToPace(currentDayData.actual_pace_sec) : '';
        }
        if (actualDurationEl) {
            actualDurationEl.value = currentDayData.actual_duration_min != null ? currentDayData.actual_duration_min : '';
        }
        if (feelingEl) {
            feelingEl.value = currentDayData.feeling || '';
        }

        // 更新标题和按钮状态
        var titleEl = document.getElementById('exec-day-label');
        if (titleEl) {
            titleEl.textContent = '第 ' + (currentDayData.day || '-') + ' 天 · ' + (currentDayData.type || '休息');
        }

        var btnEl = document.getElementById('exec-submit-btn');
        var btnTextEl = document.getElementById('exec-submit-text');
        if (btnEl) {
            btnEl.disabled = false;
        }
        if (btnTextEl) {
            btnTextEl.textContent = isRest ? '标记休息完成' : '标记完成';
        }

        // 显示面板
        panel.style.display = 'flex';

        // 初始化图标
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    /**
     * 关闭执行面板
     */
    function closeExecutionPanel() {
        var panel = document.getElementById('execution-panel');
        if (panel) {
            panel.style.display = 'none';
        }
        currentDayData = null;
    }

    /**
     * 提交执行数据
     */
    function submitExecution() {
        if (!currentDayData) return;

        var actualDistEl = document.getElementById('exec-actual-distance');
        var actualPaceEl = document.getElementById('exec-actual-pace');
        var actualDurationEl = document.getElementById('exec-actual-duration');
        var feelingEl = document.getElementById('exec-feeling');
        var btn = document.getElementById('exec-submit-btn');
        var btnText = document.getElementById('exec-submit-text');

        var payload = {
            day: currentDayData.day,
            completed: true,
            actual_distance_km: actualDistEl && actualDistEl.value ? parseFloat(actualDistEl.value) : null,
            actual_pace_sec: actualPaceEl && actualPaceEl.value ? paceToSec(actualPaceEl.value) : null,
            actual_duration_min: actualDurationEl && actualDurationEl.value ? parseFloat(actualDurationEl.value) : null,
            feeling: feelingEl ? feelingEl.value.trim() : ''
        };

        if (btn) btn.disabled = true;
        if (btnText) btnText.textContent = '保存中...';

        fetch('/api/plan/update_day', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(function(resp) { return resp.json(); })
        .then(function(data) {
            if (data.success) {
                // 更新当前数据
                currentDayData.completed = true;
                currentDayData.actual_distance_km = payload.actual_distance_km;
                currentDayData.actual_pace_sec = payload.actual_pace_sec;
                currentDayData.actual_duration_min = payload.actual_duration_min;
                currentDayData.feeling = payload.feeling;

                // 刷新日历
                if (data.plan && window.PlanCalendar) {
                    var calendar = document.getElementById('plan-calendar');
                    if (calendar) {
                        window.PlanCalendar.renderCalendar(calendar, data.plan);
                    }
                }

                // 触发刷新事件（供 training.html 更新完成率和摘要）
                document.dispatchEvent(new CustomEvent('execution-saved', {
                    detail: { day: currentDayData.day, plan: data.plan }
                }));

                if (window.DS && window.DS.showToast) {
                    window.DS.showToast('执行记录已保存', 'success');
                }

                closeExecutionPanel();
            } else {
                if (window.DS && window.DS.showToast) {
                    window.DS.showToast(data.error || '保存失败', 'error');
                }
            }
        })
        .catch(function(e) {
            if (window.DS && window.DS.showToast) {
                window.DS.showToast('请求失败：' + e.message, 'error');
            }
        })
        .finally(function() {
            if (btn) btn.disabled = false;
            if (btnText) btnText.textContent = '标记完成';
        });
    }

    // 导出到全局
    window.ExecutionPanel = {
        open: openExecutionPanel,
        close: closeExecutionPanel,
        submit: submitExecution
    };
})();