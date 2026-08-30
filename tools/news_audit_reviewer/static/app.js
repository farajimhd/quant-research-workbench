const state = {
  data: null,
  filters: {},
  groupBy: [],
  groups: [],
  selectedGroup: null,
  articleData: null,
  page: 1,
  pageSize: 100,
  commentRow: null,
  saveTimers: new Map(),
};

const $ = (id) => document.getElementById(id);
const FIELD_LABELS = {
  synthesis_path: "Synthesis path", title_pattern_id: "Title pattern", normalized_title_template: "Title template",
  gold_label: "Gold label", synthesis_label: "Synthesis label", confusion_cell: "Confusion cell",
  ticker: "Ticker", channel: "Channel", provider_tag: "Provider tag", author: "Author", provider: "Provider",
  year: "Year", month: "Month", review_status: "Review status",
};

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  let payload = {};
  try { payload = await response.json(); } catch (_) { /* empty */ }
  if (!response.ok) throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  return payload;
}

function fmt(value) { return Number(value || 0).toLocaleString(); }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char])); }
function labelClass(value) { return value === "eligible" ? "eligible" : "ineligible"; }
function setSaveStatus(kind, text) { const el = $("saveStatus"); el.className = `save-status ${kind}`; el.innerHTML = `<i></i>${text}`; }

function toast(message, error = false) {
  const el = $("toast"); el.textContent = message; el.className = `toast${error ? " error" : ""}`; el.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { el.hidden = true; }, 4500);
}

function debounce(key, fn, delay = 550) {
  clearTimeout(state.saveTimers.get(key)); setSaveStatus("saving", "Persisting…");
  state.saveTimers.set(key, setTimeout(async () => {
    try { await fn(); setSaveStatus("saved", "ClickHouse ready"); }
    catch (error) { setSaveStatus("error", "Write failed"); toast(error.message, true); }
  }, delay));
}

function selectedGroupBy() {
  return [$("groupBy1").value, $("groupBy2").value, $("groupBy3").value].filter(Boolean);
}

function readFilters() {
  return {
    q: $("searchQuery").value.trim(),
    search_scope: $("fullTextSearch").checked ? "full_text" : "title",
    gold_label: $("goldFilter").value,
    synthesis_label: $("synthesisFilter").value,
    review_status: $("statusFilter").value,
    ticker: $("tickerFilter").value.trim().toUpperCase(),
    date_from: $("dateFrom").value,
    date_to: $("dateTo").value,
  };
}

function compactObject(value) {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== "" && item !== false && item != null));
}

function workspaceKey() {
  return JSON.stringify({ filters: compactObject(state.filters), group_by: state.groupBy });
}

function updateSummary(summary) {
  state.data.summary = summary;
  const percent = summary.articles ? Math.round(summary.reviewed_articles / summary.articles * 100) : 0;
  $("progressLabel").textContent = `${fmt(summary.reviewed_articles)} of ${fmt(summary.articles)} explicitly labeled`;
  $("progressPercent").textContent = `${percent}%`;
  $("progressBar").style.width = `${percent}%`;
}

function populateGroupSelects() {
  const fields = ["", ...state.data.group_fields];
  for (const [index, id] of ["groupBy1", "groupBy2", "groupBy3"].entries()) {
    const select = $(id); select.replaceChildren();
    for (const field of fields) {
      const option = document.createElement("option"); option.value = field;
      option.textContent = field ? FIELD_LABELS[field] : index === 0 ? "Choose dimension" : "No additional dimension";
      select.appendChild(option);
    }
  }
  [$("groupBy1").value, $("groupBy2").value, $("groupBy3").value] = state.data.default_group_by;
}

