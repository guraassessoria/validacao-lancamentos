const state = {
  rows: [],
  columns: [],
  issues: [],
  serverMode: false,
  serverOutputUrl: "",
  settingsTimer: null,
  sortColumn: "date",
  sortDirection: "asc",
};

const selectors = {
  fileInput: document.querySelector("#fileInput"),
  supplierFileInput: document.querySelector("#supplierFileInput"),
  accountPlanFileInput: document.querySelector("#accountPlanFileInput"),
  importBtn: document.querySelector("#importBtn"),
  importSupplierBtn: document.querySelector("#importSupplierBtn"),
  importAccountPlanBtn: document.querySelector("#importAccountPlanBtn"),
  supplierDrop: document.querySelector("#supplierDrop"),
  accountPlanDrop: document.querySelector("#accountPlanDrop"),
  supplierFileName: document.querySelector("#supplierFileName"),
  accountPlanFileName: document.querySelector("#accountPlanFileName"),
  fileName: document.querySelector("#fileName"),
  analyzeBtn: document.querySelector("#analyzeBtn"),
  exportBtn: document.querySelector("#exportBtn"),
  settingsBtn: document.querySelector("#settingsBtn"),
  backBtn: document.querySelector("#backBtn"),
  mainView: document.querySelector("#mainView"),
  settingsView: document.querySelector("#settingsView"),
  monthInput: document.querySelector("#monthInput"),
  resultPrefixes: document.querySelector("#resultPrefixes"),
  ignoredWords: document.querySelector("#ignoredWords"),
  excludedPatterns: document.querySelector("#excludedPatterns"),
  dateColumn: document.querySelector("#dateColumn"),
  debitColumn: document.querySelector("#debitColumn"),
  creditColumn: document.querySelector("#creditColumn"),
  historyColumn: document.querySelector("#historyColumn"),
  mappingPanel: document.querySelector("#mappingPanel"),
  sampleInfo: document.querySelector("#sampleInfo"),
  resultsBody: document.querySelector("#resultsBody"),
  searchInput: document.querySelector("#searchInput"),
  columnFilters: document.querySelectorAll(".column-filter"),
  sortButtons: document.querySelectorAll(".sort-button"),
  totalRows: document.querySelector("#totalRows"),
  resultRows: document.querySelector("#resultRows"),
  currentRows: document.querySelector("#currentRows"),
  issueRows: document.querySelector("#issueRows"),
};

const aliases = {
  date: ["CT2_DATA", "DATA LCTO", "DATA", "DT", "DT_LANC", "DATA_LANCAMENTO"],
  debit: ["CT2_DEBITO", "CTA DEBITO", "CTA DEB", "DEBITO", "CONTA_DEBITO", "CTA_DEBITO"],
  credit: ["CT2_CREDIT", "CT2_CREDITO", "CTA CREDITO", "CTA CRED", "CREDITO", "CONTA_CREDITO", "CTA_CREDITO"],
  history: ["CT2_HIST", "CT2_HISTOR", "HIST LANC", "HISTORICO LANC", "HISTORICO", "HIST", "DESCRICAO"],
};

selectors.fileInput.addEventListener("change", handleFile);
selectors.supplierFileInput.addEventListener("change", handleSupplierFile);
selectors.accountPlanFileInput.addEventListener("change", handleAccountPlanFile);
selectors.importBtn.addEventListener("click", importSelectedFile);
selectors.importSupplierBtn.addEventListener("click", importSupplierFile);
selectors.importAccountPlanBtn.addEventListener("click", importAccountPlanFile);
selectors.analyzeBtn.addEventListener("click", runAnalysis);
selectors.exportBtn.addEventListener("click", exportIssues);
selectors.settingsBtn.addEventListener("click", showSettings);
selectors.backBtn.addEventListener("click", showMain);
selectors.searchInput.addEventListener("input", () => renderIssues(state.issues));
selectors.columnFilters.forEach((control) => {
  control.addEventListener("input", () => renderIssues(state.issues));
});
selectors.sortButtons.forEach((button) => {
  button.addEventListener("click", () => sortIssues(button.dataset.sort));
});
[selectors.resultPrefixes, selectors.ignoredWords, selectors.excludedPatterns].forEach((control) => {
  control.addEventListener("input", scheduleSettingsSave);
});
initServerMode();

