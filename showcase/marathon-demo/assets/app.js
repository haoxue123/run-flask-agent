// ===== 交互逻辑（纯前端，无后端）=====

// 导航高亮
const page = document.body.dataset.page;
document.querySelectorAll('.nav-links a').forEach(a => {
  if (a.dataset.page === page) a.classList.add('active');
});

// SVG 折线图
function drawChart(elId, values, color) {
  const el = document.getElementById(elId);
  if (!el || !values.length) return;
  const w = 600, h = 180, pad = 28;
  const max = Math.max(...values), min = Math.min(...values), range = (max - min) || 1;
  const pts = values.map((v, i) => {
    const x = pad + i * (w - 2 * pad) / (values.length - 1);
    const y = h - pad - ((v - min) / range) * (h - 2 * pad);
    return [x, y];
  });
  const poly = pts.map(p => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
  const area = pad + ',' + (h - pad) + ' ' + poly + ' ' + (w - pad) + ',' + (h - pad);
  el.innerHTML =
    '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" width="100%" height="180">' +
    '<polygon points="' + area + '" fill="' + color + '22"/>' +
    '<polyline points="' + poly + '" fill="none" stroke="' + color + '" stroke-width="2.5" stroke-linejoin="round"/>' +
    pts.map(p => '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="3" fill="' + color + '"/>').join('') +
    '</svg>';
}

// ===== 页面 1：数据总览 =====
if (document.getElementById('runList')) {
  const kpis = [
    { v: DATA.kpis.totalKm + ' km', l: '累计跑量' },
    { v: DATA.kpis.lastPace, l: '最近配速' },
    { v: DATA.kpis.vo2max, l: 'VO2max' },
    { v: DATA.kpis.load, l: '训练负荷' },
    { v: DATA.kpis.runs, l: '跑步次数' }
  ];
  document.getElementById('kpis').innerHTML = kpis.map(k =>
    '<div class="kpi"><div class="v">' + k.v + '</div><div class="l">' + k.l + '</div></div>').join('');
  document.getElementById('insights').innerHTML = DATA.insights.map(t => '<li>' + t + '</li>').join('');

  const list = document.getElementById('runList');
  function render(filter) {
    const f = (filter || '').trim().toLowerCase();
    const items = DATA.runs.filter(r => !f || r.route.toLowerCase().includes(f) || r.type.toLowerCase().includes(f));
    list.innerHTML = items.map(r =>
      '<li class="run-item" data-id="' + r.id + '">' +
      '<div class="top"><span><span class="tag">' + r.type + '</span>' + r.date + '</span><span>' + r.distance + ' km</span></div>' +
      '<div class="sub">配速 ' + r.pace + ' · 心率 ' + r.hr + ' · ' + r.route + ' · 爬升 ' + r.elev + 'm</div>' +
      '</li>').join('') || '<li class="muted">无匹配结果</li>';
  }
  render();
  document.getElementById('searchInput').addEventListener('input', e => render(e.target.value));
  list.addEventListener('click', e => {
    const li = e.target.closest('.run-item');
    if (li) location.href = 'analysis.html?run=' + li.dataset.id;
  });
}

// ===== 页面 2：深度分析（详情查看）=====
if (document.getElementById('analysisView')) {
  const id = new URLSearchParams(location.search).get('run') || DATA.runs[0].id;
  const run = DATA.runs.find(r => r.id === id) || DATA.runs[0];
  document.getElementById('aTitle').textContent = run.date + ' · ' + run.type + ' · ' + run.route;
  document.getElementById('aMeta').innerHTML =
    '<tr><th>距离</th><td>' + run.distance + ' km</td><th>配速</th><td>' + run.pace + '</td></tr>' +
    '<tr><th>平均心率</th><td>' + run.hr + ' bpm</td><th>爬升</th><td>' + run.elev + ' m</td></tr>';
  drawChart('paceChart', run.splits, '#0e7c8a');
  const zones = [
    { n: 'Z1 恢复', c: '#3a8f6b', p: 15 }, { n: 'Z2 有氧', c: '#0e7c8a', p: 45 },
    { n: 'Z3 节奏', c: '#e8833a', p: 28 }, { n: 'Z4 无氧', c: '#cf6f28', p: 12 }
  ];
  document.getElementById('hrZones').innerHTML = zones.map(z =>
    '<span style="background:' + z.c + '">' + z.n + ' ' + z.p + '%</span>').join('');
}

// ===== 页面 3：训练计划（筛选）=====
if (document.getElementById('planList')) {
  const types = ['全部', ...new Set(DATA.plan.map(p => p.type))];
  document.getElementById('filters').innerHTML = types.map((t, i) =>
    '<button class="filter' + (i === 0 ? ' active' : '') + '" data-type="' + t + '">' + t + '</button>').join('');
  const list = document.getElementById('planList');
  function render(type) {
    const items = type === '全部' ? DATA.plan : DATA.plan.filter(p => p.type === type);
    list.innerHTML = items.map(p =>
      '<li class="plan-item"><span class="date">' + p.date + '</span>' +
      '<span class="title">' + p.title + '</span>' +
      (p.done ? '<span class="done">✓ 已完成</span>' : '<span class="todo">待执行</span>') + '</li>').join('');
  }
  render('全部');
  document.getElementById('filters').addEventListener('click', e => {
    const b = e.target.closest('.filter'); if (!b) return;
    document.querySelectorAll('#filters .filter').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    render(b.dataset.type);
  });
}

// ===== 页面 4：长期趋势（多图 + 指标切换）=====
if (document.getElementById('trendCharts')) {
  const metrics = {
    pace:   { label: '平均配速 (s/km，越低越好)', color: '#0e7c8a', key: 'pace' },
    volume: { label: '月跑量 (km)', color: '#e8833a', key: 'volume' },
    vo2max: { label: 'VO2max', color: '#3a8f6b', key: 'vo2max' }
  };
  const keys = Object.keys(metrics);
  document.getElementById('trendFilters').innerHTML = keys.map((k, i) =>
    '<button class="filter' + (i === 0 ? ' active' : '') + '" data-metric="' + k + '">' + metrics[k].label + '</button>').join('');
  const box = document.getElementById('trendCharts');
  function render(mk) {
    const m = metrics[mk];
    box.innerHTML = '<div class="chart" id="tchart"></div>';
    drawChart('tchart', DATA.trends[m.key].map(d => d.v), m.color);
    document.getElementById('trendCaption').textContent = m.label;
  }
  render('pace');
  document.getElementById('trendFilters').addEventListener('click', e => {
    const b = e.target.closest('.filter'); if (!b) return;
    document.querySelectorAll('#trendFilters .filter').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    render(b.dataset.metric);
  });
}

// ===== 页面 5：教练对话（表单视觉 + 反馈）=====
if (document.getElementById('chat')) {
  const chat = document.getElementById('chat');
  function addBubble(text, who) {
    const d = document.createElement('div');
    d.className = 'bubble ' + who;
    d.textContent = text;
    chat.appendChild(d);
    chat.scrollTop = chat.scrollHeight;
  }
  addBubble('你好，我是你的 AI 跑步教练。可以问我训练安排、配速、恢复等相关问题。', 'coach');
  const input = document.getElementById('coachInput');
  document.getElementById('coachForm').addEventListener('submit', e => {
    e.preventDefault();
    const t = input.value.trim(); if (!t) return;
    addBubble(t, 'user'); input.value = '';
    const reply = DATA.coach[t] || '这是个好问题。基于你近期数据，我建议保持当前节奏，长距离日后安排恢复跑，并关注晨起静息心率。';
    setTimeout(() => addBubble(reply, 'coach'), 450);
  });
  document.getElementById('feedbackForm').addEventListener('submit', e => {
    e.preventDefault();
    document.getElementById('fbMsg').hidden = false;
  });
  // 快捷问题
  document.querySelectorAll('.quick-q').forEach(q => q.addEventListener('click', () => {
    input.value = q.textContent; input.focus();
  }));
}
