'use strict';

const ST = {
  good: { label: '良好', icon: '●', color: '#0ca30c' },
  caution: { label: '注意', icon: '▲', color: '#fab219' },
  warn: { label: '警戒', icon: '■', color: '#d03b3b' },
  na: { label: 'データ不足', icon: '－', color: '#d5d8dc' },
};
const CODE = { g: 'good', c: 'caution', w: 'warn', n: 'na' };
const QNAMES = { reference: '参考' };
const TRANSFORM_LABEL = { none: '水準', yoy_pct: '前年同期比', mom_pct: '前月比', diff: '前月差' };
const C = { blue: '#2a78d6', orange: '#d9571f', ma: '#5b6168', grid: '#e6e8eb', tick: '#3b4148', rec: '#e9eaec', ref: '#3b4148' };

let IND = null, SUM = null, HIST = null;
const seriesCache = {};
let filter = 'all', search = '';
let favs = loadFavs();

// ---------- ユーティリティ ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function loadFavs() { try { return new Set(JSON.parse(localStorage.getItem('umt-fav') || '[]')); } catch { return new Set(); } }
function saveFavs() { try { localStorage.setItem('umt-fav', JSON.stringify([...favs])); } catch { /* 保存できない環境では無視 */ } }
function fmt(v, d) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return Number(v).toLocaleString('ja-JP', { minimumFractionDigits: d, maximumFractionDigits: d });
}
function fmtSigned(v, d) { if (v === null || v === undefined) return '—'; return (v > 0 ? '+' : v < 0 ? '−' : '±') + fmt(Math.abs(v), d); }
function chgUnit(ind) { return ind.unit === '%' ? 'pt' : ind.unit; }
function dateLabel(s, freq) {
  if (!s) return '—';
  const [y, m, d] = s.split('-').map(Number);
  if (freq === 'Q') return `${y}年Q${Math.floor((m - 1) / 3) + 1}`;
  if (freq === 'M') return `${y}年${m}月`;
  return `${y}/${m}/${d}`;
}
const WD = ['日', '月', '火', '水', '木', '金', '土'];
function mdw(s) { const d = new Date(s + 'T00:00:00'); return `${d.getMonth() + 1}/${d.getDate()}（${WD[d.getDay()]}）`; }
function badge(state, small) {
  const s = ST[state] || ST.na;
  return `<span class="badge s-${state || 'na'}"${small ? ' style="font-size:12px;padding:0 8px"' : ''}>${s.icon} ${s.label}</span>`;
}
// 変化が良い方向か（色分け用）
function changeClass(ind, from, to) {
  if (from === null || from === undefined || to === null || to === undefined) return 'flat';
  const diff = to - from;
  if (Math.abs(diff) < 1e-9) return 'flat';
  if (ind.direction === 'up') return diff > 0 ? 'up-good' : 'down-bad';
  if (ind.direction === 'down') return diff < 0 ? 'up-good' : 'down-bad';
  if (ind.direction === 'target') return Math.abs(to - 2) < Math.abs(from - 2) ? 'up-good' : 'down-bad';
  return 'flat';
}
// 前月比・前月差の指標は『1年前の前月比との差』に意味がないので出さない
function showYear(i) { return !['mom_pct', 'diff'].includes(i.transform); }
const TIER_ORDER = { S: 0, A: 1, B: 2, C: 3 };
function tierChip(t) {
  if (!t) return '';
  const label = (IND.settings.tier_labels || {})[t] || t;
  return `<span class="tier t-${t}" title="重要度：${esc(label)}">${esc(label)}</span>`;
}
function arrow(v) { return v > 0 ? '▲' : v < 0 ? '▼' : '→'; }

// ---------- 読み込み ----------
async function load() {
  const get = (p) => fetch(p, { cache: 'no-cache' }).then((r) => { if (!r.ok) throw new Error(p); return r.json(); });
  try {
    [IND, SUM, HIST] = await Promise.all([get('data/indicators.json'), get('data/summary.json'), get('data/history.json')]);
  } catch (e) {
    $('meta').textContent = 'データを読み込めませんでした（' + e.message + '）。';
    return;
  }
  document.title = IND.settings.site_title;
  $('title').textContent = IND.settings.site_title;
  IND.questions.forEach((q) => { QNAMES[q.id] = q.name; });
  renderHeader(); renderQuestions(); renderNotable(); renderCalendar(); renderChips(); renderGroups(); renderHistory();
}

