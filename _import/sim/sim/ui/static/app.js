const state = {
  overview: null,
  currentLevel: "L0",
  manifests: {},
  truthTables: [],
  baselineEval: null,
};

const PRESETS = {
  baseline: { order_qty_multiplier: 1.0, selling_price_ex_vat_uzs: 111607.14, marketing_multiplier: 1.0, route_id: "" },
  shortage: { order_qty_multiplier: 0.5, selling_price_ex_vat_uzs: 111607.14, marketing_multiplier: 1.0, route_id: "" },
  overstock: { order_qty_multiplier: 2.0, selling_price_ex_vat_uzs: 111607.14, marketing_multiplier: 1.0, route_id: "" },
  air: { order_qty_multiplier: 1.0, selling_price_ex_vat_uzs: 111607.14, marketing_multiplier: 1.0, route_id: "rte_04" },
  breach: { order_qty_multiplier: 1.0, selling_price_ex_vat_uzs: 125000.0, marketing_multiplier: 1.0, route_id: "" },
};

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k === "className") {
      node.className = v;
    } else if (k === "textContent") {
      node.textContent = v;
    } else if (k.startsWith("data-")) {
      node.setAttribute(k, String(v));
    } else {
      node.setAttribute(k, String(v));
    }
  }
  const items = Array.isArray(children) ? children : [children];
  for (const child of items) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function makeCell(label, content, className = "") {
  const td = el("td", { className }, content);
  td.style.setProperty("--label", JSON.stringify(label));
  return td;
}