async function runQuery({ preserveSelection = false } = {}) {
  state.filters = compactObject(readFilters());
  state.groupBy = selectedGroupBy();
  if (!state.groupBy.length) { toast("Choose at least one grouping dimension.", true); return; }
  setSaveStatus("saving", "Querying…");
  try {
    const result = await api("/api/groups", { method: "POST", body: JSON.stringify({ filters: state.filters, group_by: state.groupBy }) });
    state.groups = result.groups;
    $("groupCount").textContent = `${fmt(result.total_groups)}${result.truncated ? "+" : ""}`;
    $("groupResultLabel").textContent = result.total_groups === 1 ? "1 group" : "Groups";
    renderGroups();
    const previousId = preserveSelection ? state.selectedGroup?.group_id : null;
    const next = state.groups.find((group) => group.group_id === previousId) || state.groups[0];
    if (next) await selectGroup(next);
    else showEmpty("No matching groups", "Change the search or filters and run the query again.");
    $("workspaceNote").value = state.data.notes[`workspace:${workspaceKey()}`] || "";
    setSaveStatus("saved", "ClickHouse ready");
  } catch (error) { setSaveStatus("error", "Query failed"); toast(error.message, true); }
}

function renderGroups() {
  const root = $("groupList"); root.replaceChildren();
  for (const group of state.groups) {
    const button = document.createElement("button"); button.type = "button";
    button.className = `group-card${state.selectedGroup?.group_id === group.group_id ? " active" : ""}`;
    const title = Object.entries(group.selection).map(([key, value]) => `${FIELD_LABELS[key]}: ${value || "(empty)"}`).join(" · ");
    const coverage = group.rows ? Math.round(group.reviewed / group.rows * 100) : 0;
    button.innerHTML = `<span class="group-card-title"></span><span class="group-card-stats"><span>${group.completed ? "✓ " : ""}${fmt(group.rows)} rows · ${fmt(group.changed)} changed</span><span class="mini-progress"><i style="width:${coverage}%"></i></span><span>${coverage}%</span></span>`;
    button.querySelector(".group-card-title").textContent = title;
    button.addEventListener("click", () => selectGroup(group)); root.appendChild(button);
  }
}

async function selectGroup(group) {
  state.selectedGroup = group; state.page = 1; state.commentRow = null; renderGroups();
  await loadArticles();
}

async function loadArticles() {
  if (!state.selectedGroup) return;
  setSaveStatus("saving", "Loading group…");
  try {
    state.articleData = await api("/api/articles", {
      method: "POST",
      body: JSON.stringify({ filters: state.filters, selection: state.selectedGroup.selection, page: state.page, page_size: state.pageSize }),
    });
    renderReview(); setSaveStatus("saved", "ClickHouse ready");
  } catch (error) { setSaveStatus("error", "Query failed"); toast(error.message, true); }
}

function showEmpty(title, copy) {
  $("reviewPanel").hidden = true; $("emptyState").hidden = false;
  $("emptyState").querySelector("h2").textContent = title; $("emptyState").querySelector("p").textContent = copy;
}

function renderReview() {
  const data = state.articleData, group = state.selectedGroup;
  $("emptyState").hidden = true; $("reviewPanel").hidden = false;
  const entries = Object.entries(group.selection);
  $("selectionChips").innerHTML = entries.map(([key, value]) => `<span class="selection-chip">${escapeHtml(FIELD_LABELS[key])}: ${escapeHtml(value || "(empty)")}</span>`).join("");
  $("groupTitle").textContent = entries.map(([, value]) => value || "(empty)").join(" · ");
  $("groupMeta").textContent = `${fmt(data.summary.rows)} articles · ${fmt(data.summary.reviewed)} labeled · ${fmt(data.summary.changed)} changed from gold`;
  $("queryDescription").textContent = `${state.groupBy.map((field) => FIELD_LABELS[field]).join(" → ")} grouping`;
  $("groupCoverage").textContent = `${fmt(data.summary.reviewed)} / ${fmt(data.summary.rows)} labeled`;
  $("articleCount").textContent = fmt(data.summary.rows);
  renderExplanation(data.context); renderDecision(); renderRows(); renderPagination();
  $("groupNote").value = state.data.notes[`group:${data.group_id}`] || data.group_state.note || "";
}