function renderHeader() {
  const n = IND.indicators.length;
  $('meta').textContent = `データ更新：${SUM.generated_at_jst}（日本時間）／ 指標 ${n}本 ／ 出典 FRED`;
  const a = [];
  if (SUM.test_mode) a.push('<div class="alert test">テストデータで表示中です。本物の経済データではありません。</div>');
  if (SUM.failed.length) a.push(`<div class="alert">取得に失敗した指標：${SUM.failed.map((x) => esc(x.name)).join('、')}</div>`);
  if (SUM.fallback_used.length) a.push(`<div class="alert">取得に失敗したため前回のデータで表示：${SUM.fallback_used.map(esc).join('、')}</div>`);
  $('alerts').innerHTML = a.join('');
}

function renderQuestions() {
  $('questions').innerHTML = SUM.questions.map((q) => `
    <button class="qcard st-${q.state}" data-q="${q.id}" title="${esc(q.rule_text)}">
      <div class="qhead"><span class="qname">${esc(q.name)}</span>${badge(q.state)}</div>
      <p class="qq">${esc(q.question)}</p>
      <p class="qvals">${esc(q.reasons[0] || '')}</p>
      <p class="qwhy">${esc(q.reasons[1] || '')}</p>
    </button>`).join('') +
    `<details style="grid-column:1/-1"><summary>判定ルールを見る</summary><ul>${SUM.questions.map((q) => `<li><b>${esc(q.name)}</b>：${esc(q.rule_text)}</li>`).join('')}</ul></details>`;
  document.querySelectorAll('.qcard').forEach((b) => b.addEventListener('click', () => {
    filter = b.dataset.q; renderChips(); renderGroups();
    document.getElementById('chips').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }));
  if (IND.settings.show_cycle && SUM.cycle) {
    $('cycle').innerHTML = `<div class="cyclebox"><b>景気サイクル（参考）：${esc(SUM.cycle)}</b>　景気の判定×FF金利の6か月の方向で機械的に分類。2か月続けて同じ判定の時だけ切り替えています。</div>`;
  }
}

function renderNotable() {
  const items = [];
  SUM.notable.forEach((n) => items.push(`<li><span class="big">大きな変化</span> <b>${esc(n.name)}</b>：直近の変化（${fmtSigned(n.change, 2)}${esc(n.unit === '%' ? 'pt' : n.unit)}）が過去20年の変化の中で珍しい大きさです（z=${fmtSigned(n.z, 1)}）。</li>`));
  SUM.stale.forEach((s) => items.push(`<li><span class="big">更新遅れ</span> <b>${esc(s.name)}</b>：最新データが${esc(s.latest_date)}（${s.age_days}日前）のままです。発表の延期か取得の問題の可能性があります。</li>`));
  (SUM.warnings || []).forEach((w) => items.push(`<li>${esc(w)}</li>`));
  $('notable').innerHTML = items.length ? `<ul class="list">${items.join('')}</ul>` : '<p class="empty">特になし</p>';
}

function renderCalendar() {
  const u = SUM.upcoming || [];
  $('calendar').innerHTML = u.length
    ? `<ul class="list">${u.map((x) => `<li><span class="d">${mdw(x.date)}</span>${(x.items || (x.names || []).map((n) => ({ name: n }))).map((it) => `<span class="calitem">${tierChip(it.tier)}${esc(it.name)}</span>`).join('')}</li>`).join('')}</ul>
       <p class="note">日次の金利・ドル・VIXは毎営業日更新のため省いています。日付は米国時間（日本では翌日の夜〜未明）。</p>`
    : '<p class="empty">予定を取得できませんでした。</p>';
}

// ---------- 指標一覧 ----------
function renderChips() {
  const opts = [['all', 'すべて'], ...IND.questions.map((q) => [q.id, q.name]), ['reference', '参考'], ['fav', '★ お気に入り']];
  $('chips').innerHTML = opts.map(([k, v]) => `<button class="chip" role="tab" data-f="${k}" aria-selected="${filter === k}">${esc(v)}</button>`).join('');
  document.querySelectorAll('.chip').forEach((c) => c.addEventListener('click', () => { filter = c.dataset.f; renderChips(); renderGroups(); }));
}

