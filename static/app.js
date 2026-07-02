const controls = {
  tool: document.querySelector("#toolSelect"),
  from: document.querySelector("#fromDate"),
  to: document.querySelector("#toDate"),
  project: document.querySelector("#projectSelect"),
  pageSize: document.querySelector("#pageSize"),
  prevPage: document.querySelector("#prevPage"),
  nextPage: document.querySelector("#nextPage"),
};

initDatePicker(controls.from);
initDatePicker(controls.to);

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
let selectedHeatmapDate = null;
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
    .filter((row) => row.period_type === "daily"
      && (!selectedHeatmapDate || row.period === selectedHeatmapDate))
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
    renderHeatmap();
    setStatus(`Updated ${payload.meta.generated_at}`);
  } catch (err) {
    allRows = [];
    selectedHeatmapDate = null;
    renderTable();
    renderHeatmap();
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
      <td>${row.tool === "claude" ? "Claude Code" : row.tool === "codex" ? "Codex" : row.tool === "copilot" ? "GitHub Copilot" : row.tool === "opencode" ? "OpenCode" : row.tool}</td>
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

/* ── Heatmap ──────────────────────────────────────────── */

function computeHeatmapLevels(values) {
  const sorted = values.filter(v => v > 0).sort((a, b) => a - b);
  if (sorted.length === 0) return () => 0;

  const len = sorted.length;
  const q1 = sorted[Math.min(Math.floor(len * 0.25), len - 1)];
  const q2 = sorted[Math.min(Math.floor(len * 0.50), len - 1)];
  const q3 = sorted[Math.min(Math.floor(len * 0.75), len - 1)];

  return (value) => {
    if (value <= 0) return 0;
    if (value <= q1) return 1;
    if (value <= q2) return 2;
    if (value <= q3) return 3;
    return 4;
  };
}

function renderHeatmap() {
  const loadingEl = document.getElementById("heatmapLoading");
  const emptyEl = document.getElementById("heatmapEmpty");
  const contentEl = document.getElementById("heatmapContent");
  const monthsEl = document.getElementById("heatmapMonths");
  const colsEl = document.getElementById("heatmapCols");

  if (!loadingEl || !contentEl) return;

  const metric = "total_tokens";
  const dailyRows = allRows.filter(r => r.period_type === "daily");

  // Build metric map
  const dataByDate = new Map();
  for (const row of dailyRows) {
    const val = Number(row[metric] || 0);
    const key = row.period;
    dataByDate.set(key, (dataByDate.get(key) || 0) + val);
  }

  // Always show the grid (even if no data — all cells level-0)
  loadingEl.hidden = true;
  emptyEl.hidden = true;
  contentEl.hidden = false;

  // Quantile thresholds from data
  const getLevel = computeHeatmapLevels([...dataByDate.values()]);

  // ── Date grid: 53 columns, last cell = today ──────────
  const today = new Date();
  // Start from ~53 weeks before today, snapped to Sunday
  const start = new Date(today);
  start.setDate(start.getDate() - 52 * 7 - start.getDay()); // Sunday of 53 weeks ago
  const cursor = new Date(start);

  // Build 53 weeks of grid data
  const weeks = [];
  const monthLabels = [];
  const seenMonths = new Set();

  for (let wi = 0; wi < 53; wi++) {
    const cells = [];
    for (let dow = 0; dow < 7; dow++) {
      const dateStr = fmtDate(cursor);
      const isFuture = dateStr > fmtDate(today);
      const value = isFuture ? 0 : (dataByDate.get(dateStr) || 0);
      const level = isFuture ? 0 : getLevel(value);
      cells.push({ date: dateStr, value, level, isFuture });

      // Month label: at the column containing the 1st of the month
      if (cursor.getDate() === 1 && !seenMonths.has(cursor.getMonth())) {
        seenMonths.add(cursor.getMonth());
        monthLabels.push({ col: wi, name: MONTHS[cursor.getMonth()].slice(0, 3) });
      }

      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push(cells);
  }

  // ── Fluid cell size ──────────────────────────────────
  const gridRow = colsEl.closest(".heatmap-grid-row");
  const available = gridRow ? gridRow.clientWidth - 36 - 4 : 600; // minus y-labels area + buffer
  const gap = 3;
  const cellSize = Math.max(10, Math.floor((available - 52 * gap) / 53));
  const step = cellSize + gap;

  // Cell height slightly smaller than width, with padding to maintain total height
  const cellHeight = Math.max(6, cellSize - 2);
  const colPad = Math.floor((cellSize - cellHeight) * 7 / 2);

  // Set dynamic CSS variables on common ancestor so y-labels + cells both inherit
  contentEl.style.setProperty("--heatmap-cell-size", cellSize + "px");
  contentEl.style.setProperty("--heatmap-cell-height", cellHeight + "px");
  contentEl.style.setProperty("--heatmap-col-padding", colPad + "px");
  contentEl.style.setProperty("--heatmap-cell-gap", gap + "px");

  // Render month labels
  monthsEl.innerHTML = "";
  for (const ml of monthLabels) {
    const span = document.createElement("span");
    span.className = "heatmap-month-label";
    span.textContent = ml.name;
    span.style.left = (ml.col * step) + "px";
    monthsEl.appendChild(span);
  }

  // Render columns
  colsEl.innerHTML = "";
  for (let wi = 0; wi < weeks.length; wi++) {
    const col = document.createElement("div");
    col.className = "heatmap-col";
    for (const cell of weeks[wi]) {
      const el = document.createElement("div");
      el.className = "heatmap-cell" + (cell.isFuture ? " heatmap-cell-future" : " level-" + cell.level);
      el.dataset.date = cell.date;
      el.dataset.value = String(cell.value);
      el.tabIndex = 0;
      el.setAttribute("role", "gridcell");
      el.setAttribute("aria-label", cell.date + ": " + fmt(cell.value) + " total");
      if (selectedHeatmapDate === cell.date) el.classList.add("selected");
      col.appendChild(el);
    }
    colsEl.appendChild(col);
  }
}

function parseLocalDate(str) {
  const m = str.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3]);
}

