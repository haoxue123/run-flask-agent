/**
 * 训练计划月历网格
 * 支持结构化数据（来自 /api/plan/load）和旧格式纯文本（fallback 解析）。
 */
(function() {
    'use strict';

    /**
     * 解析计划文本，提取每天信息（旧格式 fallback）
     * @param {string} planText - LLM 生成的训练计划文本
     * @returns {Array} - [{day: 1, type: "慢跑", pace: "6:00", distance: "5km", detail: "..."}, ...]
     */
    function parsePlan(planText) {
        if (!planText) return [];
        const days = [];
        const chineseNum = { '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7 };
        
        let match;
        const matches = [];
        const regex = /(?:第\s*(\d+)\s*天|Day\s*(\d+)|第\s*([一二三四五六七])\s*天)/gi;
        while ((match = regex.exec(planText)) !== null) {
            const d = match[1] ? parseInt(match[1]) : (match[2] ? parseInt(match[2]) : chineseNum[match[3]]);
            matches.push(d);
        }
        
        for (let i = 0; i < matches.length; i++) {
            const day = matches[i];
            const dayRegex = new RegExp('第\\s*' + day + '\\s*天');
            const prevMatch = dayRegex.exec(planText);
            const prevIdx = prevMatch ? prevMatch.index : -1;
            let content = '';
            if (prevIdx >= 0) {
                let endIdx = planText.length;
                if (i + 1 < matches.length) {
                    const nextRegex = new RegExp('第\\s*' + matches[i+1] + '\\s*天');
                    const nextMatch = nextRegex.exec(planText);
                    if (nextMatch) endIdx = nextMatch.index;
                }
                content = planText.substring(prevIdx, endIdx);
            }
            
            // 提取训练类型
            const KNOWN_TYPES = ['休息', '慢跑', '节奏跑', '间歇跑', '冲刺跑', '长距离', '恢复跑', '轻松跑', 'LSD', 'Tempo', 'Interval', 'Rest', 'Easy', 'Recovery'];
            let type = '休息';
            const typePatterns = KNOWN_TYPES.map(t => new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'));
            for (let j = 0; j < typePatterns.length; j++) {
                const m = content.match(typePatterns[j]);
                if (m) {
                    type = KNOWN_TYPES[j];
                    break;
                }
            }
            
            // 提取配速
            let pace = '';
            const paceMatch = content.match(/(?:配速|pace)[：:\s]*(\d+['′:]\d+)/i);
            if (paceMatch) {
                pace = paceMatch[1];
            }
            
            // 提取距离
            let distance = '';
            const distMatch = content.match(/(\d+\.?\d*)\s*(?:公里|km|KM)/i);
            if (distMatch) {
                distance = distMatch[1] + 'km';
            }
            
            days.push({
                day: day,
                type: type,
                pace: pace,
                distance: distance,
                detail: content.trim().replace(/\n/g, ' ').substring(0, 100),
                completed: false,
                is_rest: type === '休息'
            });
        }
        
        return days;
    }

    /**
     * 判断数据是否为结构化格式（数组且每项含 day 字段）
     * @param {*} rawData
     * @returns {boolean}
     */
    function isStructured(rawData) {
        if (!Array.isArray(rawData) || rawData.length === 0) return false;
        return rawData[0] && typeof rawData[0] === 'object' && 'day' in rawData[0];
    }

    /**
     * 将结构化数据标准化为渲染所需格式
     * @param {Array} structuredDays
     * @returns {Array}
     */
    function normalizeStructuredDays(structuredDays) {
        return structuredDays.map(function(d) {
            return {
                day: d.day,
                weekday: d.weekday,
                type: d.type || '休息',
                is_rest: d.is_rest || (d.type === '休息'),
                distance: d.distance_km != null ? (d.distance_km + 'km') : '',
                distance_km: d.distance_km,
                pace: d.target_pace || '',
                target_pace_sec: d.target_pace_sec,
                duration_min: d.duration_min,
                heart_rate_zone: d.heart_rate_zone,
                note: d.note || '',
                completed: d.completed || false,
                actual_distance_km: d.actual_distance_km,
                actual_pace_sec: d.actual_pace_sec,
                actual_duration_min: d.actual_duration_min,
                feeling: d.feeling
            };
        });
    }

    /**
     * 渲染月历网格
     * @param {HTMLElement} container - 容器元素
     * @param {Array} rawData - 结构化数据数组 或 parsePlan 返回的数组
     */
    function renderCalendar(container, rawData) {
        if (!container) return;
        if (!rawData || rawData.length === 0) {
            container.innerHTML = '<div class="text-sm text-muted-foreground py-4 text-center">暂无法解析计划</div>';
            return;
        }

        // 检测数据格式：结构化 vs 旧格式
        var days;
        if (isStructured(rawData)) {
            days = normalizeStructuredDays(rawData);
        } else {
            // 旧格式（已由 parsePlan 处理过），直接使用
            days = rawData;
        }
        
        const zoneColors = {
            '休息': 'var(--rc-muted)',
            '恢复跑': 'var(--rc-zone-1)',
            '轻松跑': 'var(--rc-zone-1)',
            '慢跑': 'var(--rc-zone-2)',
            '节奏跑': 'var(--rc-zone-3)',
            '长距离': 'var(--rc-zone-3)',
            '间歇跑': 'var(--rc-zone-4)',
            '冲刺跑': 'var(--rc-zone-5)',
            'LSD': 'var(--rc-zone-3)',
        };
        
        const totalWeeks = Math.ceil(days.length / 7);
        const weekLabels = ['一', '二', '三', '四', '五', '六', '日'];
        
        let html = '';
        
        for (let w = 0; w < totalWeeks; w++) {
            if (totalWeeks > 1) {
                html += '<div class="text-xs text-muted-foreground font-medium mb-1 mt-2">第 ' + (w + 1) + ' 周</div>';
            }
            html += '<div class="grid grid-cols-7 gap-2">';
            if (w === 0) {
                for (const wl of weekLabels) {
                    html += '<div class="text-center text-xs text-muted-foreground">' + wl + '</div>';
                }
            }
            for (let d = 0; d < 7; d++) {
                const idx = w * 7 + d;
                if (idx >= days.length) break;
                const day = days[idx];
                const color = zoneColors[day.type] || 'var(--rc-muted)';
                const isRest = day.is_rest || day.type === '休息';
                const completed = day.completed || false;
                
                html += '<div class="plan-day aspect-square rounded-md border border-border p-1.5 flex flex-col text-left relative' + (completed ? ' completed' : '') + '" data-day="' + day.day + '" style="border-left: 3px solid ' + color + '">';
                html += '<div class="flex items-center justify-between">';
                html += '<span class="text-[11px] leading-none tnum">' + day.day + '</span>';
                if (completed) {
                    html += '<i data-lucide="check-circle" class="w-3.5 h-3.5 text-[color:var(--state-success)]"></i>';
                }
                html += '</div>';
                html += '<span class="mt-auto h-1.5 rounded-full" style="background-color: ' + color + '"></span>';
                html += '<span class="text-[10px] leading-tight truncate mt-1 plan-type' + (isRest ? ' text-muted-foreground' : '') + '">' + (day.type || '休息') + '</span>';
                html += '<span class="text-[10px] text-primary leading-tight truncate plan-pace">' + (day.pace || '') + '</span>';
                html += '<span class="text-[10px] text-muted-foreground leading-tight plan-distance">' + (day.distance || '') + '</span>';
                html += '</div>';
            }
            html += '</div>';
        }
        container.innerHTML = html;

        // 绑定点击事件 — 传递完整结构化数据
        container.querySelectorAll('.plan-day').forEach(el => {
            el.addEventListener('click', function() {
                const dayNum = parseInt(this.getAttribute('data-day'));
                const dayData = days.find(d => d.day === dayNum) || {};
                const event = new CustomEvent('plan-day-click', {
                    detail: {
                        day: dayNum,
                        type: dayData.type || '',
                        pace: dayData.pace || '',
                        distance: dayData.distance || '',
                        completed: dayData.completed || false,
                        is_rest: dayData.is_rest || false,
                        distance_km: dayData.distance_km,
                        target_pace_sec: dayData.target_pace_sec,
                        duration_min: dayData.duration_min,
                        heart_rate_zone: dayData.heart_rate_zone,
                        note: dayData.note || '',
                        actual_distance_km: dayData.actual_distance_km,
                        actual_pace_sec: dayData.actual_pace_sec,
                        actual_duration_min: dayData.actual_duration_min,
                        feeling: dayData.feeling
                    }
                });
                document.dispatchEvent(event);
            });
        });

        // 初始化 lucide 图标
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    // 导出到全局
    window.PlanCalendar = {
        parsePlan: parsePlan,
        renderCalendar: renderCalendar,
        isStructured: isStructured,
        normalizeStructuredDays: normalizeStructuredDays
    };
})();