function renderGroups() {
  const qs = [...IND.questions.map((q) => ({ id: q.id, name: q.name, question: q.question })), { id: 'reference', name: '参考', question: '判定には使わない指標' }];
  const stateOf = Object.fromEntries(SUM.questions.map((q) => [q.id, q.state]));
  const html = qs.map((q) => {
    let list = IND.indicators.filter((i) => i.question === q.id);
    if (filter === 'fav') list = list.filter((i) => favs.has(i.id));
    else if (filter !== 'all' && filter !== q.id) list = [];
    if (search) list = list.filter((i) => i.name.includes(search));
    if (!list.length) return '';
    list.sort((a, b) => ((a.role === 'main' ? 0 : 1) - (b.role === 'main' ? 0 : 1)) || ((TIER_ORDER[a.tier] ?? 9) - (TIER_ORDER[b.tier] ?? 9)));
    return `<div class="group"><h3>${esc(q.name)} <small>${esc(q.question)}</small>${stateOf[q.id] ? badge(stateOf[q.id], true) : ''}</h3>
      <div class="grid">${list.map(card).join('')}</div></div>`;
  }).join('');
  const legend = `<p class="note">${['S', 'A', 'B', 'C'].map(tierChip).join(' ')}　${esc(IND.settings.tier_rule || '')}　「主役」は5つの判定に使う指標、「補助」「参考」は確認用。</p>`;
  $('groups').innerHTML = html ? legend + html : '<p class="empty">該当する指標がありません。</p>';
  document.querySelectorAll('.card').forEach((el) => {
    el.addEventListener('click', (e) => { if (e.target.closest('.fav')) return; openModal(el.dataset.id); });
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openModal(el.dataset.id); } });
  });
  document.querySelectorAll('.fav').forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const id = b.dataset.id; favs.has(id) ? favs.delete(id) : favs.add(id); saveFavs(); renderGroups();
  }));
}

function card(i) {
  if (!i.ok) return `<div class="card"><p class="cname">${esc(i.name)}</p><p class="empty">データなし</p></div>`;
  const cu = chgUnit(i);
  const roleTxt = i.role === 'main' ? '主役' : i.role === 'ref' ? '参考' : '補助';
  const next = i.next_release ? ` ・ 次回 ${mdw(i.next_release)}` : '';
  const stale = i.fresh.stale ? ` <span class="stale">（${i.fresh.age_days}日前のデータ）</span>` : '';
  const lag = i.id === 'umich' ? ' <span class="stale">※FREDは1か月遅れ</span>' : '';
  return `<div class="card ${i.role === 'main' ? 'main' : ''}" role="button" tabindex="0" data-id="${i.id}" aria-label="${esc(i.name)}の詳細を開く">
    <div class="ctop"><span><span class="role ${i.role === 'main' ? 'main' : ''}">${roleTxt}</span> ${i.zone ? badge(i.zone, true) : ''}</span>
      <button class="fav ${favs.has(i.id) ? 'on' : ''}" data-id="${i.id}" aria-label="お気に入り">${favs.has(i.id) ? '★' : '☆'}</button></div>
    <p class="cname">${tierChip(i.tier)}${esc(i.name)}</p>
    <div class="cval">${fmt(i.latest, i.decimals)}<small>${esc(i.unit)}</small>${i.big_move ? '<span class="big">大きな変化</span>' : ''}</div>
    <div class="cdate">${dateLabel(i.latest_date, i.freq)}${stale}${lag}${next}</div>
    ${spark(i)}
    <div class="chg">
      <span>前回から <span class="${changeClass(i, i.prev, i.latest)}">${arrow(i.change)} ${fmtSigned(i.change, i.decimals)}${esc(cu)}</span></span>
      ${showYear(i) ? `<span>1年前から <span class="${changeClass(i, i.year_ago, i.latest)}">${arrow(i.yoy_change)} ${fmtSigned(i.yoy_change, i.decimals)}${esc(cu)}</span></span>` : ''}
    </div>
    <div class="trend">${esc(i.trend.text)}${i.percentile_10y !== null ? `／過去10年で下から${i.percentile_10y}%の位置` : ''}</div>
  </div>`;
}