function showSettings() {
  selectors.mainView.hidden = true;
  selectors.settingsView.hidden = false;
}

function showMain() {
  selectors.settingsView.hidden = true;
  selectors.mainView.hidden = false;
}

function sortIssues(column) {
  if (state.sortColumn === column) {
    state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
  } else {
    state.sortColumn = column;
    state.sortDirection = "asc";
  }

  renderIssues(state.issues);
}

async function handleFile(event) {
  const [file] = event.target.files;
  if (!file) return;

  selectors.fileName.textContent = file.name;

  if (state.serverMode) {
    selectors.importBtn.disabled = false;
    renderEmpty("Arquivo selecionado. Importe para atualizar a base fixa.");
    return;
  }

  const extension = file.name.split(".").pop().toLowerCase();
  const rows = extension === "csv" ? await readCsv(file) : await readWorkbook(file);

  const enrichedRows = enrichContinuationHistories(rows);
  state.rows = enrichedRows;
  state.columns = collectColumns(rows);
  populateColumnSelectors(state.columns);
  selectors.mappingPanel.hidden = false;
  selectors.analyzeBtn.disabled = enrichedRows.length === 0;
  selectors.sampleInfo.textContent = `${enrichedRows.length.toLocaleString("pt-BR")} linhas principais carregadas. Confira as colunas antes de analisar.`;
  setMetric("totalRows", enrichedRows.length);
  setMetric("resultRows", 0);
  setMetric("currentRows", 0);
  setMetric("issueRows", 0);
  renderEmpty("Arquivo carregado. Configure o mes analisado e execute a analise.");
}

function readCsv(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = reader.result;
      const delimiter = detectDelimiter(text);
      const workbook = XLSX.read(trimToHeader(text), { type: "string", FS: delimiter });
      const sheet = workbook.Sheets[workbook.SheetNames[0]];
      resolve(XLSX.utils.sheet_to_json(sheet, { defval: "" }));
    };
    reader.onerror = reject;
    reader.readAsText(file, "utf-8");
  });
}

function readWorkbook(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const workbook = XLSX.read(reader.result, { type: "array", cellDates: true });
      const sheet = workbook.Sheets[workbook.SheetNames[0]];
      resolve(XLSX.utils.sheet_to_json(sheet, { defval: "" }));
    };
    reader.onerror = reject;
    reader.readAsArrayBuffer(file);
  });
}

function detectDelimiter(text) {
  const firstLine = trimToHeader(text).split(/\r?\n/)[0] || "";
  const semicolons = (firstLine.match(/;/g) || []).length;
  const commas = (firstLine.match(/,/g) || []).length;
  return semicolons >= commas ? ";" : ",";
}

function trimToHeader(text) {
  const lines = String(text ?? "").split(/\r?\n/);
  const headerIndex = lines.findIndex((line) => {
    const normalized = normalizeText(line);
    return normalized.includes("DATA LCTO") && normalized.includes("HIST LANC");
  });
  return headerIndex >= 0 ? lines.slice(headerIndex).join("\n") : String(text ?? "");
}

function enrichContinuationHistories(rows) {
  const enriched = [];
  let activeRow = null;

  rows.forEach((row) => {
    if (isContinuationHistory(row)) {
      if (activeRow) {
        activeRow.__fullHistory = `${activeRow.__fullHistory || ""}${row["Hist Lanc"] || ""}`;
      }
      return;
    }

    activeRow = { ...row, __fullHistory: row["Hist Lanc"] || "" };
    enriched.push(activeRow);
  });

  return enriched;
}

function isContinuationHistory(row) {
  return normalizeText(row["Tipo Lcto"]).includes("CONT HIST");
}

function collectColumns(rows) {
  const columns = new Set();
  rows.slice(0, 50).forEach((row) => Object.keys(row).forEach((column) => columns.add(column)));
  return [...columns];
}