/* ── Heatmap tooltip ─────────────────────────────────── */

function showHeatmapTooltip(date, value) {
  const tip = document.getElementById("heatmapTooltip");
  if (!tip) return;
  tip.textContent = date + ": " + fmt(Number(value)) + " Total";
  tip.hidden = false;
}

function positionHeatmapTooltip(e) {
  const tip = document.getElementById("heatmapTooltip");
  if (!tip || tip.hidden) return;
  const x = Math.min(e.clientX + 12, window.innerWidth - tip.offsetWidth - 8);
  const y = e.clientY + 12;
  tip.style.left = Math.max(4, x) + "px";
  tip.style.top = y + "px";
}

function hideHeatmapTooltip() {
  const tip = document.getElementById("heatmapTooltip");
  if (tip) tip.hidden = true;
}

/* ── Heatmap event delegation ────────────────────────── */

(function attachHeatmapEvents() {
  const cols = document.getElementById("heatmapCols");
  if (!cols) return;

  cols.addEventListener("mouseover", (e) => {
    const cell = e.target.closest(".heatmap-cell");
    if (!cell) { hideHeatmapTooltip(); return; }
    showHeatmapTooltip(cell.dataset.date, cell.dataset.value);
  });

  cols.addEventListener("mousemove", positionHeatmapTooltip);

  cols.addEventListener("mouseout", (e) => {
    if (e.target.closest(".heatmap-cell")) hideHeatmapTooltip();
  });

  cols.addEventListener("click", (e) => {
    const cell = e.target.closest(".heatmap-cell");
    if (!cell) return;
    const date = cell.dataset.date;
    if (selectedHeatmapDate === date) {
      selectedHeatmapDate = null;
    } else {
      selectedHeatmapDate = date;
    }
    document.querySelectorAll(".heatmap-cell.selected").forEach(el => el.classList.remove("selected"));
    if (selectedHeatmapDate) {
      document.querySelectorAll('.heatmap-cell[data-date="' + selectedHeatmapDate + '"]')
        .forEach(el => el.classList.add("selected"));
    }
    currentPage = 1;
    renderTable();
  });

  cols.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.closest(".heatmap-cell")) {
      e.preventDefault();
      e.target.click();
    }
  });
})();

/* ── Date picker ───────────────────────────────────── */

const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
const WEEKDAYS = ["Su","Mo","Tu","We","Th","Fr","Sa"];