function fmtNum(val, digits = 0) {
  const num = Number(val);
  if (!Number.isFinite(num)) return String(val ?? "—");
  return num.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtBytes(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} Б`;
  return `${(n / 1024).toFixed(1)} КБ`;
}

function fmtVal(val) {
  if (val === null || val === undefined) return "—";
  if (typeof val === "object") return JSON.stringify(val);
  return String(val);
}

async function fetchJson(url, options = {}) {
  const resp = await fetch(url, options);
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.error || `HTTP ${resp.status}`);
  }
  return data;
}

function setupTabs() {
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  const activateTab = (targetTab) => {
    for (const tab of tabs) {
      const selected = tab === targetTab;
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      tab.setAttribute("tabindex", selected ? "0" : "-1");
      const panel = document.getElementById(tab.getAttribute("aria-controls"));
      if (panel) {
        panel.hidden = !selected;
      }
    }
  };

  tabs.forEach((tab, idx) => {
    tab.addEventListener("click", () => activateTab(tab));
    tab.addEventListener("keydown", (evt) => {
      let nextIdx = null;
      if (evt.key === "ArrowRight") nextIdx = (idx + 1) % tabs.length;
      if (evt.key === "ArrowLeft") nextIdx = (idx - 1 + tabs.length) % tabs.length;
      if (evt.key === "Home") nextIdx = 0;
      if (evt.key === "End") nextIdx = tabs.length - 1;
      if (nextIdx !== null) {
        evt.preventDefault();
        tabs[nextIdx].focus();
        activateTab(tabs[nextIdx]);
      }
    });
  });
}

function renderOverview(overview) {
  state.overview = overview;
  const rep = overview.calibration_report;
  const tgt = overview.calibration_targets;
  document.getElementById("seed-input").value = String(overview.seed);
  document.getElementById("regen-status").textContent = `Готово (seed=${overview.seed})`;
  document.getElementById("company-subtitle").textContent =
    `${overview.company.city}, ${overview.company.country} · Препарат: ${overview.focus_sku.trade_name} (${overview.focus_sku.sku_id})`;

  document.getElementById("kpi-sales").textContent = `${fmtNum(rep.annual_sales_packs)} уп.`;
  document.getElementById("kpi-chchn").textContent = `ChChN ${fmtNum(rep.chchn_with_vat_uzs)} сум`;
  document.getElementById("kpi-chun").textContent = `ChUN ${fmtNum(rep.chun_with_vat_uzs)} сум`;
  document.getElementById("kpi-base").textContent = `Расч. база ${fmtNum(Math.round(rep.calc_base_ex_vat_uzs))} сум`;
  document.getElementById("kpi-margin").textContent = `Маржа ${(rep.net_margin_ex_vat * 100).toFixed(2)}%`;
  document.getElementById("kpi-profit").textContent = `Прибыль: ${fmtNum(Math.round(rep.net_profit_uzs))} сум`;
  document.getElementById("kpi-pre-margin").textContent =
    `До реформы ${(rep.pre_reform_net_margin_ex_vat * 100).toFixed(2)}%`;
  document.getElementById("eval-price").value = String(tgt.wholesale_ex_vat_uzs);
}

async function ensureManifest(level) {
  if (!state.manifests[level]) {
    state.manifests[level] = await fetchJson(`/api/worlds/${encodeURIComponent(level)}`);
  }
  return state.manifests[level];
}

function populateDocTypeSelect(docs) {
  const select = document.getElementById("docs-type-select");
  const current = select.value;
  const types = Array.from(new Set(docs.map((d) => d.doc_type))).sort();
  select.replaceChildren(
    el("option", { value: "", textContent: "Все типы (D1–D13, J1)" }),
    ...types.map((t) => el("option", { value: t, textContent: t }))
  );
  if (types.includes(current)) select.value = current;
}

function renderWorldStats(level) {
  const summary = state.overview.worlds_summary[level];
  document.getElementById("world-level-desc").textContent = `${summary.name}: ${summary.description}`;
  const bar = document.getElementById("world-stats-bar");
  bar.replaceChildren(
    el("div", { className: "stat-chip" }, [`Документов: `, el("strong", { textContent: String(summary.total_docs) })]),
    el("div", { className: "stat-chip" }, [`Искажений: `, el("strong", { textContent: String(summary.total_corruptions) })]),
    el("div", { className: "stat-chip" }, [`Восстановимых: `, el("strong", { textContent: String(summary.recoverable_count) })]),
    el("div", { className: "stat-chip" }, [`Невосстановимых: `, el("strong", { textContent: String(summary.unrecoverable_count) })]),
    el("div", { className: "stat-chip" }, [`Пропущенных (omitted): `, el("strong", { textContent: String(summary.omitted_documents_count) })])
  );
}

function renderDocumentsTable(level) {
  const manifest = state.manifests[level];
  if (!manifest) return;
  const query = document.getElementById("docs-search-input").value.trim().toLowerCase();
  const docType = document.getElementById("docs-type-select").value;
  const corruptFilter = document.getElementById("docs-corrupt-select").value;

  const filtered = manifest.documents.filter((doc) => {
    if (docType && doc.doc_type !== docType) return false;
    if (corruptFilter === "corrupted" && doc.corruptions.length === 0) return false;
    if (corruptFilter === "clean" && doc.corruptions.length > 0) return false;
    if (query) {
      const hay = `${doc.filename} ${doc.doc_type} ${(doc.truth_refs || []).join(" ")}`.toLowerCase();
      if (!hay.includes(query)) return false;
    }
    return true;
  });

  const tbody = document.getElementById("docs-tbody");
  if (filtered.length === 0) {
    tbody.replaceChildren(el("tr", {}, [makeCell("Результат", "Документы по фильтру не найдены")]));
    return;
  }

  const rows = filtered.map((doc) => {
    const corrCount = doc.corruptions.length;
    const unrecCount = doc.corruptions.filter((c) => !c.recoverable).length;
    let badge;
    if (corrCount === 0) {
      badge = el("span", { className: "badge badge-ok", textContent: "Чистый (0)" });
    } else if (unrecCount > 0) {
      badge = el("span", { className: "badge badge-danger", textContent: `Потери (${corrCount}, безвозвр. ${unrecCount})` });
    } else {
      badge = el("span", { className: "badge badge-warn", textContent: `Искажён (${corrCount}, восст.)` });
    }

    const previewBtn = el("button", {
      type: "button",
      className: "btn btn-primary btn-sm",
      "data-action": "preview",
      "data-level": level,
      "data-filename": doc.filename,
      textContent: "Просмотр",
    });
    const downloadLink = el("a", {
      href: doc.download_url,
      download: doc.filename,
      className: "btn btn-secondary btn-sm",
      textContent: "Скачать",
    });

    return el("tr", {}, [
      el("th", { scope: "row", textContent: doc.filename }),
      makeCell("Тип", doc.doc_type),
      makeCell("Формат", doc.preview_kind.toUpperCase()),
      makeCell("Размер", fmtBytes(doc.size_bytes)),
      makeCell("Искажения", badge),
      makeCell("Ссылки truth", String((doc.truth_refs || []).length)),
      makeCell("Действия", el("div", { className: "actions-cell" }, [previewBtn, downloadLink])),
    ]);
  });
  tbody.replaceChildren(...rows);
}

async function selectWorldLevel(level) {
  state.currentLevel = level;
  for (const btn of document.querySelectorAll("#world-level-pills .pill-btn")) {
    btn.setAttribute("aria-pressed", btn.dataset.level === level ? "true" : "false");
  }
  renderWorldStats(level);
  const manifest = await ensureManifest(level);
  populateDocTypeSelect(manifest.documents);
  renderDocumentsTable(level);
}

function buildDataTable(headers, rows) {
  const thead = el("thead", {}, [
    el("tr", {}, headers.map((h) => el("th", { scope: "col", textContent: String(h ?? "") }))),
  ]);
  const tbody = el("tbody", {}, rows.map((row) =>
    el("tr", {}, headers.map((h, idx) =>
      idx === 0
        ? el("th", { scope: "row", textContent: fmtVal(row[idx]) })
        : makeCell(String(h ?? ""), fmtVal(row[idx]))
    ))
  ));
  return el("div", { className: "table-wrapper", tabindex: "0" }, [
    el("table", { className: "data-table" }, [thead, tbody]),
  ]);
}

function renderPreviewContent(preview) {
  if (preview.kind === "pdf") {
    return el("iframe", {
      className: "preview-frame",
      src: preview.media_url,
      title: `PDF документ ${preview.manifest_entry.filename || ""}`,
    });
  }
  if (preview.kind === "image") {
    return el("img", {
      className: "preview-image",
      src: preview.media_url,
      alt: `Изображение документа ${preview.manifest_entry.filename || ""}`,
    });
  }
  if (preview.kind === "csv") {
    const info = el("p", {
      className: "panel-desc",
      textContent: `Кодировка: ${preview.encoding} · Разделитель: "${preview.delimiter}" · Показано ${preview.rows.length} из ${preview.total_rows} строк`,
    });
    return el("div", {}, [info, buildDataTable(preview.headers, preview.rows)]);
  }
  if (preview.kind === "xlsx") {
    const blocks = Object.entries(preview.sheets || {}).map(([name, sheet]) =>
      el("div", { className: "modal-section" }, [
        el("h3", { textContent: `Лист «${name}» (строк: ${sheet.total_rows})` }),
        buildDataTable(sheet.headers || [], sheet.rows || []),
      ])
    );
    return el("div", {}, blocks);
  }
  if (preview.kind === "eml") {
    const attText = (preview.attachments || []).map((a) => `${a.filename} (${fmtBytes(a.size_bytes)})`).join(", ") || "нет";
    return el("div", { className: "eml-box" }, [
      el("p", {}, [el("strong", { textContent: "От: " }), preview.from]),
      el("p", {}, [el("strong", { textContent: "Кому: " }), preview.to]),
      el("p", {}, [el("strong", { textContent: "Тема: " }), preview.subject]),
      el("p", {}, [el("strong", { textContent: "Дата: " }), preview.date]),
      el("p", {}, [el("strong", { textContent: "Вложения: " }), attText]),
      el("pre", { className: "eml-body", textContent: preview.body }),
    ]);
  }
  return el("p", { textContent: "Предпросмотр для этого формата недоступен." });
}

function renderModalManifestSection(preview) {
  const corrs = (preview.manifest_entry && preview.manifest_entry.corruptions) || [];
  const corrBlock = el("div", { className: "modal-section" }, [
    el("h3", { textContent: `Операторы порчи в документе (${corrs.length})` }),
  ]);
  if (corrs.length === 0) {
    corrBlock.appendChild(el("p", { className: "panel-desc", textContent: "Искажения не зарегистрированы (эталонный документ)." }));
  } else {
    const headers = ["Оператор", "Поле", "true_value", "shown_value", "Восстановимо", "Подсказка"];
    const rows = corrs.map((c) => [
      c.operator,
      c.field,
      fmtVal(c.true_value),
      fmtVal(c.shown_value),
      c.recoverable ? "Да (восстановимо)" : "Нет (безвозвратно)",
      c.recover_hint,
    ]);
    corrBlock.appendChild(buildDataTable(headers, rows));
  }

  const refs = preview.truth_preview || [];
  const refBlock = el("div", { className: "modal-section" }, [
    el("h3", { textContent: `Связанные записи истины truth_refs (${refs.length})` }),
  ]);
  if (refs.length === 0) {
    refBlock.appendChild(el("p", { className: "panel-desc", textContent: "Связанные строки truth_refs отсутствуют." }));
  } else {
    const refItems = refs.map((r) =>
      el("div", { className: "stat-chip mono" }, [`${r.ref}: ${JSON.stringify(r.row)}`])
    );
    refBlock.appendChild(el("div", { className: "stats-bar" }, refItems));
  }
  return [corrBlock, refBlock];
}

async function openDocumentModal(level, filename) {
  const dialog = document.getElementById("doc-preview-modal");
  const titleEl = document.getElementById("modal-title");
  const subEl = document.getElementById("modal-subtitle");
  const bodyEl = document.getElementById("modal-body");

  titleEl.textContent = filename;
  subEl.textContent = `Уровень ${level} · Загрузка предпросмотра...`;
  bodyEl.replaceChildren(el("p", { textContent: "Загрузка..." }));
  dialog.showModal();

  try {
    const preview = await fetchJson(
      `/api/worlds/${encodeURIComponent(level)}/preview/${encodeURIComponent(filename)}`
    );
    subEl.textContent = `Уровень ${level} · Тип ${preview.manifest_entry.doc_type || "—"} · Формат ${preview.kind}`;
    bodyEl.replaceChildren(renderPreviewContent(preview), ...renderModalManifestSection(preview));
  } catch (err) {
    bodyEl.replaceChildren(el("p", { className: "error-text", textContent: `Ошибка загрузки: ${err.message}` }));
  }
}

function setupModal() {
  const dialog = document.getElementById("doc-preview-modal");
  dialog.addEventListener("close", () => {
    dialog.returnValue = "";
  });
  if (!("closedBy" in HTMLDialogElement.prototype)) {
    let downOutside = false;
    const isOutside = (evt) => {
      const rect = dialog.getBoundingClientRect();
      return (
        evt.clientX < rect.left ||
        evt.clientX > rect.right ||
        evt.clientY < rect.top ||
        evt.clientY > rect.bottom
      );
    };
    dialog.addEventListener("pointerdown", (evt) => {
      downOutside = isOutside(evt);
    });
    dialog.addEventListener("pointerup", (evt) => {
      if (downOutside && isOutside(evt)) {
        dialog.close();
      }
      downOutside = false;
    });
  }
}

function validateFormWithFocus(form) {
  const controls = Array.from(form.querySelectorAll("input, select, textarea"));
  let firstInvalid = null;
  for (const ctrl of controls) {
    if (ctrl.checkValidity()) {
      ctrl.removeAttribute("aria-invalid");
    } else {
      ctrl.setAttribute("aria-invalid", "true");
      if (!firstInvalid) firstInvalid = ctrl;
    }
  }
  if (firstInvalid) {
    firstInvalid.focus();
    firstInvalid.reportValidity();
    return false;
  }
  return true;
}

function bindValidationClear(form) {
  form.addEventListener("input", (evt) => {
    const ctrl = evt.target;
    if (ctrl && typeof ctrl.checkValidity === "function" && ctrl.checkValidity()) {
      ctrl.removeAttribute("aria-invalid");
    }
  });
}

async function renderCorruptionsTab() {
  const levelSel = document.getElementById("corr-level-select").value;
  const opSel = document.getElementById("corr-op-select").value;
  const recSel = document.getElementById("corr-rec-select").value;

  const levelsToLoad = levelSel === "ALL" ? ["L0", "L1", "L2", "L3", "L4"] : [levelSel];
  const manifests = await Promise.all(levelsToLoad.map((lvl) => ensureManifest(lvl)));

  const omittedItems = [];
  const allRows = [];
  const operatorsSet = new Set();

  manifests.forEach((man) => {
    for (const om of man.omitted_documents || []) {
      omittedItems.push(`[${man.level}] ${om.doc_type} (${om.scope}): ${om.reason}`);
    }
    for (const doc of man.documents || []) {
      for (const corr of doc.corruptions || []) {
        operatorsSet.add(corr.operator);
        if (opSel && corr.operator !== opSel) continue;
        if (recSel === "true" && !corr.recoverable) continue;
        if (recSel === "false" && corr.recoverable) continue;
        allRows.push({ level: man.level, filename: doc.filename, ...corr });
      }
    }
  });

  const opSelectEl = document.getElementById("corr-op-select");
  const sortedOps = Array.from(operatorsSet).sort();
  if (opSelectEl.options.length <= 1 && sortedOps.length > 0) {
    opSelectEl.replaceChildren(
      el("option", { value: "", textContent: "Все операторы" }),
      ...sortedOps.map((op) => el("option", { value: op, textContent: op }))
    );
    opSelectEl.value = opSel;
  }

  const omittedBox = document.getElementById("omitted-docs-box");
  if (omittedItems.length > 0) {
    omittedBox.hidden = false;
    omittedBox.replaceChildren(
      el("strong", { textContent: `Пропущенные документы (omitted_documents, ${omittedItems.length}): ` }),
      el("span", { textContent: omittedItems.join(" | ") })
    );
  } else {
    omittedBox.hidden = true;
    omittedBox.replaceChildren();
  }

  const tbody = document.getElementById("corr-tbody");
  if (allRows.length === 0) {
    tbody.replaceChildren(el("tr", {}, [makeCell("Результат", "Искажений для выбранных фильтров нет")]));
    return;
  }

  const trs = allRows.map((c) => {
    const statusBadge = c.recoverable
      ? el("span", { className: "badge badge-warn", textContent: "Восстановимо" })
      : el("span", { className: "badge badge-danger", textContent: "Безвозвратно" });
    return el("tr", {}, [
      el("th", { scope: "row", textContent: c.filename }),
      makeCell("Уровень", c.level),
      makeCell("Оператор", el("span", { className: "badge badge-neutral", textContent: c.operator })),
      makeCell("Поле", el("span", { className: "mono", textContent: c.field })),
      makeCell("Истина", el("span", { className: "mono", textContent: fmtVal(c.true_value) })),
      makeCell("В документе", el("span", { className: "mono", textContent: fmtVal(c.shown_value) })),
      makeCell("Статус", statusBadge),
      makeCell("Подсказка", c.recover_hint),
    ]);
  });
  tbody.replaceChildren(...trs);
}

async function initTruthTab() {
  const data = await fetchJson("/api/truth");
  state.truthTables = data.tables || [];
  const select = document.getElementById("truth-table-select");
  select.replaceChildren(
    ...state.truthTables.map((t) =>
      el("option", { value: t.name, textContent: `${t.name} (${t.row_count} строк)` })
    )
  );
  await loadTruthTable();
}

async function loadTruthTable() {
  const tableName = document.getElementById("truth-table-select").value;
  if (!tableName) return;
  const skuId = document.getElementById("truth-sku-select").value;
  const limit = document.getElementById("truth-limit-select").value;
  const qs = new URLSearchParams({ limit });
  if (skuId) qs.set("sku_id", skuId);

  const data = await fetchJson(`/api/truth/${encodeURIComponent(tableName)}?${qs.toString()}`);
  document.getElementById("truth-meta-pill").textContent =
    `Таблица ${data.table}: показано ${data.rows.length} из ${data.total_rows} строк`;

  const thead = document.getElementById("truth-thead");
  const tbody = document.getElementById("truth-tbody");
  thead.replaceChildren(
    el("tr", {}, data.columns.map((col) => el("th", { scope: "col", textContent: col })))
  );

  if (data.rows.length === 0) {
    tbody.replaceChildren(el("tr", {}, [makeCell("Результат", "Строки не найдены")]));
    return;
  }

  const trs = data.rows.map((row) =>
    el("tr", {}, data.columns.map((col, idx) =>
      idx === 0
        ? el("th", { scope: "row", textContent: fmtVal(row[col]) })
        : makeCell(col, fmtVal(row[col]))
    ))
  );
  tbody.replaceChildren(...trs);
}

function renderEvalSvg(metrics) {
  const svg = document.getElementById("eval-chart");
  const ns = "http://www.w3.org/2000/svg";
  svg.replaceChildren();

  const items = [
    { label: "Выручка", value: metrics.revenue_ex_vat_uzs / 1e9, color: "#0284c7" },
    { label: "COGS", value: metrics.cogs_ex_vat_uzs / 1e9, color: "#64748b" },
    { label: "Опер. расходы", value: metrics.operating_costs_uzs / 1e9, color: "#f59e0b" },
    { label: "Прибыль сценария", value: metrics.net_profit_uzs / 1e9, color: metrics.net_profit_uzs >= 0 ? "#0f766e" : "#b91c1c" },
    { label: "Прибыль эталона", value: metrics.baseline_profit_uzs / 1e9, color: "#0f172a" },
  ];

  const maxAbs = Math.max(1, ...items.map((i) => Math.abs(i.value)));
  const zeroY = 175;
  const maxBarH = 135;

  const axis = document.createElementNS(ns, "line");
  axis.setAttribute("x1", "30");
  axis.setAttribute("x2", "620");
  axis.setAttribute("y1", String(zeroY));
  axis.setAttribute("y2", String(zeroY));
  axis.setAttribute("stroke", "#94a3b8");
  axis.setAttribute("stroke-width", "1.5");
  svg.appendChild(axis);

  items.forEach((item, idx) => {
    const x = 55 + idx * 114;
    const barW = 72;
    const barH = Math.max(2, (Math.abs(item.value) / maxAbs) * maxBarH);
    const y = item.value >= 0 ? zeroY - barH : zeroY;

    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(y));
    rect.setAttribute("width", String(barW));
    rect.setAttribute("height", String(barH));
    rect.setAttribute("rx", "4");
    rect.setAttribute("fill", item.color);
    svg.appendChild(rect);

    const valTxt = document.createElementNS(ns, "text");
    valTxt.setAttribute("x", String(x + barW / 2));
    valTxt.setAttribute("y", String(item.value >= 0 ? y - 6 : y + barH + 14));
    valTxt.setAttribute("text-anchor", "middle");
    valTxt.setAttribute("font-size", "12");
    valTxt.setAttribute("font-weight", "700");
    valTxt.setAttribute("fill", "#0f172a");
    valTxt.textContent = `${item.value.toFixed(2)}B`;
    svg.appendChild(valTxt);

    const lblTxt = document.createElementNS(ns, "text");
    lblTxt.setAttribute("x", String(x + barW / 2));
    lblTxt.setAttribute("y", "235");
    lblTxt.setAttribute("text-anchor", "middle");
    lblTxt.setAttribute("font-size", "11");
    lblTxt.setAttribute("fill", "#334155");
    lblTxt.textContent = item.label;
    svg.appendChild(lblTxt);
  });
}

function renderEvalResult(data) {
  const m = data.metrics;
  const grid = document.getElementById("eval-metrics-grid");
  const tiles = [
    ["Чистая прибыль", `${fmtNum(Math.round(m.net_profit_uzs))} сум`],
    ["Чистая маржа", `${(m.net_margin_ex_vat * 100).toFixed(2)}%`],
    ["Упущение vs эталон (Regret)", `${fmtNum(Math.round(m.regret_vs_baseline_uzs))} сум`],
    ["Выручка без НДС", `${fmtNum(Math.round(m.revenue_ex_vat_uzs))} сум`],
    ["Продажи за 12 мес.", `${fmtNum(m.annual_sales_packs)} уп.`],
    ["Дефицит (stockout)", `${fmtNum(m.stockout_packs)} уп.`],
    ["Списания (writeoffs)", `${fmtNum(m.writeoff_packs)} уп.`],
    ["Операционные расходы", `${fmtNum(Math.round(m.operating_costs_uzs))} сум`],
  ];
  grid.replaceChildren(
    ...tiles.map(([label, val]) =>
      el("div", { className: "metric-tile" }, [el("span", { textContent: label }), el("strong", { textContent: val })])
    )
  );
  renderEvalSvg(m);
}

async function runEvaluation() {
  const errEl = document.getElementById("eval-error");
  errEl.textContent = "";
  const form = document.getElementById("eval-form");
  if (!validateFormWithFocus(form)) {
    return;
  }
  const routeRadio = form.querySelector('input[name="route_id"]:checked');
  const payload = {
    order_qty_multiplier: Number(document.getElementById("eval-qty-mult").value),
    selling_price_ex_vat_uzs: Number(document.getElementById("eval-price").value),
    marketing_multiplier: Number(document.getElementById("eval-mkt-mult").value),
    route_id: routeRadio && routeRadio.value ? routeRadio.value : null,
  };
  try {
    const result = await fetchJson("/api/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderEvalResult(result);
  } catch (err) {
    errEl.textContent = err.message;
  }
}

function setupEventListeners() {
  document.getElementById("world-level-pills").addEventListener("click", (evt) => {
    const btn = evt.target.closest("button[data-level]");
    if (btn) selectWorldLevel(btn.dataset.level);
  });

  document.getElementById("docs-filter-form").addEventListener("input", () => renderDocumentsTable(state.currentLevel));
  document.getElementById("docs-filter-form").addEventListener("change", () => renderDocumentsTable(state.currentLevel));

  document.getElementById("docs-tbody").addEventListener("click", (evt) => {
    const btn = evt.target.closest('button[data-action="preview"]');
    if (btn) openDocumentModal(btn.dataset.level, btn.dataset.filename);
  });

  document.getElementById("corr-filter-form").addEventListener("change", () => renderCorruptionsTab());
  document.getElementById("truth-filter-form").addEventListener("change", () => loadTruthTable());

  document.getElementById("eval-presets").addEventListener("click", (evt) => {
    const btn = evt.target.closest("button[data-preset]");
    if (!btn) return;
    const preset = PRESETS[btn.dataset.preset];
    if (!preset) return;
    document.getElementById("eval-qty-mult").value = String(preset.order_qty_multiplier);
    document.getElementById("eval-price").value = String(preset.selling_price_ex_vat_uzs);
    document.getElementById("eval-mkt-mult").value = String(preset.marketing_multiplier);
    const targetRadio = document.querySelector(`input[name="route_id"][value="${preset.route_id}"]`);
    if (targetRadio) targetRadio.checked = true;
    runEvaluation();
  });

  const evalForm = document.getElementById("eval-form");
  bindValidationClear(evalForm);
  evalForm.addEventListener("submit", (evt) => {
    evt.preventDefault();
    runEvaluation();
  });

  const regenForm = document.getElementById("regenerate-form");
  bindValidationClear(regenForm);
  regenForm.addEventListener("submit", async (evt) => {
    evt.preventDefault();
    const form = evt.currentTarget;
    if (!validateFormWithFocus(form)) {
      return;
    }
    const seed = Number(document.getElementById("seed-input").value);
    const statusEl = document.getElementById("regen-status");
    const submitBtn = document.getElementById("regen-submit-btn");
    submitBtn.disabled = true;
    statusEl.textContent = `Генерация seed=${seed}...`;
    try {
      const overview = await fetchJson("/api/regenerate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seed }),
      });
      state.manifests = {};
      renderOverview(overview);
      await Promise.all([selectWorldLevel(state.currentLevel), renderCorruptionsTab(), initTruthTab(), runEvaluation()]);
      statusEl.textContent = `Обновлено (seed=${overview.seed})`;
    } catch (err) {
      statusEl.textContent = `Ошибка: ${err.message}`;
    } finally {
      submitBtn.disabled = false;
    }
  });
}

async function init() {
  setupTabs();
  setupModal();
  setupEventListeners();
  const overview = await fetchJson("/api/overview");
  renderOverview(overview);
  await Promise.all([
    selectWorldLevel("L0"),
    renderCorruptionsTab(),
    initTruthTab(),
    fetchJson("/api/evaluate/baseline").then(renderEvalResult),
  ]);
}

init().catch((err) => {
  document.getElementById("regen-status").textContent = `Ошибка загрузки: ${err.message}`;
});