function renderExplanation(context) {
  const root = $("explanationBody"); root.replaceChildren();
  const list = document.createElement("ul"); list.className = "reason-list";
  const reasons = [...context.path_reasons, context.pattern_reason].filter(Boolean);
  if (!reasons.length) reasons.push("This custom query crosses multiple synthesis paths; inspect the path and pattern columns per article.");
  for (const text of reasons) { const item = document.createElement("li"); item.textContent = text; list.appendChild(item); }
  root.appendChild(list);
  const tags = document.createElement("div"); tags.className = "reason-tags";
  for (const entry of context.decision_reasons) { const tag = document.createElement("span"); tag.className = "reason-tag"; tag.textContent = `${entry.value} · ${fmt(entry.count)}`; tags.appendChild(tag); }
  root.appendChild(tags);
}

function renderDecision() {
  const disposition = state.articleData.group_state.disposition || "";
  document.querySelectorAll(".decision-option").forEach((button) => button.classList.toggle("selected", button.dataset.decision === disposition));
  const complete = $("completeButton"), completed = Boolean(Number(state.articleData.group_state.completed));
  complete.disabled = !disposition;
  complete.classList.toggle("is-complete", completed);
  complete.innerHTML = completed ? "<span>✓</span> Group complete" : "<span>✓</span> Mark group complete";
  $("decisionHelp").textContent = completed
    ? "This exact query-defined group is complete. Later label changes remain in history."
    : disposition === "mixed"
      ? "Mixed mode: persist each article label below, then complete the group."
      : disposition
        ? "Bulk labels are already persisted. Mark the group complete when satisfied."
        : "Choose a group disposition or label articles individually.";
}

function renderRows() {
  const root = $("articleRows"); root.replaceChildren();
  for (const row of state.articleData.rows) {
    const tr = document.createElement("tr");
    const tickers = (row.tickers || []).map((ticker) => `<span class="ticker-token">${escapeHtml(ticker)}</span>`).join("") || "—";
    tr.innerHTML = `
      <td class="date-cell">${formatDate(row.published_at_utc)}</td><td class="ticker-cell">${tickers}</td>
      <td><span class="label-stack"><span class="label-pill ${labelClass(row.gold_label)}">Gold ${escapeHtml(row.gold_label)}</span><span class="synthesis-mini">Synthesis ${escapeHtml(row.synthesis_label)}</span></span></td>
      <td><button class="article-title-button" type="button"></button><span class="template-line"></span><span class="source-id">${escapeHtml(row.source_id)} · ${escapeHtml(row.title_pattern_id)}</span></td>
      <td><div class="operator-toggle"><button type="button" data-label="eligible" class="eligible ${row.operator_label === "eligible" ? "active" : ""}">Eligible</button><button type="button" data-label="ineligible" class="ineligible ${row.operator_label === "ineligible" ? "active" : ""}">Ineligible</button></div><button class="clear-label" type="button">${row.operator_label ? "Clear your label" : "Uses gold until labeled"}</button></td>
      <td><button class="comment-button ${row.article_comment ? "has-comment" : ""}" type="button">${row.article_comment ? "Edit" : "+ Note"}</button></td>`;
    tr.querySelector(".article-title-button").textContent = row.title;
    tr.querySelector(".template-line").textContent = row.normalized_title_template || "";
    tr.querySelector(".article-title-button").addEventListener("click", () => openArticle(row.source_id));
    tr.querySelectorAll(".operator-toggle button").forEach((button) => button.addEventListener("click", () => saveArticle(row, button.dataset.label, row.article_comment || "")));
    tr.querySelector(".clear-label").addEventListener("click", () => saveArticle(row, "", row.article_comment || ""));
    tr.querySelector(".comment-button").addEventListener("click", () => { state.commentRow = state.commentRow === row.source_id ? null : row.source_id; renderRows(); });
    root.appendChild(tr);
    if (state.commentRow === row.source_id) {
      const noteRow = document.createElement("tr"); noteRow.className = "comment-row";
      noteRow.innerHTML = `<td colspan="6"><textarea class="row-comment" placeholder="Article-specific reasoning or follow-up note…"></textarea></td>`;
      const textarea = noteRow.querySelector("textarea"); textarea.value = row.article_comment || "";
      textarea.addEventListener("input", () => debounce(`article-note-${row.source_id}`, () => saveArticle(row, row.operator_label || "", textarea.value, false)));
      root.appendChild(noteRow); setTimeout(() => textarea.focus(), 0);
    }
  }
}

