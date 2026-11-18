/* LAGRANGE Rx demo UI. Static, no build step. Every number shown comes from an API response;
   when the API has nothing, the screen says "нет данных" and why. */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const S = { batchId: null, graphId: null, batch: null, queue: [], twin: null, plan: null, samples: null, graphs: [] };
const SCREENS = ['docs', 'understood', 'issues', 'twin', 'plan', 'shock', 'evidence', 'progress'];

/* ---------- helpers ---------- */
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const nf = (d) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: d });
const NA = (why = 'нет данных') => `<span class="na">${esc(why)}</span>`;
const int = (v) => (isNum(v) ? nf(0).format(v) : NA());
const pct = (v, d = 2) => (isNum(v) ? nf(d).format(v * 100) + ' %' : NA());
const pp = (v) => (isNum(v) ? nf(2).format(v) + ' п.п.' : NA());
function uzs(v) {
  if (!isNum(v)) return NA();
  const a = Math.abs(v), sign = v < 0 ? '−' : '';
  if (a >= 1e9) return `${sign}${nf(2).format(a / 1e9)} млрд`;
  if (a >= 1e6) return `${sign}${nf(1).format(a / 1e6)} млн`;
  return `${sign}${nf(0).format(a)}`;
}
const badge = (text, kind = '') => `<span class="badge ${kind}">${esc(text)}</span>`;
const ASSUMPTION = /assumption|synthetic|допущен|синтет|not a forecast|engine default|engineconfig|not calibrated|unconfirmed/i;
const flag = (text) => (ASSUMPTION.test(String(text)) ? ' ' + badge('допущение / синтетика', 'syn') : '');
const notice = (html, kind = '') => `<div class="notice ${kind}">${html}</div>`;

function toast(msg, bad = false) {
  const t = $('#toast');
  t.textContent = msg; t.className = bad ? 'bad' : ''; t.hidden = false;
  clearTimeout(toast.id); toast.id = setTimeout(() => { t.hidden = true; }, 4500);
}

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', headers: {} };
  if (opts.json !== undefined) { init.method = opts.method || 'POST'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.json); }
  if (opts.form) { init.method = 'POST'; init.body = opts.form; }
  const r = await fetch(path, init);
  let body = null;
  try { body = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) {
    const d = body && body.detail;
    const msg = typeof d === 'string' ? d : d && (d.reason || d.error) ? `${d.error || ''}${d.reason ? ': ' + d.reason : ''}` : `HTTP ${r.status}`;
    throw Object.assign(new Error(msg), { status: r.status, detail: d });
  }
  return body;
}

function busy(btn, on) { if (btn) btn.disabled = on; }
function save() { try { localStorage.setItem('lagrange-ui', JSON.stringify({ batchId: S.batchId, graphId: S.graphId })); } catch { /* storage may be blocked */ } }
function restore() { try { Object.assign(S, JSON.parse(localStorage.getItem('lagrange-ui') || '{}')); } catch { /* ignore */ } }

function ctx() {
  const parts = [];
  if (S.batchId) parts.push(`пакет <b class="mono">${esc(S.batchId.slice(0, 8))}</b>`);
  if (S.graphId) parts.push(`граф <b class="mono">${esc(S.graphId.slice(0, 12))}</b>`);
  const sid = S.plan && S.plan.snapshot && S.plan.snapshot.snapshot_id;
  if (sid) parts.push(`снимок <b class="mono">${esc(sid)}</b>`);
  $('#ctx').innerHTML = parts.join(' · ') || 'граф не выбран';
  $$('#nav a').forEach((a) => a.classList.toggle('done',
    (a.dataset.screen === 'docs' && !!S.graphId) || (a.dataset.screen === 'twin' && !!S.twin) || (a.dataset.screen === 'plan' && !!S.plan)));
}