function populateColumnSelectors(columns) {
  const controls = [
    [selectors.dateColumn, "date"],
    [selectors.debitColumn, "debit"],
    [selectors.creditColumn, "credit"],
    [selectors.historyColumn, "history"],
  ];

  controls.forEach(([select, type]) => {
    select.innerHTML = "";
    columns.forEach((column) => {
      const option = document.createElement("option");
      option.value = column;
      option.textContent = column;
      select.append(option);
    });
    select.value = findColumn(columns, aliases[type]);
  });
}

function findColumn(columns, candidates) {
  const normalized = columns.map((column) => [column, normalizeText(column)]);
  const found = normalized.find(([, column]) => candidates.some((candidate) => column === normalizeText(candidate)));
  return found ? found[0] : columns[0] || "";
}

function runAnalysis() {
  if (state.serverMode) {
    runServerAnalysis();
    return;
  }

  if (!selectors.monthInput.value) {
    renderEmpty("Informe o mes analisado antes de executar.");
    return;
  }

  const config = getConfig();
  const entries = buildEntries(state.rows, config);
  const historicalEntries = entries.filter((entry) => entry.month < config.month);
  const currentEntries = entries.filter((entry) => entry.month === config.month);
  const supplierHistory = buildSupplierHistory(historicalEntries);

  state.issues = currentEntries.filter((entry) => {
    const previous = supplierHistory.get(entry.supplierKey);
    return !previous || !previous.accounts.has(entry.account);
  }).map((entry) => ({
    ...entry,
    previousAccounts: supplierHistory.get(entry.supplierKey)?.lastAccounts || [],
  }));

  setMetric("totalRows", state.rows.length);
  setMetric("resultRows", entries.length);
  setMetric("currentRows", currentEntries.length);
  setMetric("issueRows", state.issues.length);
  selectors.exportBtn.disabled = state.issues.length === 0;
  selectors.searchInput.disabled = state.issues.length === 0;
  setTableControlsEnabled(state.issues.length > 0);
  renderIssues(state.issues);
}

function handleSupplierFile(event) {
  const [file] = event.target.files;
  if (!file) return;

  selectors.supplierFileName.textContent = file.name;
  selectors.importSupplierBtn.disabled = false;
  renderEmpty("Cadastro selecionado. Importe o MATA020 antes da CT2 para melhorar a identificacao dos fornecedores.");
}

function handleAccountPlanFile(event) {
  const [file] = event.target.files;
  if (!file) return;

  selectors.accountPlanFileName.textContent = file.name;
  selectors.importAccountPlanBtn.disabled = false;
  renderEmpty("Plano de contas selecionado. Importe para exibir descricoes das contas nas divergencias.");
}

async function initServerMode() {
  try {
    const response = await fetch("/api/base");
    if (!response.ok) return;

    state.serverMode = true;
    selectors.supplierDrop.hidden = false;
    selectors.accountPlanDrop.hidden = false;
    selectors.importSupplierBtn.hidden = false;
    selectors.importAccountPlanBtn.hidden = false;
    selectors.importBtn.hidden = false;
    selectors.analyzeBtn.disabled = false;
    selectors.fileName.textContent = "Selecionar XLSX ou CSV";
    await loadSettings();
    updateBaseSummary(await response.json());
  } catch {
    state.serverMode = false;
  }
}

async function runServerAnalysis() {
  if (!selectors.monthInput.value) {
    renderEmpty("Informe o mes analisado antes de executar.");
    return;
  }

  selectors.analyzeBtn.disabled = true;
  selectors.exportBtn.disabled = true;
  selectors.searchInput.disabled = true;
  selectors.sampleInfo.textContent = "Processando CT2 no SQLite local...";
  renderEmpty("Analise em andamento. Arquivos grandes podem levar alguns segundos.");

  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      body: buildAnalysisPayload(selectors.monthInput.value),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || payload.detail || "Falha ao analisar o arquivo.");

    const serverRows = payload.divergences || payload.preview || [];
    state.serverOutputUrl = payload.downloadUrl;
    state.issues = serverRows.map(normalizeServerIssue);
    setMetric("totalRows", payload.imported);
    setMetric("resultRows", payload.imported);
    setMetric("currentRows", payload.currentEntries);
    setMetric("issueRows", payload.total);
    selectors.sampleInfo.textContent = `${Number(payload.total).toLocaleString("pt-BR")} divergencias geradas. A tabela mostra todos os lancamentos do mes analisado.`;
    selectors.exportBtn.disabled = payload.total === 0;
    selectors.searchInput.disabled = state.issues.length === 0;
    setTableControlsEnabled(state.issues.length > 0);
    renderIssues(state.issues);
  } catch (error) {
    renderEmpty(error.message);
    selectors.sampleInfo.textContent = "Nao foi possivel concluir a analise.";
  } finally {
    selectors.analyzeBtn.disabled = false;
  }
}