async function saveArticle(row, label, comment, rerender = true) {
  setSaveStatus("saving", "Persisting label…");
  try {
    const oldLabel = row.operator_label || "";
    const wasChanged = Boolean(oldLabel) && oldLabel !== row.gold_label;
    const result = await api(`/api/articles/${row.source_id}/label`, { method: "PUT", body: JSON.stringify({ operator_label: label, comment }) });
    row.operator_label = result.decision.operator_label; row.article_comment = result.decision.note;
    updateSummary(result.summary);
    state.articleData.summary.reviewed += label && !oldLabel ? 1 : !label && oldLabel ? -1 : 0;
    const isChanged = Boolean(label) && label !== row.gold_label;
    state.articleData.summary.changed += isChanged && !wasChanged ? 1 : !isChanged && wasChanged ? -1 : 0;
    if (rerender) renderRows();
    setSaveStatus("saved", "ClickHouse ready");
  } catch (error) { setSaveStatus("error", "Write failed"); toast(error.message, true); await loadArticles(); }
}

async function chooseDisposition(disposition) {
  if (!state.articleData) return;
  const label = disposition === "all_eligible" ? "eligible" : disposition === "all_ineligible" ? "ineligible" : "";
  if (label && !confirm(`Persist ${label} for all ${fmt(state.articleData.summary.rows)} articles in this exact query group?`)) return;
  setSaveStatus("saving", label ? "Writing bulk labels…" : "Saving mixed group…");
  try {
    const result = await api("/api/group", {
      method: "PUT",
      body: JSON.stringify({ filters: state.filters, selection: state.selectedGroup.selection, disposition, completed: Boolean(label), note: $("groupNote").value, apply_label: label }),
    });
    updateSummary(result.summary); toast(label ? `${fmt(result.matched_rows)} labels persisted to ClickHouse` : "Mixed group saved");
    await runQuery({ preserveSelection: true });
  } catch (error) { setSaveStatus("error", "Write failed"); toast(error.message, true); }
}

async function toggleComplete() {
  const groupState = state.articleData?.group_state; if (!groupState?.disposition) return;
  const completed = !Boolean(Number(groupState.completed));
  try {
    const result = await api("/api/group", {
      method: "PUT",
      body: JSON.stringify({ filters: state.filters, selection: state.selectedGroup.selection, disposition: groupState.disposition, completed, note: $("groupNote").value }),
    });
    state.articleData.group_state = result; renderDecision(); toast(completed ? "Group marked complete" : "Group reopened");
    if (completed) setTimeout(nextUnreviewedGroup, 250);
  } catch (error) { toast(error.message, true); }
}

function nextUnreviewedGroup() {
  if (!state.groups.length) return;
  const current = Math.max(0, state.groups.findIndex((group) => group.group_id === state.selectedGroup?.group_id));
  for (let offset = 1; offset <= state.groups.length; offset += 1) {
    const candidate = state.groups[(current + offset) % state.groups.length];
    if (!candidate.completed || candidate.reviewed < candidate.rows) { selectGroup(candidate); return; }
  }
  toast("Every article in the current query has an explicit operator label.");
}

async function openArticle(sourceId) {
  try {
    const detail = await api(`/api/articles/${sourceId}`);
    $("detailTitle").textContent = detail.title;
    $("detailMeta").innerHTML = [detail.published_at_utc, ...(detail.tickers || []), detail.author, detail.synthesis_path, detail.title_pattern_id].filter(Boolean).map((value) => `<span>${escapeHtml(value)}</span>`).join("");
    $("detailText").textContent = detail.rendered_text || detail.teaser || detail.title;
    $("articleDrawer").hidden = false;
  } catch (error) { toast(error.message, true); }
}

function renderPagination() {
  const data = state.articleData, total = data.summary.rows;
  $("pageSummary").textContent = total ? `Showing ${fmt((data.page - 1) * data.page_size + 1)}–${fmt(Math.min(data.page * data.page_size, total))} of ${fmt(total)}` : "No articles";
  $("pageLabel").textContent = `Page ${data.page} of ${data.total_pages}`;
  $("previousPage").disabled = data.page <= 1; $("nextPage").disabled = data.page >= data.total_pages;
}

