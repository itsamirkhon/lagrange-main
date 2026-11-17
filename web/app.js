/* LAGRANGE Rx: honest, completely local UI over the same solver API as the CLI. */
'use strict';

const $ = (selector) => document.querySelector(selector);
const fmt0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const fmt1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
const state = { originalCase: null, lastCase: null, lastResponse: null, loading: false };
const inputs = ['risk', 'fx', 'freight', 'ceiling', 'budget', 'discount'];
const REGULAR_LABELS = { risk: '%', fx: '%', freight: '%', ceiling: '%', budget: 'B UZS', discount: '%' };

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}
function compactMoney(value, suffix = ' UZS') {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  const sign = n < 0 ? '−' : '';
  const abs = Math.abs(n);
  return `${sign}${abs >= 1e9 ? `${(abs / 1e9).toFixed(2)}B` : abs >= 1e6 ? `${(abs / 1e6).toFixed(1)}M` : fmt0.format(abs)}${suffix}`;
}
function updateControlLabels() {
  inputs.forEach((id) => {
    const el = $('#' + id);
    const out = $('#' + id + '-value');
    if (!out) return;
    const value = Number(el.value);
    if (id === 'budget') out.textContent = `${value.toFixed(2)}B UZS`;
    else if (id === 'ceiling') out.textContent = `${value}%`;
    else if (id === 'risk' || id === 'discount') out.textContent = `${value}%`;
    else out.textContent = `+${value}%`;
  });
}
function cloneCase() {
  if (!state.originalCase) throw new Error('Demo dataset not loaded');
  const c = JSON.parse(JSON.stringify(state.originalCase));
  c.working_capital_budget_uzs = Math.round(Number($('#budget').value) * 1e9);
  c.scenarios.find((s) => s.id === 'fx_rise').fx_change_pct = Number($('#fx').value);
  c.scenarios.find((s) => s.id === 'freight_rise').freight_change_pct = Number($('#freight').value);
  c.scenarios.forEach((s) => { s.wholesale_ceiling_change_pct = Number($('#ceiling').value); });
  const disabled = $('#supplier').value;
  if (disabled) c.options.find((o) => o.id === disabled).eligible = false;
  return c;
}
function setStatus(text, kind) {
  const el = $('#status');
  el.textContent = text.toUpperCase();
  el.className = 'status' + (kind ? ` status-${kind}` : '');
}
function setBusy(busy) {
  state.loading = busy;
  $('#run').disabled = busy;
  $('#run-label').textContent = busy ? 'Calculating...' : 'Run optimization';
}
function metricValue(id, text, isNegative = false) {
  const node = $('#' + id);
  node.textContent = text;
  node.style.color = isNegative ? 'var(--danger)' : '';
}
function renderUnsolvable(response) {
  metricValue('expected', 'Not feasible');
  metricValue('improvement', '—');
  metricValue('worst', '—');
  $('#margin').textContent = 'No legal/feasible allocation under current inputs';
  $('#improvement-sub').textContent = 'Baseline comparison unavailable';
  $('#worst-sub').textContent = 'No candidate was accepted';
  $('#total-packs').textContent = 'No feasible allocation';
  $('#allocations').innerHTML = '<div class="placeholder">The constraints conflict. Change a shock, route, or capital limit and re-run.</div>';
  $('#scenario-chart').innerHTML = '<div class="placeholder">No feasible plan to evaluate.</div>';
  $('#diagnostics').innerHTML = response.diagnostics.map((d) => `<div class="diagnostic"><span class="diag-icon">!</span><span>${escapeHtml(d)}</span></div>`).join('');
}
function renderAllocations(solution, demand) {
  $('#total-packs').textContent = `${fmt0.format(demand)} / ${fmt0.format(demand)} packs`;
  const colorPalette = ['#6cebd2', '#42b1bd', '#8ed9b4', '#61a5d0', '#9ce3e1'];
  $('#allocations').innerHTML = solution.allocations
    .slice().sort((a, b) => b.packs - a.packs)
    .map((a, i) => {
      const pct = 100 * a.packs / demand;
      return `<div class="allocation-row" title="${escapeHtml(a.option_name)}">
        <div><div class="route-name">${escapeHtml(a.option_name)}</div><div class="route-description">${fmt0.format(a.shipments)} shipments</div></div>
        <div class="alloc-track"><div class="alloc-fill" style="width:${pct.toFixed(2)}%;background:${colorPalette[i % colorPalette.length]}"></div></div>
        <div class="route-val">${fmt1.format(pct)}%</div></div>`;
    }).join('');
}
function scenarioCell(label, value, maxAbs, cssClass) {
  const pct = Math.max(0, Math.min(49.7, Math.abs(value) / maxAbs * 47));
  const place = value < 0 ? `left:${(50 - pct).toFixed(2)}%` : 'left:50%';
  return `<div class="chart-bar-row"><span class="chart-label">${label}</span><div class="chart-track"><div class="chart-bar ${cssClass}" style="${place};width:${pct.toFixed(2)}%"></div></div><span class="chart-number">${compactMoney(value, '')}</span></div>`;
}
function renderScenarios(response) {
  const scenarios = response.optimized.scenario_results;
  const base = new Map((response.baseline?.scenario_results || []).map((s) => [s.scenario_id, s]));
  const all = scenarios.flatMap((s) => [s.profit_uzs, base.get(s.scenario_id)?.profit_uzs || 0]);
  const maxAbs = Math.max(1, ...all.map(Math.abs));
  $('#scenario-chart').innerHTML = scenarios.map((s) => {
    const b = base.get(s.scenario_id);
    return `<div class="chart-group"><div class="chart-group-title"><strong>${escapeHtml(s.name)}</strong><span>${fmt0.format(s.probability * 100)}% model weight</span></div>
      ${b ? scenarioCell('B', b.profit_uzs, maxAbs, 'baseline') : '<div class="chart-bar-row"><span class="chart-label">B</span><span class="route-description">Baseline unavailable</span></div>'}
      ${scenarioCell('O', s.profit_uzs, maxAbs, 'optimal')}</div>`;
  }).join('');
}
function renderDiagnostics(response) {
  const sol = response.optimized;
  const compliance = sol.scenario_results.every((r) => r.under_budget && r.markup_compliant && r.registered_ceiling_compliant);
  const diag = [
    `${compliance ? 'PASS' : 'FAIL'} — constraints audited independently after solve across all ${sol.scenario_results.length} scenarios.`,
    `HiGHS reported ${response.status}. Relative optimality gap: ${response.relative_optimality_gap === null ? 'not available' : `${(response.relative_optimality_gap * 100).toFixed(3)}%`}.`,
    `Worst-case working capital: ${compactMoney(sol.worst_case_cash_used_uzs)} of ${compactMoney(state.lastCase.working_capital_budget_uzs)}.`,
    ...response.diagnostics,
  ];
  $('#diagnostics').innerHTML = diag.map((d) => `<div class="diagnostic"><span class="diag-icon">&#10003;</span><span>${escapeHtml(d)}</span></div>`).join('');
}
function renderResults(response) {
  if (!response.optimized) { renderUnsolvable(response); return; }
  const sol = response.optimized;
  metricValue('expected', compactMoney(sol.expected_profit_uzs), sol.expected_profit_uzs < 0);
  metricValue('worst', compactMoney(sol.worst_case_profit_uzs), sol.worst_case_profit_uzs < 0);
  metricValue('improvement', response.expected_profit_delta_uzs === null ? 'N/A' : `${response.expected_profit_delta_uzs >= 0 ? '+' : '−'}${compactMoney(Math.abs(response.expected_profit_delta_uzs))}`, response.expected_profit_delta_uzs < 0);
  $('#margin').textContent = `${fmt1.format(sol.expected_margin_pct)}%`;
  $('#improvement-sub').textContent = response.baseline ? 'vs. unchanged status-quo route' : 'Baseline unavailable in this scenario';
  $('#worst-sub').textContent = `${fmt1.format(sol.worst_case_margin_pct)}% margin under worst modeled shock`;
  renderAllocations(sol, state.lastCase.annual_demand_packs);
  renderScenarios(response);
  renderDiagnostics(response);
}
async function requestJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message;
    try { message = JSON.stringify(await response.json()); } catch (_) { message = await response.text(); }
    throw new Error(`HTTP ${response.status}: ${message}`);
  }
  return response.json();
}
async function solve() {
  if (state.loading) return;
  setBusy(true);
  setStatus('Calculating', 'loading');
  $('#error-banner').hidden = true;
  try {
    const caseInput = cloneCase();
    const risk = Number($('#risk').value) / 100;
    const result = await requestJSON('/api/v1/optimize', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ risk_aversion: risk, case: caseInput }),
    });
    state.lastCase = caseInput;
    state.lastResponse = result;
    if (result.status === 'infeasible') setStatus('Infeasible', 'infeasible');
    else setStatus(result.status === 'optimal' ? 'Optimal' : 'Feasible');
    renderResults(result);
  } catch (err) {
    setStatus('Error', 'error');
    $('#error-banner').textContent = `Solver request failed: ${err.message}`;
    $('#error-banner').hidden = false;
  } finally { setBusy(false); }
}
async function renderCostPlus() {
  const pct = Number($('#discount').value);
  try {
    const r = await requestJSON(`/api/v1/experiments/cost-plus?discount_pct=${encodeURIComponent(pct)}`);
    // Ignore out-of-order responses if the user is still dragging the control.
    if (Number($('#discount').value) !== r.discount_pct) return;
    $('#paradox-before').textContent = compactMoney(r.before.annual_profit_uzs);
    $('#paradox-after').textContent = compactMoney(r.after.annual_profit_uzs);
    const negative = r.profit_delta_uzs < 0;
    $('#paradox-result').textContent = negative
      ? `In this synthetic case, a ${pct}% factory discount reduces modeled annual profit by ${compactMoney(Math.abs(r.profit_delta_uzs))}, because the markup-based sale-price limit also falls.`
      : r.profit_delta_uzs > 0
        ? `Modeled annual profit increases by ${compactMoney(r.profit_delta_uzs)}. The active pricing bound determines the direction.`
        : 'No discount; both outcomes match.';
  } catch (err) {
    $('#paradox-result').textContent = `Experiment unavailable: ${err.message}`;
  }
}
function downloadEvidence() {
  if (!state.lastResponse) return;
  const payload = {
    created_at_utc: new Date().toISOString(),
    context: 'synthetic demonstration, not actual commercial performance or compliance advice',
    inputs: { risk_aversion: Number($('#risk').value) / 100, case: state.lastCase },
    result: state.lastResponse,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'lagrange-rx-evidence.json';
  link.click();
  URL.revokeObjectURL(url);
}
function addListeners() {
  inputs.forEach((id) => $('#' + id).addEventListener('input', updateControlLabels));
  $('#run').addEventListener('click', solve);
  $('#reset').addEventListener('click', () => {
    const defaults = { risk: 35, fx: 12, freight: 35, ceiling: 0, budget: 18.5, discount: 5 };
    Object.entries(defaults).forEach(([id, value]) => { $('#' + id).value = value; });
    $('#supplier').value = '';
    updateControlLabels();
    solve();
    renderCostPlus();
  });
  $('#impossible').addEventListener('click', () => { $('#budget').value = 1; updateControlLabels(); solve(); });
  $('#download').addEventListener('click', downloadEvidence);
  let discountTimer;
  $('#discount').addEventListener('input', () => { clearTimeout(discountTimer); discountTimer = setTimeout(renderCostPlus, 180); });
  updateControlLabels();
}
async function init() {
  addListeners();
  try {
    const [c, assumptions] = await Promise.all([
      requestJSON('/api/v1/demo-case'),
      requestJSON('/api/v1/assumptions'),
    ]);
    state.originalCase = c;
    $('#assumption-list').innerHTML = assumptions.limitations.map((a, i) => `<div class="assumption-item"><b>${String(i + 1).padStart(2, '0')}</b><span>${escapeHtml(a)}</span></div>`).join('');
    await Promise.all([solve(), renderCostPlus()]);
  } catch (err) {
    setStatus('Error', 'error');
    $('#error-banner').textContent = `Could not initialize the decision lab: ${err.message}`;
    $('#error-banner').hidden = false;
  }
}
init();