async function importSelectedFile() {
  const [file] = selectors.fileInput.files;
  if (!file) {
    renderEmpty("Selecione um CSV da CT2 para importar.");
    return;
  }

  await importFile(file, {
    button: selectors.importBtn,
    kind: "ct2",
    progress: "Importando CT2 para a base fixa...",
    waiting: "Importacao em andamento. Meses ja existentes serao substituidos.",
  });
}

async function importSupplierFile() {
  const [file] = selectors.supplierFileInput.files;
  if (!file) {
    renderEmpty("Selecione o XML do MATA020 para importar.");
    return;
  }

  await importFile(file, {
    button: selectors.importSupplierBtn,
    kind: "supplier",
    progress: "Importando cadastro MATA020...",
    waiting: "Importacao do cadastro em andamento.",
  });
}

async function importAccountPlanFile() {
  const [file] = selectors.accountPlanFileInput.files;
  if (!file) {
    renderEmpty("Selecione o plano de contas para importar.");
    return;
  }

  await importFile(file, {
    button: selectors.importAccountPlanBtn,
    kind: "accountPlan",
    progress: "Importando plano de contas...",
    waiting: "Importacao do plano de contas em andamento.",
  });
}

async function importFile(file, messages) {
  if (state.serverMode) {
    await saveSettings();
  }

  messages.button.disabled = true;
  selectors.analyzeBtn.disabled = true;
  selectors.sampleInfo.textContent = messages.progress;
  renderEmpty(messages.waiting);

  try {
    const payload = await uploadFileWithProgress(file, messages.progress, messages.kind);

    updateBaseSummary(payload.base);
    if (payload.supplierCount) {
      renderEmpty(`${Number(payload.supplierCount).toLocaleString("pt-BR")} fornecedores importados do cadastro.`);
    } else if (payload.accountCount) {
      renderEmpty(`${Number(payload.accountCount).toLocaleString("pt-BR")} contas importadas do plano de contas.`);
    } else {
      renderEmpty(`Base atualizada: ${payload.months.join(", ")}.`);
    }
  } catch (error) {
    selectors.sampleInfo.textContent = "Nao foi possivel importar o arquivo.";
    renderEmpty(error.message);
  } finally {
    messages.button.disabled = false;
    selectors.analyzeBtn.disabled = false;
  }
}

function scheduleSettingsSave() {
  if (!state.serverMode) return;
  clearTimeout(state.settingsTimer);
  state.settingsTimer = setTimeout(() => {
    saveSettings().catch(() => {
      selectors.sampleInfo.textContent = "Nao foi possivel salvar as configuracoes automaticamente.";
    });
  }, 700);
}

async function loadSettings() {
  try {
    const response = await fetch("/api/settings");
    if (!response.ok) return;
    const settings = await response.json();
    selectors.resultPrefixes.value = settings.resultPrefixes || selectors.resultPrefixes.value;
    selectors.ignoredWords.value = settings.ignoredWords || selectors.ignoredWords.value;
    selectors.excludedPatterns.value = settings.excludedPatterns || selectors.excludedPatterns.value;
  } catch {
    // Mantem os padroes da tela se o backend nao responder.
  }
}

async function saveSettings() {
  if (!state.serverMode) return;
  clearTimeout(state.settingsTimer);

  const form = new FormData();
  form.append("resultPrefixes", selectors.resultPrefixes.value);
  form.append("ignoredWords", selectors.ignoredWords.value);
  form.append("excludedPatterns", selectors.excludedPatterns.value);

  const response = await fetch("/api/settings", {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || payload.detail || "Falha ao salvar configuracoes.");
  }
}