/* ---------- navigation ---------- */
function show(name) {
  if (!SCREENS.includes(name)) name = 'docs';
  SCREENS.forEach((s) => { $('#' + s).hidden = s !== name; });
  $$('#nav a').forEach((a) => (a.dataset.screen === name ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
  if (name === 'evidence') loadEvidence();
  if (name === 'progress') loadProgress();
  if (name === 'shock') fillRoutes();
}
window.addEventListener('hashchange', () => show(location.hash.slice(1)));

/* ---------- 1. documents ---------- */
async function loadSamples() {
  const box = $('#samples');
  try {
    S.samples = await api('/ingest/samples');
  } catch (e) { box.innerHTML = NA('список образцов недоступен: ' + e.message); return; }
  const list = S.samples.samples;
  if (!list.length) {
    box.innerHTML = NA(S.samples.configured
      ? 'Папка образцов задана, но документов в ней нет.'
      : 'Образцов нет: укажите в LAGRANGE_SAMPLES_DIR папку с документами (по подпапке на уровень L0–L4).');
    return;
  }
  box.innerHTML = '<div class="row" style="margin-top:0">' + list.map((s) =>
    `<button class="small" data-sample="${esc(s.name)}" title="${int(s.files)} файлов, ${int(s.bytes / 1024)} КБ">${esc(s.name)} <span class="muted">· ${int(s.files)}</span></button>`).join('') + '</div>';
  $$('[data-sample]', box).forEach((b) => b.addEventListener('click', () => uploadSample(b)));
}

async function loadGraphs() {
  const box = $('#graphs');
  try { S.graphs = (await api('/ontology/graphs')).graph_ids; } catch (e) { box.innerHTML = NA('недоступно: ' + e.message); return; }
  if (!S.graphs.length) { box.innerHTML = NA('Подтверждённых графов пока нет.'); return; }
  box.innerHTML = `<div class="row" style="margin-top:0"><select id="graph-pick">${S.graphs.map((g) => `<option>${esc(g)}</option>`).join('')}</select>
    <button class="small" id="btn-use-graph">Использовать</button></div>`;
  $('#btn-use-graph').addEventListener('click', () => {
    S.graphId = $('#graph-pick').value; S.twin = null; S.plan = null; save(); ctx(); toast('Граф выбран: ' + S.graphId); location.hash = '#twin';
  });
}

async function uploadSample(btn) {
  busy(btn, true);
  $('#upload-state').textContent = 'Загружаю образец ' + btn.dataset.sample + '…';
  try {
    const r = await fetch(`/ingest/samples/${encodeURIComponent(btn.dataset.sample)}/archive`);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const form = new FormData();
    form.append('files', await r.blob(), btn.dataset.sample + '.zip');
    await ingest(form);
  } catch (e) { toast('Ошибка: ' + e.message, true); $('#upload-state').textContent = ''; } finally { busy(btn, false); }
}

async function ingest(form) {
  $('#upload-state').textContent = 'Разбираю документы…';
  const { batch_id: id } = await api('/ingest/batches', { form });
  S.batchId = id; S.graphId = null; S.twin = null; S.plan = null; save();
  await refreshBatch();
  $('#upload-state').textContent = 'Готово.';
  ctx();
}

$('#btn-upload').addEventListener('click', async (ev) => {
  const form = new FormData();
  const picked = [...$('#in-folder').files, ...$('#in-files').files];
  if (!picked.length) { toast('Выберите папку или файлы', true); return; }
  picked.forEach((f) => form.append('files', f, f.webkitRelativePath || f.name));
  busy(ev.target, true);
  try { await ingest(form); } catch (e) { toast('Ошибка: ' + e.message, true); $('#upload-state').textContent = ''; } finally { busy(ev.target, false); }
});

async function refreshBatch() {
  if (!S.batchId) return;
  S.batch = await api(`/ingest/batches/${S.batchId}`);
  S.queue = await api(`/ingest/batches/${S.batchId}/review`);
  renderBatch(); renderUnderstood(); renderIssues(); renderQueue();
}

function docName(id) { const d = ((S.batch || {}).documents || []).find((x) => x.document_id === id); return d ? d.filename : String(id).slice(0, 8); }

function renderBatch() {
  const b = S.batch;
  $('#batch-card').hidden = !b;
  if (!b) return;
  $('#batch-id').textContent = S.batchId;
  const pending = S.queue.filter((q) => q.status === 'pending').length;
  $('#batch-summary').innerHTML = `<p>Документов: <b>${int(b.documents.length)}</b> · значений на проверку: <b>${int(b.proposed_instances.length)}</b> ·
    вопросов в очереди: <b>${int(pending)}</b> из ${int(S.queue.length)} · замечаний: <b>${int(b.issues.length)}</b> · дата расчёта: ${esc(b.as_of)}</p>`;
  $('#doc-table tbody').innerHTML = b.documents.map((d) => `<tr><td>${esc(d.filename)}${d.duplicate ? ' ' + badge('дубликат', 'warn') : ''}${flag(d.filename)}</td>
    <td>${esc(d.doc_class)}</td><td class="num">${isNum(d.class_confidence) ? pct(d.class_confidence, 0) : NA()}</td><td>${esc(d.status)}</td></tr>`).join('');
}

$('#btn-commit').addEventListener('click', async (ev) => {
  busy(ev.target, true);
  try {
    const r = await api(`/ingest/batches/${S.batchId}/commit`, { method: 'POST' });
    S.graphId = S.batchId; S.twin = null; S.plan = null; save(); ctx();
    const inc = (r.incomplete || []);
    $('#commit-state').innerHTML = `снимок <code>${esc(r.snapshot_id.slice(0, 16))}</code> · записано объектов: ${int(r.committed)}` +
      (inc.length ? ` · ${badge('неполно: ' + inc.length, 'warn')}` : '');
    if (r.committed === 0) toast('Граф пуст: подтвердите значения на экране «Проблемы» и повторите', true);
    loadGraphs();
  } catch (e) { toast('Ошибка фиксации: ' + e.message, true); } finally { busy(ev.target, false); }
});

/* ---------- 2. understood ---------- */
function renderQuote(ev) {
  if (!ev) return NA('нет цитаты');
  const q = ev.quote || ev.text_span;
  return `<div class="quote">${q ? `<mark>${esc(q)}</mark>` : NA('цитата отсутствует')}</div>
    <div class="src">${esc(docName(ev.document_id))}${ev.page ? ', стр. ' + esc(ev.page) : ''}
      ${ev.document_id ? `<button class="small ghost" data-page="${esc(ev.document_id)}:${esc(ev.page || 1)}">показать страницу</button>` : ''}</div><div class="page-slot"></div>`;
}

document.addEventListener('click', async (ev) => {
  const b = ev.target.closest('[data-page]');
  if (!b) return;
  const [doc, page] = b.dataset.page.split(':');
  const slot = b.parentElement.nextElementSibling;
  try {
    const r = await fetch(`/ingest/documents/${encodeURIComponent(doc)}/page/${encodeURIComponent(page)}.png`);
    if (!r.ok) throw new Error();
    slot.innerHTML = `<img class="page-img" alt="страница документа" src="${URL.createObjectURL(await r.blob())}">`;
  } catch { slot.innerHTML = NA('изображение страницы недоступно (документ не растровый)'); }
});

function renderUnderstood() {
  const b = S.batch, body = $('#understood-body');
  if (!b) return;
  const statuses = [...new Set(b.proposed_instances.map((i) => i.status))];
  const sel = $('#f-status'), cur = sel.value;
  sel.innerHTML = '<option value="">все</option>' + statuses.map((s) => `<option ${s === cur ? 'selected' : ''}>${esc(s)}</option>`).join('');
  const f = $('#f-field').value.trim().toLowerCase(), st = sel.value;
  const rows = b.proposed_instances.filter((i) => (!st || i.status === st) && (!f || Object.keys(i.fields).some((k) => k.toLowerCase().includes(f))));
  $('#understood-count').textContent = `показано ${Math.min(rows.length, 150)} из ${rows.length}`;
  if (!rows.length) { body.innerHTML = NA('Нет значений по этому фильтру.'); return; }
  body.innerHTML = '<div class="table-wrap"><table><thead><tr><th>Поле</th><th>Значение</th><th>Статус</th><th>Источник</th></tr></thead><tbody>' +
    rows.slice(0, 150).map((i) => Object.entries(i.fields).map(([name, v]) => {
      const shown = v.corrected_value ?? v.raw;
      const kind = i.status === 'confirmed' ? 'ok' : i.status === 'extracted_unconfirmed' ? 'warn' : '';
      return `<tr><td>${esc(name)}<div class="src">строка ${esc(i.source_row)}</div></td><td><b>${esc(shown)}</b>${v.corrected_value ? `<div class="src">в документе: ${esc(v.raw)}</div>` : ''}</td>
        <td>${badge(i.status, kind)}</td><td>${renderQuote(v.evidence || (i.evidence || [])[0])}</td></tr>`;
    }).join('')).join('') + '</tbody></table></div>';
}
$('#f-field').addEventListener('input', renderUnderstood);
$('#f-status').addEventListener('change', renderUnderstood);

/* ---------- 3. issues + queue ---------- */
function renderIssues() {
  const b = S.batch, box = $('#issues-body');
  if (!b) return;
  if (!b.issues.length) { box.innerHTML = notice('Замечаний нет.', 'ok'); return; }
  const rank = { blocking: 0, high: 0, medium: 1, warning: 1, low: 2, info: 3 };
  const rows = [...b.issues].sort((x, y) => (rank[x.severity] ?? 4) - (rank[y.severity] ?? 4));
  box.innerHTML = '<div class="table-wrap"><table><thead><tr><th>Серьёзность</th><th>Тип</th><th>Детали</th><th>Документы</th></tr></thead><tbody>' +
    rows.map((i) => `<tr><td>${badge(i.severity, ['blocking', 'high'].includes(i.severity) ? 'bad' : ['medium', 'warning'].includes(i.severity) ? 'warn' : '')}</td>
      <td>${esc(i.issue_type)}</td><td>${esc(i.detail || '')}${(i.evidence || []).length ? renderQuote(i.evidence[0]) : ''}</td>
      <td>${(i.affected || []).map((a) => esc(docName(a))).join(', ')}</td></tr>`).join('') + '</tbody></table></div>';
}

function renderQueue() {
  const box = $('#queue-body');
  if (!S.batch) return;
  const pending = S.queue.filter((q) => q.status === 'pending');
  $('#btn-confirm-all').hidden = !pending.length;
  if (!S.queue.length) { box.innerHTML = NA('Очередь пуста: человеку нечего подтверждать.'); return; }
  box.innerHTML = `<p class="muted">Ожидают ответа: ${int(pending.length)} из ${int(S.queue.length)}. Сначала вопросы с наибольшим влиянием на деньги.</p>` +
    S.queue.slice(0, 60).map((q) => {
      const items = q.items || [];
      const money = isNum(q.money_impact_uzs) ? `влияние ${uzs(q.money_impact_uzs)} UZS` : 'влияние на деньги не оценено';
      return `<div class="qcard" data-q="${esc(q.question_id)}"><div class="head"><b>${esc(q.field)}</b>
        ${badge(q.status, q.status === 'pending' ? 'warn' : 'ok')} ${q.affects_money ? badge('влияет на деньги', 'bad') : ''}<span class="muted">${money}</span></div>
        ${items.slice(0, 5).map((it) => `<div class="qitem" data-i="${esc(it.instance_id)}"><b>${esc(it.value.corrected_value ?? it.value.raw)}</b>
          ${renderQuote(it.value.evidence)}
          ${q.status === 'pending' ? `<div class="ctl"><input placeholder="правильное значение" aria-label="правильное значение"><button class="small" data-act="correct">Исправить</button></div>` : ''}</div>`).join('')}
        ${items.length > 5 ? `<p class="muted">… и ещё ${items.length - 5} значений того же поля (действия ниже применяются ко всем).</p>` : ''}
        ${q.status === 'pending' ? `<div class="row"><button class="small primary" data-act="confirm">Подтвердить</button>
          <button class="small" data-act="irrelevant">Не относится</button><button class="small" data-act="need_document">Нужен документ</button></div>`
          : `<div class="src">ответ: ${esc(q.action || '')}</div>`}</div>`;
    }).join('') + (S.queue.length > 60 ? `<p class="muted">Показаны первые 60 из ${int(S.queue.length)}.</p>` : '');
}

async function answer(qid, body) {
  await api(`/ingest/review/${qid}`, { json: body });
}
$('#queue-body').addEventListener('click', async (ev) => {
  const btn = ev.target.closest('[data-act]');
  if (!btn) return;
  const card = btn.closest('.qcard'), qid = card.dataset.q;
  busy(btn, true);
  try {
    if (btn.dataset.act === 'correct') {
      const item = btn.closest('.qitem'), value = $('input', item).value.trim();
      if (!value) { toast('Введите правильное значение', true); return; }
      await answer(qid, { action: 'correct', value, instance_id: item.dataset.i });
    } else await answer(qid, { action: btn.dataset.act });
    await refreshBatch();
  } catch (e) { toast('Ответ не принят: ' + e.message, true); } finally { busy(btn, false); }
});
$('#btn-confirm-all').addEventListener('click', async (ev) => {
  const pending = S.queue.filter((q) => q.status === 'pending');
  if (!confirm(`Подтвердить ${pending.length} вопросов без индивидуальной проверки?`)) return;
  busy(ev.target, true);
  let failed = 0;
  for (const q of pending) { try { await answer(q.question_id, { action: 'confirm' }); } catch { failed += 1; } }
  await refreshBatch(); busy(ev.target, false);
  toast(failed ? `Не принято ответов: ${failed}` : 'Все вопросы подтверждены', !!failed);
});

/* ---------- graph requirement ---------- */
function needGraph(box) {
  if (S.graphId) return false;
  box.innerHTML = notice('Граф не выбран. Зафиксируйте подтверждённые данные на экране «Документы клиента» или выберите готовый граф.', 'warn');
  return true;
}
function errBox(e) {
  const d = e.detail;
  if (d && d.error === 'twin_not_calibrated') return notice('Двойник не откалиброван — оптимизация не запускалась. ' + calibrationHtml(d.calibration), 'bad');
  if (d && d.error === 'graph_not_usable_by_engine') return notice(`<b>Граф нельзя использовать в расчёте.</b> ${esc(d.reason)}`, 'bad');
  return notice(`<b>Ошибка ${esc(e.status || '')}.</b> ${esc(e.message)}`, 'bad');
}

/* ---------- 4. twin ---------- */
function calibrationHtml(c) {
  if (!c) return NA();
  const miss = (c.missing_data || []), iss = (c.issues || []);
  return `<div class="kpis">
      <div class="kpi"><div class="l">Маржа двойника</div><div class="v">${pct(c.twin_margin)}</div></div>
      <div class="kpi"><div class="l">Фактическая маржа (по документам)</div><div class="v">${pct(c.actual_margin)}</div></div>
      <div class="kpi"><div class="l">Расхождение</div><div class="v">${pp(c.gap_pp)}</div><div class="s">${c.calibrated ? 'в пределах допуска' : 'вне допуска'}</div></div>
      <div class="kpi"><div class="l">Статус</div><div class="v">${c.calibrated ? badge('откалиброван', 'ok') : badge('не откалиброван', 'bad')}</div></div></div>
    ${isNum(c.twin_margin) && isNum(c.actual_margin) ? marginBars(c) : ''}
    <h3>Чего не хватает данным</h3>${miss.length ? `<ul class="plain">${miss.map((m) => `<li>${esc(m)}${flag(m)}</li>`).join('')}</ul>` : NA('Недостающих данных API не назвал.')}
    ${iss.length ? `<h3>Замечания калибровки</h3><ul class="plain">${iss.map((m) => `<li>${esc(typeof m === 'string' ? m : JSON.stringify(m))}</li>`).join('')}</ul>` : ''}`;
}
function marginBars(c) {
  const max = Math.max(Math.abs(c.twin_margin), Math.abs(c.actual_margin)) || 1;
  const row = (l, v, cls) => `<div class="bar"><span>${l}</span><span class="track"><span class="fill ${cls}${v < 0 ? ' neg' : ''}" style="width:${(Math.abs(v) / max) * 100}%;left:0"></span></span><span class="val">${pct(v)}</span></div>`;
  return `<div class="card"><div class="bars">${row('Двойник', c.twin_margin, '')}${row('Факт по документам', c.actual_margin, 'alt')}</div></div>`;
}
async function runTwin() {
  const box = $('#twin-body');
  if (needGraph(box)) return;
  busy($('#btn-twin'), true); $('#twin-state').textContent = 'Считаю…';
  try {
    S.twin = await api('/engine/twin', { json: { graph_id: S.graphId } });
    const ue = S.twin.unit_economics || {}, routes = Object.values(ue.routes || {});
    box.innerHTML = calibrationHtml(S.twin.calibration) +
      `<div class="card"><h2>Себестоимость по маршрутам, UZS за упаковку</h2>${routes.length ? `<div class="table-wrap"><table><thead><tr><th>Маршрут</th><th class="num">Законная база</th><th class="num">Полная себестоимость</th><th class="num">Хедж на упаковку</th></tr></thead><tbody>
      ${routes.map((r) => `<tr><td>${esc(r.route_id)}${flag(r.route_id)}</td><td class="num">${int(r.legal_base)}</td><td class="num">${int(r.unit_cost)}</td><td class="num">${int(r.hedge_unit)}</td></tr>`).join('')}</tbody></table></div>` : NA('Маршрутов нет.')}
      ${ue.caps ? `<h3>Ценовые потолки, UZS за упаковку</h3><div class="kpis"><div class="kpi"><div class="l">Оптовый</div><div class="v">${int(ue.caps.wholesale)}</div></div>
        <div class="kpi"><div class="l">Розничный</div><div class="v">${int(ue.caps.retail)}</div></div>
        <div class="kpi"><div class="l">Опубликованный ЧУН</div><div class="v">${int(ue.caps.published_chun)}</div></div>
        <div class="kpi"><div class="l">Коэффициент потолка</div><div class="v">${isNum(ue.caps.factor) ? nf(3).format(ue.caps.factor) : NA()}</div></div></div>` : ''}
      ${(ue.warnings || []).length ? `<ul class="plain">${ue.warnings.map((w) => `<li>${esc(w)}${flag(w)}</li>`).join('')}</ul>` : ''}</div>`;
    $('#twin-state').textContent = '';
  } catch (e) { box.innerHTML = errBox(e); $('#twin-state').textContent = ''; } finally { busy($('#btn-twin'), false); ctx(); }
}
$('#btn-twin').addEventListener('click', runTwin);

/* ---------- 5. plan ---------- */
function waterfallHtml(w) {
  if (!w || !w.length) return NA('Каскада нет.');
  const vals = w.map((s) => s.expected_profit_uzs).filter(isNum);
  const max = Math.max(...vals.map(Math.abs), 1);
  return '<div class="bars">' + w.map((s) => `<div class="bar"><span>${esc(s.label)}</span>
    <span class="track"><span class="fill ${isNum(s.expected_profit_uzs) && s.expected_profit_uzs < 0 ? 'neg' : ''}" style="left:0;width:${isNum(s.expected_profit_uzs) ? (Math.abs(s.expected_profit_uzs) / max) * 100 : 0}%"></span></span>
    <span class="val">${uzs(s.expected_profit_uzs)} <span class="muted">(${isNum(s.delta_uzs) ? (s.delta_uzs > 0 ? '+' : '') + uzs(s.delta_uzs) : '—'})</span></span></div>`).join('') + '</div>';
}
function constraintsHtml(cs) {
  if (!cs || !cs.length) return NA('Ограничений API не вернул.');
  const binding = cs.filter((c) => c.is_binding);
  const row = (c) => `<tr><td>${c.is_binding ? badge('связывает', 'warn') : badge('не связывает')}</td><td class="mono">${esc(c.ref)}</td><td>${esc(c.kind)}</td><td>${esc(c.text)}</td></tr>`;
  return `<p class="muted">Связывающих: ${int(binding.length)} из ${int(cs.length)}.</p><div class="table-wrap"><table><thead><tr><th></th><th>rule_id</th><th>Тип</th><th>Описание</th></tr></thead><tbody>${[...binding, ...cs.filter((c) => !c.is_binding)].map(row).join('')}</tbody></table></div>`;
}
function checksHtml(r) {
  const by = {};
  (r.checks || []).forEach((c) => { const x = by[c.rule_id] || (by[c.rule_id] = { n: 0, f: 0 }); x.n += 1; if (!c.passed) x.f += 1; });
  const rows = Object.entries(by).sort();
  const state = r.legal_status || (r.legal === null ? "not_evaluated" : r.legal ? "passed" : "failed");
  const outcome = state === "not_evaluated" ? badge("не оценено: поставок нет", "warn") :
    state === "passed" ? badge("Проверки модели пройдены (не юридическое заключение)", "ok") :
    badge("Нарушены ограничения модели", "bad");
  return `<p>Проверок: <b>${int(r.checks_total)}</b>, нарушений: <b>${int(r.checks_failed)}</b> ${outcome}</p>` +
    (rows.length ? `<div class="table-wrap"><table><thead><tr><th>rule_id</th><th class="num">проверок</th><th class="num">нарушений</th></tr></thead><tbody>${rows.map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="num">${int(v.n)}</td><td class="num">${int(v.f)}</td></tr>`).join('')}</tbody></table></div>` : '');
}
function impossibleHtml(im) {
  if (!im) return '';
  const sets = (im.relax_sets || []).map((s) => '<li>' + Object.entries(s).map(([g, a]) => `${esc(g)}: ${isNum(a) ? nf(2).format(a) : '—'}`).join(', ') + '</li>').join('');
  return notice(`<b>Невозможно в таких условиях.</b> ${esc(im.text || '')}`, 'bad') +
    (sets ? `<h3>Что нужно ослабить (минимальные наборы)</h3><ul class="plain">${sets}</ul>` : '') +
    ((im.legal_notes || []).length ? `<h3>Законные ограничения (не ослабляются)</h3><ul class="plain">${im.legal_notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>` : '');
}
function planHtml(r) {
  const p = r.plan, m = r.metrics || {}, s = r.solve || {};
  const head = `<div class="kpis">
    <div class="kpi"><div class="l">Ожидаемая прибыль, UZS</div><div class="v">${uzs(m.expected_uzs)}</div></div>
    <div class="kpi"><div class="l">CVaR</div><div class="v">${uzs(m.cvar_uzs)}</div></div>
    <div class="kpi"><div class="l">Худший сценарий</div><div class="v">${uzs(m.worst_uzs)}</div></div>
    <div class="kpi"><div class="l">Вероятность убытка</div><div class="v">${pct(m.p_loss, 1)}</div></div>
    <div class="kpi"><div class="l">Решатель</div><div class="v">${esc(s.status || '—')}</div><div class="s">${s.proven_optimal ? 'оптимальность доказана' : 'не доказана'}${isNum(s.seconds) ? ', ' + nf(2).format(s.seconds) + ' с' : ''}</div></div></div>`;
  if (!p) return notice("Нет допустимого плана при заданном покрытии спроса. Отсутствие проверок не доказывает законность.", "bad") + head + impossibleHtml(r.impossible) + `<div class="card"><h2>Законность</h2>${checksHtml(r)}</div>`;
  const lines = p.lines || [];
  const policyText = r.service_policy && isNum(r.service_policy.minimum_expected_fill_rate) ?
    `<p class="policy-help">Минимальное покрытие: ${pct(r.service_policy.minimum_expected_fill_rate, 0)} (демонстрационное допущение). Запланировано: ${int(r.planned_packs)} упаковок. Эффект на реальных данных не измерен.</p>` : "";
  return policyText + head + (lines.length ? '' : notice('План не содержит ни одной поставки: при заданных данных оптимизатор не закупает ничего. Сверьте экран «Двойник» и предупреждения ниже.', 'warn')) +
    `<div class="card"><h2>План ${esc(p.plan_id)} <span class="muted">${esc(p.horizon)}</span></h2>
      ${lines.length ? `<div class="table-wrap"><table><thead><tr><th>Маршрут</th><th class="num">Упаковок</th><th class="num">Отгрузок</th><th>Месяцы</th></tr></thead><tbody>${lines.map((l) => `<tr><td>${esc(l.route_id)}</td><td class="num">${int(l.packs)}</td><td class="num">${int(l.shipments)}</td><td>${esc((l.months || []).join(', '))}</td></tr>`).join('')}</tbody></table></div>` : ''}
      <p class="muted">Доля хеджа курса: ${pct(p.fx_hedge_share, 0)} · политика цены: ${esc(p.wholesale_price_policy)} · режим риска: ${esc(p.risk_mode)}</p></div>
    <div class="card"><h2>Каскад: от текущей практики к плану, ожидаемая прибыль в UZS</h2>${waterfallHtml(r.waterfall)}</div>
    <div class="card"><h2>Связывающие ограничения</h2>${constraintsHtml(r.constraints)}</div>
    <div class="card"><h2>Законность</h2>${checksHtml(r)}</div>
    ${(r.warnings || []).length ? `<div class="card"><h2>Предупреждения и допущения</h2><ul class="plain">${r.warnings.map((w) => `<li>${esc(w)}${flag(w)}</li>`).join('')}</ul></div>` : ''}`;
}
async function runPlan() {
  const box = $('#plan-body');
  if (needGraph(box)) return;
  busy($('#btn-plan'), true); $('#plan-state').textContent = 'Оптимизирую…';
  const service = Number($('#p-service').value);
  if (!Number.isFinite(service) || service <= 0 || service > 1) {
    box.innerHTML = notice("Укажите долю обеспечения от 0 до 1.", "bad"); return;
  }
  const body = { service_level: service, graph_id: S.graphId, risk_mode: $('#p-risk').value, n: +$('#p-n').value, k: +$('#p-k').value, hedge_enabled: $('#p-hedge').checked };
  try {
    S.plan = await api('/engine/optimize', { json: body });
    box.innerHTML = planHtml(S.plan);
    $('#plan-state').textContent = S.plan.supply_status === 'ready' ? 'План пересчитан' : 'Нет допустимого плана';
    ctx();
  } catch (e) { box.innerHTML = errBox(e); $('#plan-state').textContent = 'Ошибка'; } finally { busy($('#btn-plan'), false); }
}
$('#btn-plan').addEventListener('click', runPlan);

/* ---------- 6. shock ---------- */
async function fillRoutes() {
  const sel = $('#s-route');
  if (!S.graphId || sel.options.length > 1) return;
  try {
    S.twin = S.twin || await api('/engine/twin', { json: { graph_id: S.graphId } });
    Object.keys((S.twin.unit_economics || {}).routes || {}).forEach((id) => sel.add(new Option(id, id)));
  } catch { /* the route list stays empty; the shock screen says so on use */ }
}
function shockResult(r, change) {
  const nan = !isNum(r.new_expected_uzs);
  const packs = (a, b) => { const ids = [...new Set([...Object.keys(a || {}), ...Object.keys(b || {})])];
    return ids.length ? `<div class="table-wrap"><table><thead><tr><th>Маршрут</th><th class="num">было, упаковок</th><th class="num">стало, упаковок</th></tr></thead><tbody>${ids.map((i) => `<tr><td>${esc(i)}</td><td class="num">${int((a || {})[i] ?? 0)}</td><td class="num">${int((b || {})[i] ?? 0)}</td></tr>`).join('')}</tbody></table></div>` : NA('В обоих планах нет поставок.'); };
  if (nan) return notice(`<b>Невозможно.</b> ${esc(r.text)}`, 'bad') + `<p class="muted">Статус решателя: ${esc(r.status)}. Для разбора причин задайте требования в блоке «Невозможное требование».</p>`;
  return `<div class="card"><h2>Результат: ${esc(JSON.stringify(change))}</h2><div class="kpis">
    <div class="kpi"><div class="l">Ожидаемая прибыль до, UZS</div><div class="v">${uzs(r.base_expected_uzs)}</div></div>
    <div class="kpi"><div class="l">после</div><div class="v">${uzs(r.new_expected_uzs)}</div></div>
    <div class="kpi"><div class="l">Изменение</div><div class="v">${uzs(r.delta_expected_uzs)}</div></div>
    <div class="kpi"><div class="l">Решатель</div><div class="v">${esc(r.status)}</div></div></div>
    <p>${esc(r.text)}</p>${packs(r.base_packs, r.new_packs)}</div>`;
}
$$('[data-shock]').forEach((b) => b.addEventListener('click', async () => {
  const box = $('#shock-body');
  if (needGraph(box)) return;
  if (!S.plan) { box.innerHTML = notice('Сначала постройте план на экране «План»: шок пересчитывается от него.', 'warn'); return; }
  busy(b, true);
  try {
    if (b.dataset.shock === 'impossible') {
      const body = { graph_id: S.graphId, n: +$('#p-n').value, k: +$('#p-k').value, risk_mode: 'expected' };
      if ($('#s-service').value) body.service_level = +$('#s-service').value;
      if ($('#s-budget').value) body.budget_uzs = +$('#s-budget').value;
      if (!$('#s-service').value) body.service_level = Number($('#p-service').value) || 0.90;
      const r = await api('/engine/optimize', { json: body });
      box.innerHTML = r.plan ? notice('При таких требованиях план существует.', 'ok') + planHtml(r) : planHtml(r);
      return;
    }
    const change = { fx: () => ({ fx_shift: +$('#s-fx').value / 100 }), ceil: () => ({ ceiling_cut: +$('#s-ceil').value / 100 }),
      route: () => ({ route_unavailable: $('#s-route').value }) }[b.dataset.shock]();
    if (change.route_unavailable === '') { box.innerHTML = notice('Выберите маршрут (список строится по двойнику выбранного графа).', 'warn'); return; }
    const r = await api('/engine/what-if', { json: { plan_id: S.plan.id, change } });
    box.innerHTML = shockResult(r, change);
  } catch (e) { box.innerHTML = e.status === 404 ? notice('Для этого шока нужен выполнимый план: ' + esc(e.message), 'warn') : errBox(e); } finally { busy(b, false); }
}));

let LIVE_REVISION = null;
/* ---------- 7. evidence ---------- */
function leaves(obj, prefix = '', out = []) {
  if (Array.isArray(obj)) {
    if (obj.every((x) => x === null || typeof x !== 'object')) out.push([prefix, obj.length > 12 ? `${obj.length} элементов` : obj.join(', ')]);
    else obj.slice(0, 20).forEach((x, i) => leaves(x, `${prefix}[${i}]`, out));
  } else if (obj && typeof obj === 'object') Object.entries(obj).forEach(([k, v]) => leaves(v, prefix ? `${prefix}.${k}` : k, out));
  else out.push([prefix, obj]);
  return out;
}
const fmtLeaf = (v) => (v === null ? NA('нет значения') : typeof v === 'boolean' ? badge(v ? 'да' : 'нет', v ? 'ok' : 'bad') : isNum(v) ? nf(6).format(v) : esc(v));
function kvTable(obj) {
  return `<div class="table-wrap"><table><tbody>${leaves(obj).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td>${fmtLeaf(v)}${typeof v === 'string' ? flag(v) : ''}</td></tr>`).join('')}</tbody></table></div>`;
}
function reportHtml(rep) {
  const reportRevision = rep.data && rep.data.code_commit;
  const freshness = !reportRevision ? badge("нет версии кода", "warn") :
    !LIVE_REVISION ? badge("версия сборки не подтверждена", "warn") :
    reportRevision === LIVE_REVISION ? badge("отчёт соответствует сборке", "ok") : badge("устаревший отчёт", "bad");
  const head = `<div class="row between"><h2>${esc(rep.name)}</h2><span>${rep.snapshot_id ? `snapshot_id <code>${esc(rep.snapshot_id)}</code>` : badge('snapshot_id не указан', 'warn')}</span></div>`;
  if (rep.skipped) return `<div class="card">${head}${NA('Не показан: ' + rep.skipped)}</div>`;
  const d = rep.data || {};
  let tiles = '';
  if (d.metrics && typeof d.metrics === 'object' && !Array.isArray(d.metrics)) {
    tiles = '<div class="grid2">' + Object.entries(d.metrics).map(([k, v]) => `<div class="card" style="margin:0"><h2>${esc(k)} ${v && typeof v === 'object' && 'pass' in v ? (v.pass ? badge('пройдено', 'ok') : badge('не пройдено', 'bad')) : ''}</h2>${kvTable(v && typeof v === 'object' ? v : { значение: v })}</div>`).join('') + '</div>';
  }
  const rest = { ...d }; if (tiles) delete rest.metrics;
  return `<div class="card">${head}<p>${freshness}</p>${d.truth ? `<p class="muted">Истина: ${esc(d.truth)}</p>` : ''}${typeof d.all_pass === 'boolean' ? `<p>Все пороги: ${d.all_pass ? badge('пройдены', 'ok') : badge('не пройдены', 'bad')}</p>` : ''}${tiles}
    <details><summary>${tiles ? 'Остальные поля отчёта' : 'Содержимое отчёта'}</summary>${kvTable(rest)}</details></div>`;
}
/* ---------- 8. judge-facing results summary ---------- */
function proofTile(value, heading, description, note, kind = '') {
  return `<article class="proof-tile">
    <div class="proof-tile-value">${esc(value)}</div>
    <h2>${esc(heading)}</h2>
    <p>${esc(description)}</p>
    <div class="proof-tile-note ${esc(kind)}">${esc(note)}</div>
  </article>`;
}
function proofRate(n, d) {
  return isNum(n) && isNum(d) && d > 0 ? `${int(n)} / ${int(d)}` : '—';
}
async function loadProgress() {
  const box = $('#progress-summary');
  if (!box) return;
  try {
    const [evidence, health, available] = await Promise.all([
      api('/evidence/reports'), api('/health'), api('/ontology/graphs')
    ]);
    const reports = evidence.reports || [];
    const engine = reports.find((r) => r.name === 'engine_report' && r.data && r.data.metrics);
    const r = engine && engine.data;
    const m = r && r.metrics || {};
    const opt = m.optimality || {};
    const legal = m.legality || {};
    const graphIds = available.graph_ids || [];
    const archived = r && r.code_commit && health.revision && r.code_commit !== health.revision;
    const rev = r && r.code_commit ? r.code_commit.slice(0, 10) : 'без версии';
    const label = archived ? 'Архивный отчёт' : 'Отчёт: ' + rev;
    const comparisons = proofRate(opt.matches, opt.problems);
    const legality = isNum(legal.scenarios) && isNum(legal.violations)
      ? `${int(legal.violations)} / ${int(legal.scenarios)}` : '—';
    const calibration = S.twin && S.twin.calibration || null;
    const gap = calibration && isNum(calibration.gap_pp)
      ? pp(calibration.gap_pp) : '—';
    const calLabel = calibration ? (calibration.calibrated ? 'В пределах допуска модели' : 'Допуск не выполнен') : 'Покажем после расчёта двойника';
    const dataFlag = graphIds.some((id) => id.startsWith('demo'));
    const availability = dataFlag
      ? notice(`<strong>Демонстрационная компания готова.</strong> Синтетический пример доступен на сервере. <a href="#docs" class="proof-inline-link">Выбрать компанию</a>`, 'ok')
      : notice(`<strong>Нет подготовленной компании на этом сервере.</strong> Для живого расчёта нужен подтверждённый граф L0; результаты тестов ниже — исторические.`, 'warn');
    const top = `<div class="proof-section-header"><div><h2>Измеренные результаты</h2>
        <p>Не рекламные обещания, а показатели из сохранённого отчёта и текущего сеанса.</p></div>
        <span class="proof-source-tag">${esc(label)}</span></div>`;
    const cards = `<div class="proof-metrics">
      ${proofTile(comparisons, 'Проверка оптимизатора',
        'Решения сопоставлены с полным перебором на небольших тестовых задачах.',
        opt.pass ? 'Пройдено в записанном отчёте' : 'Историческая проверка не пройдена', opt.pass ? 'ok' : 'warn')}
      ${proofTile(legality, 'Нарушений ограничений модели',
        'Независимая проверка решений на синтетических сценариях.',
        legal.pass ? 'Пройдено в записанном отчёте' : 'Историческая проверка не пройдена', legal.pass ? 'ok' : 'warn')}
      ${proofTile(gap, 'Точность цифрового двойника',
        'Расхождение между моделью и подтверждённой финансовой историей.',
        calLabel, calibration ? (calibration.calibrated ? 'ok' : 'warn') : '')}
      ${proofTile('L0', 'Реальный проход через интерфейс',
        'Синтетическая компания → двойник → план → рост курса на 15%.',
        'Автоматизированный тест Chromium · 18.11.2026', 'ok')}
    </div>`;
    const caution = r && r.all_pass === false
      ? notice('<strong>Важно:</strong> этот архивный отчёт НЕ прошёл все экономические пороги. Не трактуйте показатели выше как доказанную прибыль компании.', 'warn')
      : notice('Коммерческая выгода на реальных данных ещё не подтверждена. Проверки правил модели не заменяют юридическую экспертизу.', 'warn');
    const sources = `<div class="proof-sources">
      <span>Источник: ${r ? `engine_report.json · ${esc(rev)}` : 'сохранённый отчёт недоступен'}</span>
      <span>${health.revision ? 'Текущая сборка: ' + esc(health.revision.slice(0, 10)) : 'Версия сервера не подтверждена'}</span>
      <a href="https://github.com/itsamirkhon/lagrange-main/actions/runs/44281067006" target="_blank" rel="noopener noreferrer">Проверка реального браузера</a>
    </div>`;
    box.innerHTML = availability + top + cards + caution + sources;
  } catch (error) {
    box.innerHTML = notice('Не удалось получить проверенные показатели из API: ' + esc(error.message), 'bad');
  }
}

async function loadEvidence() {
  const box = $('#evidence-body');
  $('#evidence-session').innerHTML = S.plan
    ? `<div class="card"><h2>Текущий расчёт: проверка законности</h2>${checksHtml(S.plan)}<p class="muted">Снимок <code>${esc((S.plan.snapshot || {}).snapshot_id || '—')}</code>, оптимальность: ${(S.plan.solve || {}).proven_optimal ? badge('доказана решателем', 'ok') : badge('не доказана', 'warn')}</p></div>`
    : notice('Законность текущего расчёта появится здесь после построения плана.');
  try {
    const [r, health] = await Promise.all([api('/evidence/reports'), api('/health')]);
    LIVE_REVISION = health.revision || null;
    box.innerHTML = r.reports.length ? r.reports.map(reportHtml).join('') : NA(r.directory_exists ? 'В папке отчётов нет JSON-файлов.' : 'Папка отчётов не найдена (LAGRANGE_EVAL_DIR).');
  } catch (e) { box.innerHTML = errBox(e); }
}

/* ---------- accessible presentation mode ---------- */
const presentToggle = $('#btn-present');
if (presentToggle) {
  presentToggle.addEventListener('click', () => {
    const active = document.documentElement.classList.toggle('presentation-mode');
    presentToggle.setAttribute('aria-pressed', String(active));
    presentToggle.textContent = active ? 'Закрыть режим презентации' : 'Режим презентации';
  });
}

/* ---------- boot ---------- */
restore();
ctx();
loadSamples();
loadGraphs();
if (S.batchId) refreshBatch().catch(() => { S.batchId = null; S.batch = null; save(); ctx(); });
show(location.hash.slice(1) || 'docs');