function initDatePicker(input) {
  let popup = null;
  let viewYear, viewMonth; // the calendar view (year/month being displayed)
  let closeTimer = null;

  function parseValue() {
    const v = input.value;
    if (!v) return new Date();
    const m = v.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
    return new Date();
  }

  function build() {
    const d = parseValue();
    viewYear = d.getFullYear();
    viewMonth = d.getMonth();

    popup = document.createElement("div");
    popup.className = "date-picker-popup";
    popup.addEventListener("mousedown", (e) => e.preventDefault()); // prevent input blur

    render();
    document.body.appendChild(popup);
    position();
  }

  function render() {
    const firstDay = new Date(viewYear, viewMonth, 1).getDay();
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const daysInPrev = new Date(viewYear, viewMonth, 0).getDate();

    const today = new Date();
    const todayStr = fmtDate(today);
    const selStr = input.value;

    // Header
    const header = document.createElement("div");
    header.className = "dp-header";

    const prevBtn = document.createElement("button");
    prevBtn.className = "dp-nav";
    prevBtn.textContent = "‹";
    prevBtn.setAttribute("aria-label", "Previous month");

    const title = document.createElement("span");
    title.className = "dp-title";
    title.textContent = MONTHS[viewMonth] + " " + viewYear;

    const nextBtn = document.createElement("button");
    nextBtn.className = "dp-nav";
    nextBtn.textContent = "›";
    nextBtn.setAttribute("aria-label", "Next month");

    header.append(prevBtn, title, nextBtn);

    popup.innerHTML = "";
    popup.appendChild(header);

    // Weekday row
    const wdRow = document.createElement("div");
    wdRow.className = "dp-weekdays";
    for (const wd of WEEKDAYS) {
      const el = document.createElement("span");
      el.className = "dp-weekday";
      el.textContent = wd;
      wdRow.appendChild(el);
    }
    popup.appendChild(wdRow);

    // Day grid
    const grid = document.createElement("div");
    grid.className = "dp-days";

    // Previous month's trailing days
    const padStart = firstDay;
    for (let i = padStart - 1; i >= 0; i--) {
      const day = daysInPrev - i;
      const el = document.createElement("span");
      el.className = "dp-day dp-other";
      el.textContent = day;
      grid.appendChild(el);
    }

    // Current month days
    for (let d = 1; d <= daysInMonth; d++) {
      const dateStr = fmtDate(new Date(viewYear, viewMonth, d));
      const el = document.createElement("span");
      el.className = "dp-day";
      if (dateStr === todayStr) el.classList.add("dp-today");
      if (dateStr === selStr) el.classList.add("dp-selected");
      el.textContent = d;
      el.dataset.date = dateStr;
      el.addEventListener("click", () => select(dateStr));
      grid.appendChild(el);
    }

    // Next month's leading days (to fill 42 cells = 6 rows)
    const totalCells = padStart + daysInMonth;
    const remaining = 42 - totalCells;
    for (let d = 1; d <= remaining; d++) {
      const el = document.createElement("span");
      el.className = "dp-day dp-other";
      el.textContent = d;
      grid.appendChild(el);
    }

    popup.appendChild(grid);

    // Footer with clear button
    const footer = document.createElement("div");
    footer.className = "dp-footer";
    const clearBtn = document.createElement("button");
    clearBtn.textContent = "Clear";
    clearBtn.className = "dp-clear";
    clearBtn.addEventListener("click", (e) => { e.stopPropagation(); clear(); render(); });
    footer.appendChild(clearBtn);
    popup.appendChild(footer);

    // Nav handlers (rebound after innerHTML wipe)
    prevBtn.addEventListener("click", () => { viewMonth--; if (viewMonth < 0) { viewMonth = 11; viewYear--; } render(); });
    nextBtn.addEventListener("click", () => { viewMonth++; if (viewMonth > 11) { viewMonth = 0; viewYear++; } render(); });
  }

  function position() {
    const rect = input.getBoundingClientRect();
    popup.style.left = rect.left + "px";
    popup.style.top = (rect.bottom + 4) + "px";
    // Keep within viewport
    const pw = popup.offsetWidth;
    if (rect.left + pw > window.innerWidth) {
      popup.style.left = (window.innerWidth - pw - 8) + "px";
    }
  }

  function clear() {
    input.value = "";
    close();
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function select(dateStr) {
    input.value = dateStr;
    close();
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function open() {
    if (popup) close();
    build();
  }

  function close() {
    if (popup) { popup.remove(); popup = null; }
  }

  input.addEventListener("focus", () => {
    if (closeTimer) clearTimeout(closeTimer);
    open();
  });

  input.addEventListener("blur", () => {
    closeTimer = setTimeout(() => {
      if (!popup || !popup.matches(":hover")) close();
    }, 180);
  });

  // Close on scroll / resize (reposition wouldn't work well)
  window.addEventListener("scroll", () => { if (popup) close(); }, true);
  window.addEventListener("resize", () => { if (popup) close(); });
}

function fmtDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return y + "-" + m + "-" + day;
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
  selectedHeatmapDate = null;
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

// Reflow heatmap on resize
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(renderHeatmap, 200);
});