function buildAnalysisPayload(month) {
  const form = new FormData();
  form.append("mes", month);
  return form;
}

function buildUploadPayload(file, kind) {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("kind", kind || "ct2");
  return form;
}

function uploadFileWithProgress(file, label, kind) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/upload");

    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) {
        selectors.sampleInfo.textContent = label;
        return;
      }

      const percent = Math.round((event.loaded / event.total) * 100);
      selectors.sampleInfo.textContent = `${label} Enviando arquivo: ${percent}%.`;
      if (percent === 100) {
        renderEmpty("Arquivo enviado. Processando e atualizando a base fixa...");
      }
    });

    request.addEventListener("load", () => {
      let payload = {};
      try {
        payload = JSON.parse(request.responseText || "{}");
      } catch {
        reject(new Error("Resposta invalida do servidor."));
        return;
      }

      if (request.status >= 200 && request.status < 300) {
        resolve(payload);
      } else {
        reject(new Error(payload.error || payload.detail || "Falha ao importar o arquivo."));
      }
    });

    request.addEventListener("error", () => reject(new Error("Falha de rede durante o upload.")));
    request.addEventListener("abort", () => reject(new Error("Upload cancelado.")));
    request.send(buildUploadPayload(file, kind));
  });
}

function updateBaseSummary(base) {
  const months = base.months || [];
  setMetric("totalRows", base.total_entries || 0);
  setMetric("resultRows", base.total_entries || 0);
  setMetric("currentRows", months.length);
  setMetric("issueRows", 0);
  selectors.sampleInfo.textContent = months.length
    ? `Base fixa com ${months.length.toLocaleString("pt-BR")} meses, ${Number(base.supplier_count || 0).toLocaleString("pt-BR")} fornecedores e ${Number(base.account_count || 0).toLocaleString("pt-BR")} contas: ${months.map((item) => item.month).join(", ")}.`
    : "Base fixa vazia. Importe um CSV da CT2 para comecar.";
}

function normalizeServerIssue(row) {
  return {
    supplier: row.fornecedor_extraido,
    date: row.data_lcto,
    account: row.conta_atual,
    accountDescription: row.conta_atual_descricao || "",
    side: row.lado_resultado,
    previousAccounts: parsePreviousAccounts(row.ultimas_contas_anteriores),
    history: row.historico,
    rowNumber: row.linha_origem,
    document: row.numero_doc,
    lot: row.numero_lote,
    value: row.valor,
    debitOccurrence: row.ocorren_deb,
    creditOccurrence: row.ocorren_crd,
    resultOccurrence: row.ocorrencia_resultado,
  };
}

function parsePreviousAccounts(value) {
  if (!value || value === "Sem historico anterior") return [];
  return value.split(" | ").map((item) => {
    const match = item.match(/^(.+?)\s+\((\d{4}-\d{2}-\d{2})\)$/);
    if (!match) return { account: item, description: "", date: "", label: item };

    const [account, description = ""] = match[1].split(/\s+-\s+/, 2);
    return {
      account,
      description,
      date: match[2],
      label: match[1],
    };
  });
}

function getConfig() {
  return {
    month: selectors.monthInput.value,
    resultPrefixes: splitList(selectors.resultPrefixes.value).map(cleanAccount),
    ignoredWords: new Set(splitList(selectors.ignoredWords.value).map(normalizeText)),
    excludedPatterns: splitList(selectors.excludedPatterns.value).map(normalizeText),
    columns: {
      date: selectors.dateColumn.value,
      debit: selectors.debitColumn.value,
      credit: selectors.creditColumn.value,
      history: selectors.historyColumn.value,
    },
  };
}

function splitList(value) {
  return value.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean);
}

