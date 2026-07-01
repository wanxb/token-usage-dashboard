const controls = {
  tool: document.querySelector("#toolSelect"),
  from: document.querySelector("#fromDate"),
  to: document.querySelector("#toDate"),
  project: document.querySelector("#projectSelect"),
  pageSize: document.querySelector("#pageSize"),
  prevPage: document.querySelector("#prevPage"),
  nextPage: document.querySelector("#nextPage"),
};

const els = {
  body: document.querySelector("#usageBody"),
  status: document.querySelector("#status"),
  empty: document.querySelector("#emptyState"),
  meta: document.querySelector("#metaLine"),
  totalTokens: document.querySelector("#totalTokens"),
  requests: document.querySelector("#requests"),
  inputTokens: document.querySelector("#inputTokens"),
  outputTokens: document.querySelector("#outputTokens"),
  cachedTokens: document.querySelector("#cachedTokens"),
  reasoningTokens: document.querySelector("#reasoningTokens"),

  pageStatus: document.querySelector("#pageStatus"),
};

let allRows = [];
let currentPage = 1;
let pageSize = Number(controls.pageSize.value || 10);
const numberFormat = new Intl.NumberFormat("en-US");

function params() {
  const query = new URLSearchParams();
  query.set("tool", controls.tool.value);
  query.set("project", controls.project.value);
  if (controls.from.value) query.set("from", controls.from.value);
  if (controls.to.value) query.set("to", controls.to.value);
  return query;
}

function fmt(value) {
  return numberFormat.format(Number(value || 0));
}

function setStatus(text) {
  els.status.textContent = text;
}

function compareRows(a, b) {
  return String(b.period).localeCompare(String(a.period));
}

function visibleRows() {
  const rows = allRows
    .filter((row) => row.period_type === "daily")
    .slice()
    .sort(compareRows);

  const totalRows = rows.length;
  const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
  currentPage = Math.min(Math.max(currentPage, 1), totalPages);
  const startIndex = (currentPage - 1) * pageSize;
  const pageRows = rows.slice(startIndex, startIndex + pageSize);
  const endIndex = Math.min(startIndex + pageSize, totalRows);

  return { rows: pageRows, totalRows, totalPages, startIndex, endIndex };
}

async function loadData() {
  const query = params();
  setStatus("Loading...");
  const prevProject = controls.project.value;
  try {
    const res = await fetch(`/api/usage?${query.toString()}`, { cache: "no-store" });
    const payload = await res.json();
    if (!res.ok) throw new Error(payload.error || "Request failed");
    allRows = payload.rows || [];
    populateProjects(payload.meta?.projects || [], prevProject);
    renderSummary(payload);
    renderTable();
    setStatus(`Updated ${payload.meta.generated_at}`);
  } catch (err) {
    allRows = [];
    renderTable();
    setStatus(err.message || "Failed to load");
  }
}

function populateProjects(projects, selectedValue) {
  const select = controls.project;
  const current = selectedValue || select.value;
  select.innerHTML = '<option value="all">All</option>';
  for (const name of projects) {
    if (!name) continue;
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
  if (Array.from(select.options).some(o => o.value === current)) {
    select.value = current;
  } else {
    select.value = "all";
  }
}

function renderSummary(payload) {
  const totals = payload.totals || {};
  els.totalTokens.textContent = fmt(totals.total_tokens);
  els.requests.textContent = fmt(totals.requests);
  els.inputTokens.textContent = fmt(totals.input_tokens);
  els.outputTokens.textContent = fmt(totals.output_tokens);
  els.cachedTokens.textContent = fmt((totals.cached_tokens || 0) + (totals.cache_read_input_tokens || 0));
  els.reasoningTokens.textContent = fmt(totals.reasoning_tokens);
  const meta = payload.meta || {};
  els.meta.textContent = `${fmt(meta.event_count)} events · ${meta.timezone || "-"}`;
}

function renderTable() {
  const { rows, totalRows, totalPages, startIndex, endIndex } = visibleRows();
  els.body.innerHTML = rows.map((row) => `
    <tr>
      <td>${row.tool === "claude" ? "Claude Code" : row.tool === "codex" ? "Codex" : row.tool}</td>
      <td>${row.project || "—"}</td>
      <td>${row.period}</td>
      <td class="num">${fmt(row.requests)}</td>
      <td class="num">${fmt(row.input_tokens)}</td>
      <td class="num">${fmt(row.output_tokens)}</td>
      <td class="num">${fmt(row.cache_creation_input_tokens)}</td>
      <td class="num">${fmt(row.cache_read_input_tokens)}</td>
      <td class="num">${fmt(row.cached_tokens)}</td>
      <td class="num">${fmt(row.reasoning_tokens)}</td>
      <td class="num"><strong>${fmt(row.total_tokens)}</strong></td>
    </tr>
  `).join("");

  els.empty.hidden = rows.length > 0;
  els.pageStatus.textContent = totalRows > 0
    ? `Showing ${fmt(startIndex + 1)}–${fmt(endIndex)} of ${fmt(totalRows)}`
    : "No records";
  controls.prevPage.disabled = currentPage <= 1 || totalRows === 0;
  controls.nextPage.disabled = currentPage >= totalPages || totalRows === 0;
}

function setDefaultDates() {
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  controls.to.value = `${yyyy}-${mm}-${dd}`;
}

function resetAndLoad() {
  currentPage = 1;
  loadData();
}

Object.values(controls).forEach((control) => {
  if (!control || control === controls.prevPage || control === controls.nextPage || control === controls.pageSize) return;
  control.addEventListener("change", resetAndLoad);
});

controls.pageSize.addEventListener("change", () => {
  pageSize = Number(controls.pageSize.value || 10);
  currentPage = 1;
  renderTable();
});

controls.prevPage.addEventListener("click", () => {
  if (currentPage > 1) {
    currentPage -= 1;
    renderTable();
  }
});
controls.nextPage.addEventListener("click", () => {
  const totalRows = allRows.filter((row) => row.period_type === "daily").length;
  const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
  if (currentPage < totalPages) {
    currentPage += 1;
    renderTable();
  }
});

setDefaultDates();
loadData();