function formatDate(value) { const text = String(value || ""); return text.length >= 10 ? `${text.slice(0, 10)}<br>${text.slice(11, 16)} UTC` : escapeHtml(text); }

async function saveNote(scopeType, scopeKey, note) {
  await api("/api/notes", { method: "PUT", body: JSON.stringify({ scope_type: scopeType, scope_key: scopeKey, note }) });
  state.data.notes[`${scopeType}:${scopeKey}`] = note;
}

function resetFilters() {
  for (const id of ["searchQuery", "tickerFilter", "dateFrom", "dateTo"]) $(id).value = "";
  for (const id of ["goldFilter", "synthesisFilter", "statusFilter"]) $(id).value = "";
  $("fullTextSearch").checked = false;
  [$("groupBy1").value, $("groupBy2").value, $("groupBy3").value] = state.data.default_group_by;
  runQuery();
}

function bindEvents() {
  $("runQuery").addEventListener("click", () => runQuery()); $("resetFilters").addEventListener("click", resetFilters);
  $("searchQuery").addEventListener("keydown", (event) => { if (event.key === "Enter") runQuery(); });
  document.querySelectorAll(".decision-option").forEach((button) => button.addEventListener("click", () => chooseDisposition(button.dataset.decision)));
  $("completeButton").addEventListener("click", toggleComplete); $("nextGroup").addEventListener("click", nextUnreviewedGroup);
  $("previousPage").addEventListener("click", async () => { state.page -= 1; await loadArticles(); });
  $("nextPage").addEventListener("click", async () => { state.page += 1; await loadArticles(); });
  $("groupNote").addEventListener("input", () => { if (state.articleData) debounce("group-note", () => saveNote("group", state.articleData.group_id, $("groupNote").value)); });
  $("notesButton").addEventListener("click", () => { $("campaignNote").value = state.data.notes["campaign:campaign"] || ""; $("workspaceNote").value = state.data.notes[`workspace:${workspaceKey()}`] || ""; $("notesDrawer").hidden = false; });
  $("closeNotes").addEventListener("click", () => { $("notesDrawer").hidden = true; });
  $("notesDrawer").addEventListener("click", (event) => { if (event.target === $("notesDrawer")) $("notesDrawer").hidden = true; });
  $("campaignNote").addEventListener("input", () => debounce("campaign-note", () => saveNote("campaign", "campaign", $("campaignNote").value)));
  $("workspaceNote").addEventListener("input", () => debounce("workspace-note", () => saveNote("workspace", workspaceKey(), $("workspaceNote").value)));
  $("closeArticle").addEventListener("click", () => { $("articleDrawer").hidden = true; });
  $("articleDrawer").addEventListener("click", (event) => { if (event.target === $("articleDrawer")) $("articleDrawer").hidden = true; });
  $("copyGroupButton").addEventListener("click", async () => { if (!state.articleData) return; await navigator.clipboard.writeText(JSON.stringify(state.articleData.group_spec, null, 2)); toast("Group query copied"); });
  $("themeButton").addEventListener("click", () => { const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; document.documentElement.dataset.theme = next; localStorage.setItem("news-label-theme", next); });
  document.addEventListener("keydown", (event) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
    if (event.key.toLowerCase() === "e") chooseDisposition("all_eligible");
    if (event.key.toLowerCase() === "i") chooseDisposition("all_ineligible");
    if (event.key.toLowerCase() === "m") chooseDisposition("mixed");
    if (event.key.toLowerCase() === "j") nextUnreviewedGroup();
  });
}

async function initialize() {
  try {
    const theme = localStorage.getItem("news-label-theme"); if (theme) document.documentElement.dataset.theme = theme;
    state.data = await api("/api/bootstrap"); $("scopeArticles").textContent = fmt(state.data.audit.articles);
    updateSummary(state.data.summary); populateGroupSelects(); bindEvents();
    $("loading").hidden = true; $("app").hidden = false; await runQuery();
  } catch (error) {
    $("loading").innerHTML = `<div class="loading-mark">!</div><h2>Reviewer could not start</h2><p>${escapeHtml(error.message)}</p>`;
  }
}

initialize();