function spark(i) {
  const v = (i.spark || []).filter((x) => x !== null);
  if (v.length < 2) return '';
  const W = 260, H = 40, P = 3;
  let lo = Math.min(...v), hi = Math.max(...v);
  const ref = i.ref_line ? i.ref_line.value : null;
  if (ref !== null && ref >= lo - (hi - lo) * 0.3 && ref <= hi + (hi - lo) * 0.3) { lo = Math.min(lo, ref); hi = Math.max(hi, ref); }
  const sy = (x) => (hi === lo ? H / 2 : H - P - ((x - lo) / (hi - lo)) * (H - 2 * P));
  const pts = v.map((x, k) => `${(k / (v.length - 1)) * W},${sy(x).toFixed(1)}`).join(' ');
  const refLine = ref !== null && ref >= lo && ref <= hi ? `<line x1="0" x2="${W}" y1="${sy(ref)}" y2="${sy(ref)}" stroke="#5b6168" stroke-dasharray="3 3" stroke-width="1"/>` : '';
  const last = v[v.length - 1];
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${refLine}
    <polyline points="${pts}" fill="none" stroke="${C.blue}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
    <circle cx="${W}" cy="${sy(last)}" r="3" fill="${C.blue}"/></svg>`;
}

// ---------- 詳細（モーダル） ----------
let charts = [];
let modalState = { id: null, years: 5, cmp: '' };

async function getSeries(id) {
  if (!seriesCache[id]) {
    const r = await fetch(`data/series/${id}.json`, { cache: 'no-cache' });
    seriesCache[id] = await r.json();
  }
  return seriesCache[id];
}

async function openModal(id) {
  const i = IND.indicators.find((x) => x.id === id);
  if (!i) return;
  modalState = { id, years: 5, cmp: '' };
  const cu = chgUnit(i);
  const others = IND.indicators.filter((x) => x.id !== id && x.ok);
  $('m-body').innerHTML = `
    <h2 id="m-title" style="border:0;margin:0 44px 0 0">${tierChip(i.tier)}${esc(i.name)}</h2>
    <div style="margin-top:4px">${i.role === 'main' ? '<span class="role main">主役</span>' : ''} ${i.zone ? badge(i.zone, true) : ''} <span class="note">分類：${esc(QNAMES[i.question] || '')}／${esc(TRANSFORM_LABEL[i.transform])}</span></div>
    <p class="m-desc">${esc(i.desc)}</p>
    <div class="stats">
      <div class="stat"><span>最新（${dateLabel(i.latest_date, i.freq)}）</span><b>${fmt(i.latest, i.decimals)}${esc(i.unit)}</b></div>
      <div class="stat"><span>前回から</span><b class="${changeClass(i, i.prev, i.latest)}">${fmtSigned(i.change, i.decimals)}${esc(cu)}</b></div>
      ${showYear(i) ? `<div class="stat"><span>1年前から</span><b class="${changeClass(i, i.year_ago, i.latest)}">${fmtSigned(i.yoy_change, i.decimals)}${esc(cu)}</b></div>` : ''}
      <div class="stat"><span>過去10年での位置</span><b>${i.percentile_10y !== null ? `下から${i.percentile_10y}%` : '—'}</b></div>
      <div class="stat"><span>過去最高（${dateLabel(i.max.date, i.freq)}）</span><b>${fmt(i.max.value, i.decimals)}</b></div>
      <div class="stat"><span>過去最低（${dateLabel(i.min.date, i.freq)}）</span><b>${fmt(i.min.value, i.decimals)}</b></div>
      <div class="stat"><span>次回発表</span><b>${i.next_release ? mdw(i.next_release) : (i.freq === 'D' ? '毎営業日' : '—')}</b></div>
      <div class="stat"><span>変化の大きさ（z値）</span><b>${i.z !== null ? fmtSigned(i.z, 1) : '—'}</b></div>
    </div>
    <div class="ctrl" id="m-years">${[1, 3, 5, 10, 0].map((y) => `<button class="chip" data-y="${y}" aria-selected="${y === 5}">${y ? y + '年' : '全期間'}</button>`).join('')}
      <label style="margin-left:8px">比較する指標：<select id="m-cmp"><option value="">なし</option>${others.map((o) => `<option value="${o.id}">${esc(o.name)}</option>`).join('')}</select></label>
    </div>
    <div class="chart-legend"><span><i style="border-color:${C.blue}"></i>実績</span>${['M', 'W'].includes(i.freq) ? `<span><i style="border-top-style:dashed;border-color:${C.ma}"></i>${i.freq === 'M' ? '3か月平均' : '4週平均'}</span>` : ''}${i.ref_line ? `<span><i style="border-top-style:dotted;border-color:${C.ref}"></i>${esc(i.ref_line.label)}</span>` : ''}<span><span class="sw" style="background:${C.rec}"></span> 景気後退期（NBER）</span></div>
    <div class="chartbox"><canvas id="m-chart" aria-label="${esc(i.name)}の推移"></canvas></div>
    <div id="m-cmpbox" hidden><p class="note" style="margin:10px 0 0">比較：<b id="m-cmpname"></b>（同じ期間を上下に並べて表示。1つのグラフに2本の目盛りを重ねると見え方が操作できてしまうため）</p><div class="chartbox small"><canvas id="m-chart2"></canvas></div></div>
    <div class="readbox"><b>見方：</b>${esc(i.read)}</div>
    <div class="guide"><div class="g"><h4>良いとされる状態</h4>${esc(i.good)}</div><div class="c"><h4>注意が必要な状態</h4>${esc(i.caution)}</div></div>
    <div class="m-foot">
      出典：${sourceLinks(i)}${i.citation ? `／${esc(i.citation)}` : ''}<br>
      変化の大きさ（z値）は市場予想との比較ではなく、この指標の過去20年の変化幅と比べた珍しさ（±${IND.settings.big_move_z}以上で「大きな変化」）。<br>
      <button class="btn" id="m-csv">CSVをダウンロード</button>
    </div>`;
  $('modal').hidden = false;
  document.body.style.overflow = 'hidden';
  $('m-close').focus();
  document.querySelectorAll('#m-years .chip').forEach((b) => b.addEventListener('click', () => {
    modalState.years = Number(b.dataset.y);
    document.querySelectorAll('#m-years .chip').forEach((x) => x.setAttribute('aria-selected', x === b));
    drawCharts();
  }));
  $('m-cmp').addEventListener('change', (e) => { modalState.cmp = e.target.value; drawCharts(); });
  $('m-csv').addEventListener('click', () => downloadCsv(i));
  await drawCharts();
}

function closeModal() {
  charts.forEach((c) => c.destroy()); charts = [];
  $('modal').hidden = true; document.body.style.overflow = '';
}

function toPoints(s, key, from) {
  const out = [];
  const arr = s[key];
  if (!arr) return out;
  for (let k = 0; k < s.dates.length; k++) {
    const y = arr[k];
    if (y === null) continue;
    const x = Date.parse(s.dates[k] + 'T00:00:00Z');
    if (from && x < from) continue;
    out.push({ x, y });
  }
  return out;
}

const recessionPlugin = {
  id: 'recession',
  beforeDatasetsDraw(chart) {
    const { ctx, chartArea: a, scales: { x } } = chart;
    ctx.save(); ctx.fillStyle = C.rec;
    (IND.recessions || []).forEach(([s, e]) => {
      const x1 = Math.max(x.getPixelForValue(Date.parse(s)), a.left);
      const x2 = Math.min(x.getPixelForValue(Date.parse(e)), a.right);
      if (x2 > x1) ctx.fillRect(x1, a.top, x2 - x1, a.bottom - a.top);
    });
    ctx.restore();
  },
};
function refPlugin(ref) {
  return {
    id: 'refline',
    afterDatasetsDraw(chart) {
      if (!ref) return;
      const { ctx, chartArea: a, scales: { y } } = chart;
      const py = y.getPixelForValue(ref.value);
      if (py < a.top || py > a.bottom) return;
      ctx.save(); ctx.strokeStyle = C.ref; ctx.setLineDash([2, 3]); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = C.ref; ctx.font = '12px sans-serif';
      ctx.fillText(ref.label, a.left + 6, py - 5);
      ctx.restore();
    },
  };
}

function lineChart(canvas, datasets, ind, xmin, xmax) {
  const unit = ind.unit;
  return new Chart(canvas, {
    type: 'line',
    data: { datasets },
    plugins: [recessionPlugin, refPlugin(ind.ref_line)],
    options: {
      parsing: false, normalized: true, animation: false, maintainAspectRatio: false,
      interaction: { mode: 'nearest', axis: 'x', intersect: false },
      elements: { point: { radius: 0, hoverRadius: 4 }, line: { borderWidth: 2, tension: 0 } },
      scales: {
        x: { type: 'time', min: xmin, max: xmax, grid: { color: C.grid }, ticks: { color: C.tick, maxRotation: 0, autoSkipPadding: 16 }, time: { tooltipFormat: 'yyyy/MM/dd' } },
        y: { grid: { color: C.grid }, ticks: { color: C.tick, callback: (v) => fmt(v, Math.min(ind.decimals, 2)) } },
      },
      plugins: {
        legend: { display: false },
        decimation: { enabled: true, algorithm: 'lttb', samples: 700 },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}：${fmt(c.parsed.y, ind.decimals)}${unit}` } },
      },
    },
  });
}