function buildEntries(rows, config) {
  const supplierLookup = buildSupplierLookup(rows, config);

  return rows.flatMap((row, index) => {
    if (isExcludedRow(row, config)) return [];

    const date = parseDate(row[config.columns.date]);
    const history = getHistory(row, config);
    const supplier = extractSupplier(history, config.ignoredWords, supplierLookup);
    if (!date || !supplier.key) return [];

    const candidates = [
      { side: "D", account: cleanAccount(row[config.columns.debit]), counterpart: cleanAccount(row[config.columns.credit]) },
      { side: "C", account: cleanAccount(row[config.columns.credit]), counterpart: cleanAccount(row[config.columns.debit]) },
    ];

    return candidates
      .filter((candidate) => candidate.account && isResultAccount(candidate.account, config.resultPrefixes))
      .map((candidate) => ({
        rowNumber: index + 2,
        date,
        month: date.slice(0, 7),
        side: candidate.side,
        account: candidate.account,
        counterpart: candidate.counterpart,
        supplier: supplier.label,
        supplierKey: supplier.key,
        history,
      }));
  });
}

function isExcludedRow(row, config) {
  const searchable = normalizeText([
    row["Numero Lote"],
    row["Sub Lote"],
    row["Origem"],
    getHistory(row, config),
  ].join(" "));

  return config.excludedPatterns.some((pattern) => pattern && searchable.includes(pattern));
}

function getHistory(row, config) {
  return String(row.__fullHistory || row[config.columns.history] || "");
}

function parseDate(value) {
  if (value instanceof Date && !Number.isNaN(value.valueOf())) {
    return value.toISOString().slice(0, 10);
  }

  if (typeof value === "number" && window.XLSX?.SSF) {
    const serialDate = XLSX.SSF.parse_date_code(value);
    if (serialDate) {
      return `${serialDate.y}-${String(serialDate.m).padStart(2, "0")}-${String(serialDate.d).padStart(2, "0")}`;
    }
  }

  const raw = String(value ?? "").trim();
  if (!raw) return "";

  if (/^\d{8}$/.test(raw)) {
    return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
  }

  const brazilian = raw.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})$/);
  if (brazilian) {
    const year = brazilian[3].length === 2 ? `20${brazilian[3]}` : brazilian[3];
    return `${year}-${brazilian[2].padStart(2, "0")}-${brazilian[1].padStart(2, "0")}`;
  }

  const parsed = new Date(raw);
  return Number.isNaN(parsed.valueOf()) ? "" : parsed.toISOString().slice(0, 10);
}

function cleanAccount(value) {
  return String(value ?? "").replace(/[^\d]/g, "");
}

function isResultAccount(account, prefixes) {
  return prefixes.length === 0 || prefixes.some((prefix) => account.startsWith(prefix));
}

function buildSupplierLookup(rows, config) {
  const byInvoice = new Map();
  const legalNames = [];

  rows.forEach((row) => {
    const history = getHistory(row, config);
    const invoice = extractInvoiceNumber(history);
    const supplier = extractSupplierFromHistory(history, config.ignoredWords);
    if (!supplier.key || supplier.key.length < 5) return;

    if (invoice) {
      if (!byInvoice.has(invoice)) {
        byInvoice.set(invoice, []);
      }

      const invoiceCandidates = byInvoice.get(invoice);
      if (!invoiceCandidates.some((candidate) => candidate.key === supplier.key)) {
        invoiceCandidates.push(supplier);
      }
    }

    if (hasLegalSuffix(supplier.key) && !legalNames.some((candidate) => candidate.key === supplier.key)) {
      legalNames.push(supplier);
    }
  });

  return { byInvoice, legalNames };
}

function extractSupplier(history, ignoredWords, supplierLookup = { byInvoice: new Map(), legalNames: [] }) {
  const direct = extractSupplierFromHistory(history, ignoredWords);
  const invoice = extractInvoiceNumber(history);
  const mapped = invoice ? findMappedSupplier(supplierLookup.byInvoice.get(invoice), direct.key) : null;

  if (mapped && mapped.key !== direct.key && (!direct.key || direct.key.length <= 5 || isFragmentOf(direct.key, mapped.key))) {
    return mapped;
  }

  const legalMapped = findMappedSupplier(supplierLookup.legalNames, direct.key);
  if (legalMapped && legalMapped.key !== direct.key) return legalMapped;

  return direct;
}

function findMappedSupplier(candidates, fragment) {
  if (!candidates?.length) return null;

  const usableCandidates = fragment
    ? candidates.filter((candidate) => isFragmentOf(fragment, candidate.key))
    : candidates;

  if (usableCandidates.length !== 1 && fragment) return null;
  if (usableCandidates.length === 0) return null;

  return usableCandidates
    .slice()
    .sort((a, b) => b.key.length - a.key.length)[0];
}

