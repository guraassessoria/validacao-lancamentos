const state = {
  rows: [],
  columns: [],
  issues: [],
};

const selectors = {
  fileInput: document.querySelector("#fileInput"),
  fileName: document.querySelector("#fileName"),
  analyzeBtn: document.querySelector("#analyzeBtn"),
  exportBtn: document.querySelector("#exportBtn"),
  monthInput: document.querySelector("#monthInput"),
  resultPrefixes: document.querySelector("#resultPrefixes"),
  ignoredWords: document.querySelector("#ignoredWords"),
  excludedPatterns: document.querySelector("#excludedPatterns"),
  dateColumn: document.querySelector("#dateColumn"),
  debitColumn: document.querySelector("#debitColumn"),
  creditColumn: document.querySelector("#creditColumn"),
  historyColumn: document.querySelector("#historyColumn"),
  sampleInfo: document.querySelector("#sampleInfo"),
  resultsBody: document.querySelector("#resultsBody"),
  searchInput: document.querySelector("#searchInput"),
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
selectors.analyzeBtn.addEventListener("click", runAnalysis);
selectors.exportBtn.addEventListener("click", exportIssues);
selectors.searchInput.addEventListener("input", () => renderIssues(state.issues));

async function handleFile(event) {
  const [file] = event.target.files;
  if (!file) return;

  selectors.fileName.textContent = file.name;
  const extension = file.name.split(".").pop().toLowerCase();
  const rows = extension === "csv" ? await readCsv(file) : await readWorkbook(file);

  const enrichedRows = enrichContinuationHistories(rows);
  state.rows = enrichedRows;
  state.columns = collectColumns(rows);
  populateColumnSelectors(state.columns);
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
  renderIssues(state.issues);
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
  const filtered = query
    ? issues.filter((issue) => normalizeText(`${issue.supplier} ${issue.account} ${issue.history}`).includes(query))
    : issues;

  if (filtered.length === 0) {
    renderEmpty(issues.length === 0 ? "Nenhuma divergencia encontrada." : "Nenhuma divergencia corresponde ao filtro.");
    return;
  }

  selectors.resultsBody.innerHTML = "";
  filtered.forEach((issue) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${escapeHtml(issue.supplier)}</td>
      <td>${escapeHtml(formatDate(issue.date))}</td>
      <td><strong>${escapeHtml(issue.account)}</strong> <span class="muted">${issue.side}</span></td>
      <td>${renderPreviousAccounts(issue.previousAccounts)}</td>
      <td>${escapeHtml(issue.history)}</td>
    `;
    selectors.resultsBody.append(row);
  });
}

function renderPreviousAccounts(accounts) {
  if (!accounts.length) return '<span class="muted">Sem historico anterior</span>';
  return `<div class="account-list">${accounts
    .map((item) => `<span class="account-chip">${escapeHtml(item.account)} - ${escapeHtml(formatDate(item.date))}</span>`)
    .join("")}</div>`;
}

function renderEmpty(message) {
  selectors.resultsBody.innerHTML = `<tr><td colspan="5" class="empty-state">${escapeHtml(message)}</td></tr>`;
}

function exportIssues() {
  const header = ["fornecedor", "data", "conta_atual", "lado", "ultimas_contas_anteriores", "historico"];
  const lines = state.issues.map((issue) => [
    issue.supplier,
    issue.date,
    issue.account,
    issue.side,
    issue.previousAccounts.map((item) => `${item.account} (${item.date})`).join(" | "),
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

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