async function drawCharts() {
  const i = IND.indicators.find((x) => x.id === modalState.id);
  const s = await getSeries(i.id);
  charts.forEach((c) => c.destroy()); charts = [];
  const lastX = Date.parse(s.dates[s.dates.length - 1] + 'T00:00:00Z');
  const from = modalState.years ? lastX - modalState.years * 365.25 * 864e5 : null;
  const main = toPoints(s, 'v', from);
  const xmin = from || (main.length ? main[0].x : undefined);
  const ds = [{ label: '実績', data: main, borderColor: C.blue, backgroundColor: C.blue }];
  if (s.ma) ds.push({ label: i.freq === 'M' ? '3か月平均' : '4週平均', data: toPoints(s, 'ma', from), borderColor: C.ma, borderDash: [5, 4], borderWidth: 1.5 });
  charts.push(lineChart($('m-chart'), ds, i, xmin, lastX));

  const box = $('m-cmpbox');
  if (modalState.cmp) {
    const o = IND.indicators.find((x) => x.id === modalState.cmp);
    const s2 = await getSeries(o.id);
    $('m-cmpname').textContent = `${o.name}（${o.unit || '指数'}）`;
    box.hidden = false;
    charts.push(lineChart($('m-chart2'), [{ label: o.name, data: toPoints(s2, 'v', from), borderColor: C.orange, backgroundColor: C.orange }], o, xmin, lastX));
  } else {
    box.hidden = true;
  }
}