function extractSupplierFromHistory(history, ignoredWords) {
  const candidate = trimTechnicalSuffix(cleanSupplierCandidate(pickSupplierCandidate(history)));
  const tokens = candidate
    .split(/\s+/)
    .filter((token) => token && !ignoredWords.has(token));

  const key = trimTechnicalSuffix(tokens.join(" "));
  return {
    key,
    label: toTitleCase(key),
  };
}

function cleanSupplierCandidate(value) {
  return normalizeText(value)
    .replace(/\b\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2}\b/g, " ")
    .replace(/\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b/g, " ")
    .replace(/\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b/g, " ")
    .replace(/\b\d+[,.]?\d*\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function trimTechnicalSuffix(value) {
  return value
    .replace(/\bHIS(?:T|TORICO)?\b.*$/i, "")
    .replace(/\bENTR(?:ADA)?$/i, "")
    .replace(/\s+/g, " ")
    .trim();
}

function extractInvoiceNumber(history) {
  const match = String(history ?? "").match(/\b(?:NF|NFE|NOTA\s+FISCAL)\.?\s*([0-9./-]+)/i);
  return match ? cleanAccount(match[1]) : "";
}

function isFragmentOf(fragment, fullValue) {
  return fullValue.startsWith(fragment) || fragment.startsWith(fullValue.slice(0, fragment.length));
}

function hasLegalSuffix(value) {
  return /\b(LTDA|S A|SA|ME|EPP|EIRELI|INC|LLC)\b$/i.test(value);
}

function pickSupplierCandidate(history) {
  const raw = String(history ?? "").trim();
  const afterInvoice = raw.match(/\b(?:NF|NFE|NOTA\s+FISCAL)\.?\s*[\d./-]+\s*[-:]?\s*(.+)$/i);
  if (afterInvoice?.[1]) {
    const invoiceCandidate = afterInvoice[1];
    const afterInvoiceSupplier = invoiceCandidate.match(/\bFORN\.?\s+(.+)$/i);
    return cleanSupplierMarkerCandidate(afterInvoiceSupplier?.[1] || invoiceCandidate);
  }

  const afterSupplier = raw.match(/\bFORN\.?\s+(.+)$/i);
  if (afterSupplier?.[1]) return cleanSupplierMarkerCandidate(afterSupplier[1]);

  return "";
}

function cleanSupplierMarkerCandidate(value) {
  return String(value ?? "").split(/\s+-\s*|-\s+/)[0].trim();
}

function normalizeText(value) {
  return String(value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9 ]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toUpperCase();
}

function toTitleCase(value) {
  return value.toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function buildSupplierHistory(entries) {
  const history = new Map();

  entries
    .slice()
    .sort((a, b) => a.date.localeCompare(b.date))
    .forEach((entry) => {
      if (!history.has(entry.supplierKey)) {
        history.set(entry.supplierKey, { accounts: new Set(), lastAccounts: [] });
      }

      const supplier = history.get(entry.supplierKey);
      supplier.accounts.add(entry.account);
      supplier.lastAccounts = [
        { account: entry.account, date: entry.date },
        ...supplier.lastAccounts.filter((item) => item.account !== entry.account),
      ].slice(0, 5);
    });

  return history;
}

function renderIssues(issues) {
  const query = normalizeText(selectors.searchInput.value);
  const columnFilters = getColumnFilters();
  const filtered = sortFilteredIssues(issues.filter((issue) => {
    if (query && !normalizeText(`${issue.supplier} ${issue.date} ${issue.account} ${issue.accountDescription} ${formatPreviousAccountsText(issue.previousAccounts)} ${issue.history}`).includes(query)) {
      return false;
    }

    return Object.entries(columnFilters).every(([column, value]) => {
      if (!value) return true;
      return normalizeText(getIssueColumnValue(issue, column)).includes(value);
    });
  }));

  if (filtered.length === 0) {
    renderEmpty(issues.length === 0 ? "Nenhuma divergencia encontrada." : "Nenhuma divergencia corresponde ao filtro.");
    return;
  }

  selectors.resultsBody.innerHTML = "";
  const fragment = document.createDocumentFragment();
  filtered.forEach((issue) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(issue.supplier)}</td>
      <td>${escapeHtml(formatDate(issue.date))}</td>
      <td><strong>${escapeHtml(issue.account)}</strong> <span class="muted">${escapeHtml(issue.side)}</span></td>
      <td>${escapeHtml(issue.accountDescription)}</td>
      <td>${renderPreviousAccounts(issue.previousAccounts)}</td>
      <td>${escapeHtml(issue.history)}</td>
    `;
    fragment.append(row);
  });
  selectors.resultsBody.append(fragment);
  updateSortIndicators();
}

function getColumnFilters() {
  const filters = {};
  selectors.columnFilters.forEach((control) => {
    filters[control.dataset.filter] = normalizeText(control.value);
  });
  return filters;
}

function sortFilteredIssues(issues) {
  const direction = state.sortDirection === "desc" ? -1 : 1;
  const column = state.sortColumn;

  return issues.slice().sort((a, b) => compareIssueValues(a, b, column) * direction);
}

function compareIssueValues(a, b, column) {
  if (column === "date") return String(a.date || "").localeCompare(String(b.date || ""));
  if (column === "account") return String(a.account || "").localeCompare(String(b.account || ""), "pt-BR", { numeric: true });

  return normalizeText(getIssueColumnValue(a, column)).localeCompare(
    normalizeText(getIssueColumnValue(b, column)),
    "pt-BR",
    { numeric: true }
  );
}

function getIssueColumnValue(issue, column) {
  if (column === "previousAccountsText") return formatPreviousAccountsText(issue.previousAccounts);
  return issue[column] || "";
}

function formatPreviousAccountsText(accounts) {
  if (!accounts.length) return "Sem historico anterior";
  return accounts.map((item) => `${item.label || item.account} ${formatDate(item.date)}`).join(" | ");
}

function updateSortIndicators() {
  selectors.sortButtons.forEach((button) => {
    const active = button.dataset.sort === state.sortColumn;
    button.dataset.direction = active ? state.sortDirection : "";
    button.setAttribute("aria-sort", active ? (state.sortDirection === "asc" ? "ascending" : "descending") : "none");
  });
}

function setTableControlsEnabled(enabled) {
  selectors.columnFilters.forEach((control) => {
    control.disabled = !enabled;
  });
  selectors.sortButtons.forEach((button) => {
    button.disabled = !enabled;
  });
}

function renderPreviousAccounts(accounts) {
  if (!accounts.length) return '<span class="muted">Sem historico anterior</span>';
  return `<div class="account-list">${accounts
    .map((item) => `<span class="account-chip">${escapeHtml(item.label || item.account)} - ${escapeHtml(formatDate(item.date))}</span>`)
    .join("")}</div>`;
}

function renderEmpty(message) {
  selectors.resultsBody.innerHTML = `<tr><td colspan="6" class="empty-state">${escapeHtml(message)}</td></tr>`;
}

function exportIssues() {
  if (state.serverOutputUrl) {
    window.location.href = state.serverOutputUrl;
    return;
  }

  const header = ["fornecedor", "data", "conta_atual", "conta_atual_descricao", "lado", "ultimas_contas_anteriores", "historico"];
  const lines = state.issues.map((issue) => [
    issue.supplier,
    issue.date,
    issue.account,
    issue.accountDescription,
    issue.side,
    issue.previousAccounts.map((item) => `${item.label || item.account} (${item.date})`).join(" | "),
    issue.history,
  ]);

  const csv = [header, ...lines].map((line) => line.map(csvCell).join(";")).join("\n");
  const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "divergencias-lancamentos.csv";
  link.click();
  URL.revokeObjectURL(url);
}

function csvCell(value) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function formatDate(date) {
  const [year, month, day] = date.split("-");
  return `${day}/${month}/${year}`;
}

function setMetric(id, value) {
  selectors[id].textContent = Number(value).toLocaleString("pt-BR");
}

function formatBytes(value) {
  const size = Number(value);
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB`;
  if (size >= 1024) return `${(size / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} KB`;
  return `${size} B`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