function sourceLinks(i) {
  const link = (id) => `<a href="https://fred.stlouisfed.org/series/${esc(id)}" target="_blank" rel="noopener">FRED ${esc(id)}</a>`;
  if (i.derived) {
    const byId = (x) => (IND.indicators.find((o) => o.id === x) || {}).fred;
    return `${link(byId(i.derived.a))} − ${link(byId(i.derived.b))}（このツールで計算）`;
  }
  return link(i.fred);
}

async function downloadCsv(i) {
  const s = await getSeries(i.id);
  const rows = ['date,raw,' + (TRANSFORM_LABEL[i.transform] || 'value')];
  s.dates.forEach((d, k) => rows.push(`${d},${s.raw[k] ?? ''},${s.v[k] ?? ''}`));
  const blob = new Blob(['﻿' + rows.join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = `${i.derived ? i.id : i.fred}.csv`; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- 過去の判定 ----------
function renderHistory() {
  $('histnote').textContent = HIST.note;
  const qs = IND.questions;
  const months = HIST.months;
  const N = months.length;
  const labelW = 96, rowH = 22, gap = 4;
  const W = Math.max(720, $('history').clientWidth - 20);
  const cw = (W - labelW) / N;
  const rows = [{ name: '景気後退(NBER)', s: HIST.usrec, rec: true }, ...qs.map((q) => ({ name: q.name, s: HIST.states[q.id] }))];
  const H = rows.length * (rowH + gap) + 24;
  let svg = `<svg id="histsvg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="過去の判定の推移">`;
  rows.forEach((r, ri) => {
    const y = ri * (rowH + gap);
    svg += `<text x="0" y="${y + 15}" font-size="12" fill="#111418">${esc(r.name)}</text>`;
    // 同じ色が続く区間をまとめて描く
    let k = 0;
    while (k < N) {
      const ch = r.s[k]; let e = k;
      while (e + 1 < N && r.s[e + 1] === ch) e++;
      const color = r.rec ? (ch === '1' ? '#3b4148' : '#f3f4f6') : ST[CODE[ch]].color;
      svg += `<rect x="${(labelW + k * cw).toFixed(2)}" y="${y}" width="${((e - k + 1) * cw).toFixed(2)}" height="${rowH}" fill="${color}"/>`;
      k = e + 1;
    }
  });
  const axisY = rows.length * (rowH + gap) + 12;
  months.forEach((m, k) => {
    const [yy, mm] = m.split('-').map(Number);
    if (mm === 1 && yy % 4 === 0) {
      const x = labelW + k * cw;
      svg += `<line x1="${x}" x2="${x}" y1="0" y2="${axisY - 10}" stroke="#ffffff" stroke-width="1" opacity=".7"/><text x="${x}" y="${axisY + 6}" font-size="11" fill="#3b4148" text-anchor="middle">${yy}</text>`;
    }
  });
  svg += `<rect id="histhit" x="${labelW}" y="0" width="${W - labelW}" height="${axisY}" fill="transparent"/></svg>`;
  $('history').innerHTML = `<div class="hist">${svg}</div>
    <div class="legend">${['good', 'caution', 'warn', 'na'].map((k) => `<span><i class="sw" style="background:${ST[k].color}"></i>${ST[k].icon} ${ST[k].label}</span>`).join('')}<span><i class="sw" style="background:#3b4148"></i>景気後退期</span></div>`;

  const hit = $('histhit'), tip = $('tip');
  hit.addEventListener('mousemove', (e) => {
    const r = hit.getBoundingClientRect();
    const k = Math.min(N - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * N)));
    const lines = qs.map((q) => `${q.name}：${ST[CODE[HIST.states[q.id][k]]].label}`);
    tip.innerHTML = `<b>${months[k]}</b>${HIST.usrec[k] === '1' ? '（景気後退期）' : ''}<br>${lines.join('<br>')}`;
    tip.hidden = false; tip.style.left = Math.min(e.clientX + 12, window.innerWidth - 270) + 'px'; tip.style.top = (e.clientY + 12) + 'px';
  });
  hit.addEventListener('mouseleave', () => { tip.hidden = true; });

  // 景気後退の前に警戒が出ていたか
  const head = `<tr><th>景気後退の開始</th>${qs.map((q) => `<th>${esc(q.name)}</th>`).join('')}</tr>`;
  const body = HIST.leads.map((l) => `<tr><td>${esc(l.start)}</td>${qs.map((q) => {
    const x = l.q[q.id];
    if (x.months_before === null) return '<td>警戒なし</td>';
    return `<td>${x.months_before === 0 ? '開始月に警戒' : `${x.months_before}か月前から警戒`}</td>`;
  }).join('')}</tr>`).join('');
  const prec = `<tr><td><b>当たり率</b></td>${qs.map((q) => {
    const p = HIST.precision[q.id];
    return `<td>${p.rate === null ? '—' : `${p.rate}%<br><small>警戒${p.warn_months}か月中</small>`}</td>`;
  }).join('')}</tr>`;
  $('leads').innerHTML = `<h3>景気後退の前に「警戒」が出ていたか</h3>
    <div class="tscroll"><table class="t">${head}${body}${prec}</table></div>
    <p class="note">「〇か月前から警戒」＝景気後退が始まる前の24か月以内で、最初に警戒が出た時期。当たり率＝警戒が出た月のうち、24か月以内に景気後退が始まった割合（直近24か月は結果が未確定のため除外）。インフレと金融環境は景気後退を当てるための問いではないので、当たり率は参考です。</p>`;
}

// ---------- AIに渡す ----------
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch { /* 下の方法で再試行 */ }
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  ta.remove();
  return ok;
}
document.querySelectorAll('[data-ai]').forEach((b) => b.addEventListener('click', async () => {
  const msg = $('aimsg');
  msg.textContent = '準備中…';
  try {
    const r = await fetch('data/' + b.dataset.ai, { cache: 'no-cache' });
    if (!r.ok) throw new Error(r.status);
    const text = await r.text();
    const ok = await copyText(text);
    msg.textContent = ok ? `コピーしました（${text.length.toLocaleString()}文字）。AIのチャットに貼り付けてください。` : 'コピーできませんでした。右のリンクからファイルを保存してください。';
  } catch (e) {
    msg.textContent = '読み込めませんでした（' + e.message + '）。';
  }
}));

// ---------- イベント ----------
$('m-close').addEventListener('click', closeModal);
$('modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('modal').hidden) closeModal(); });
$('search').addEventListener('input', (e) => { search = e.target.value.trim(); renderGroups(); });
let rt; window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(() => HIST && renderHistory(), 200); });
load();
