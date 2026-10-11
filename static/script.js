/**
 * Semantic SEO Tool — Frontend Logic
 * ====================================
 * - Professional loading states with step progress
 * - Typed error handling with clear user messages
 * - Pydantic-aligned data rendering
 * - Semantic HTML reports with native Word/Docs copy and export
 */

"use strict";

let _excelUrl  = "";
let _docxUrl   = "";
let _briefText = "";
let _briefHtml = "";
let _serpBriefText = "";
let _serpBriefHtml = "";
let _evidenceToken = "";
let _lastAnalysisData = null;
let _activeAbortController = null;
let _activeJobId = "";
let _activeCancelToken = "";
let _activeMode = "entity";
let _lastFocusedElement = null;
let _appMeta = null;
let _lastRequestId = "";
let _analysisWatchdog = null;
let _progressAnimationFrame = null;
let _progressDisplay = 0;
let _progressTarget = 0;
let _progressCeiling = 0;
let _progressUpdatedAt = 0;
let _progressFrameAt = 0;
let _loadingStartedAt = 0;
let _loadingClock = null;
let _cumulativeSessionCost = 0;
let _cumulativeSessionTokens = 0;

const liveEditorState = {
  analysisItems: [],
  evaluation: null,
  originalContent: "",
  draftKey: "",
  loadNonce: 0,
  undoStack: [],
  search: "",
  filter: "missing",
  saveTimer: null,
};

function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}

function readSessionValue(key) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSessionValue(key, value) {
  try {
    sessionStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function updateCostTracker(badgeId, textId, usage) {
  const badge = document.getElementById(badgeId);
  const text = document.getElementById(textId);
  if (!badge || !text) return;

  if (!usage || (usage.total_tokens === 0 && usage.cost_usd === 0)) {
    badge.style.display = "none";
    return;
  }

  const jobCost = typeof usage.cost_usd === "number" ? usage.cost_usd : 0;
  const jobTokens = typeof usage.total_tokens === "number" ? usage.total_tokens : 0;

  _cumulativeSessionCost += jobCost;
  _cumulativeSessionTokens += jobTokens;

  const costStr = jobCost < 0.01 && jobCost > 0 ? jobCost.toFixed(4) : jobCost.toFixed(3);
  const totalCostStr = _cumulativeSessionCost < 0.01 && _cumulativeSessionCost > 0 ? _cumulativeSessionCost.toFixed(4) : _cumulativeSessionCost.toFixed(3);
  const tokensStr = jobTokens.toLocaleString();

  const tpl = t("cost_tracker_badge") || "⚡ التكلفة التقديرية: ${cost} | التوكنات: {tokens}";
  let rendered = tpl.replace("{cost}", costStr).replace("{tokens}", tokensStr);

  if (_cumulativeSessionCost > jobCost + 0.0001) {
    const sessionTpl = t("cost_tracker_session") || "إجمالي الجلسة: ${cost}";
    rendered += ` · (${sessionTpl.replace("{cost}", totalCostStr)})`;
  }

  text.textContent = rendered;
  badge.style.display = "inline-flex";
}



function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeEntityKey(value) {
  const tokens = String(value || "")
    .toLowerCase()
    .replace(/[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]/g, "")
    .replace(/[إأآا]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ة/g, "ه")
    .replace(/ؤ/g, "و")
    .replace(/ئ/g, "ي")
    .replace(/[^0-9a-z\u0600-\u06ff]+/gi, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map(token => {
      for (const prefix of ["وال", "فال", "بال", "كال", "لل", "ال"]) {
        if (token.startsWith(prefix) && token.length - prefix.length >= 3) {
          return token.slice(prefix.length);
        }
      }
      return token;
    });
  return tokens.join(" ");
}

// Canonical reports arrive as sanitized semantic HTML; legacy Markdown is normalized on the server.
const SAFE_DOCUMENT_TAGS = new Set([
  "ARTICLE", "SECTION", "H1", "H2", "H3", "H4", "P", "UL", "OL", "LI",
  "STRONG", "EM", "BLOCKQUOTE", "TABLE", "THEAD", "TBODY", "TR", "TH", "TD", "HR", "BR", "SPAN",
]);

function renderSemanticHtml(container, htmlText) {
  container.replaceChildren();
  const parsed = new DOMParser().parseFromString(String(htmlText || ""), "text/html");
  const source = parsed.body.querySelector("article") || parsed.body;

  const cloneSafe = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent || "");
    if (node.nodeType !== Node.ELEMENT_NODE) return document.createDocumentFragment();
    if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "SVG", "MATH", "TEMPLATE"].includes(node.tagName)) {
      return document.createDocumentFragment();
    }
    if (!SAFE_DOCUMENT_TAGS.has(node.tagName)) {
      const fragment = document.createDocumentFragment();
      node.childNodes.forEach(child => fragment.appendChild(cloneSafe(child)));
      return fragment;
    }
    const el = document.createElement(node.tagName.toLowerCase());
    if (node.tagName === "ARTICLE") {
      el.dir = node.getAttribute("dir") === "ltr" ? "ltr" : "rtl";
      el.lang = node.getAttribute("lang") || currentLang;
    }
    if (node.tagName === "TABLE") el.className = "rich-table";
    if (node.tagName === "SPAN") {
      const allowed = ["meta-label", "content-note", "visual-suggestion", "cta-suggestion"];
      const cls = Array.from(node.classList).find(value => allowed.includes(value));
      if (cls) el.className = cls;
    }
    node.childNodes.forEach(child => el.appendChild(cloneSafe(child)));
    return el;
  };

  source.childNodes.forEach(child => container.appendChild(cloneSafe(child)));
  decorateDocumentHeadings(container);
}

function decorateDocumentHeadings(container) {
  container.querySelectorAll("h1, h2, h3, h4").forEach((heading) => {
    heading.dataset.level = heading.tagName;
    heading.classList.add("document-heading");
  });
}

function showToast(message, tone = "success") {
  let host = document.getElementById("toastHost");
  if (!host) {
    host = document.createElement("div");
    host.id = "toastHost";
    host.className = "toast-host";
    document.body.appendChild(host);
  }
  const toast = document.createElement("div");
  toast.className = `toast toast-${tone}`;
  const icon = document.createElement("i");
  icon.className = tone === "error" ? "fa-solid fa-circle-exclamation" : "fa-solid fa-circle-check";
  const text = document.createElement("span");
  text.textContent = message;
  toast.append(icon, text);
  host.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("visible"));
  setTimeout(() => {
    toast.classList.remove("visible");
    setTimeout(() => toast.remove(), 250);
  }, 2600);
}

function createJobId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `job_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`;
}

function openModal(modal) {
  if (!modal) return;
  _lastFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  modal.classList.add("visible");
  modal.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => {
    const first = modal.querySelector("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
    if (first instanceof HTMLElement) first.focus();
  });
}

function closeModal(modal) {
  if (!modal) return;
  modal.classList.remove("visible");
  modal.setAttribute("aria-hidden", "true");
  if (_lastFocusedElement?.isConnected) _lastFocusedElement.focus();
  _lastFocusedElement = null;
}

function trapModalFocus(event, modal) {
  if (event.key !== "Tab") return;
  const focusable = [...modal.querySelectorAll(
    "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
  )].filter(element => element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function requestServerCancellation(jobId = _activeJobId, token = _activeCancelToken) {
  if (!jobId || !token) return;
  const cancelData = new FormData();
  cancelData.append("token", token);
  fetch(`/cancel/${encodeURIComponent(jobId)}`, { method: "POST", body: cancelData, keepalive: true }).catch(() => {});
}

function cancelAnalysis() {
  requestServerCancellation();
  if (_activeAbortController) _activeAbortController.abort();
  clearAnalysisWatchdog();
  _activeAbortController = null;
  _activeJobId = "";
  _activeCancelToken = "";
  setLoading(false);
  if (_activeMode === "serp") showSerpError(t("err_cancelled"));
  else showError(t("err_cancelled"));
  showToast(t("err_cancelled"), "error");
}

document.addEventListener("DOMContentLoaded", () => {
  // API keys are never handled in the browser: the server reads them from .env.
  try {
    sessionStorage.removeItem("claude_api_key");
    sessionStorage.removeItem("nara_api_key");
  } catch {}

  // Bind interactions from JavaScript so the strict Content Security Policy
  // can block inline event handlers without breaking the interface.
  document.getElementById("tabEntityBtn").addEventListener("click", () => switchTab("entity"));
  document.getElementById("tabSerpBtn").addEventListener("click", () => switchTab("serp"));
  document.getElementById("addCompUrlBtn").addEventListener("click", addCompetitorUrlField);
  document.getElementById("analyzeBtn").addEventListener("click", runAnalysis);
  document.getElementById("previewBtn").addEventListener("click", previewExtraction);
  document.getElementById("analyzeSerpBtn").addEventListener("click", runSerpAnalysis);
  document.getElementById("previewSerpBtn").addEventListener("click", previewSerpExtraction);
  document.getElementById("cancelBtn").addEventListener("click", cancelAnalysis);
  document.getElementById("exportBtn").addEventListener("click", downloadExcel);
  document.getElementById("exportSerpDocxBtn").addEventListener("click", downloadSerpWord);
  document.getElementById("copyBriefBtn").addEventListener("click", copyBrief);
  document.getElementById("copySerpBriefBtn").addEventListener("click", copySerpBrief);
  document.getElementById("closePreviewBtn").addEventListener("click", () => {
    closeModal(document.getElementById("previewModal"));
  });

  // Live Editor controls
  const toggleEditorBtn = document.getElementById("toggleEditorBtn");
  if (toggleEditorBtn) {
    toggleEditorBtn.addEventListener("click", () => {
      setEditorView(!toggleEditorBtn.classList.contains("editor-active"));
    });
  }

  const liveEditor = document.getElementById("liveEditorContent");
  if (liveEditor) {
    const updateEditor = debounce(() => {
      gradeLiveEditorContent();
      scheduleEditorDraftSave();
    }, 280);
    liveEditor.addEventListener("input", updateEditor);
    liveEditor.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveEditorDraft(true);
      }
    });
  }

  document.getElementById("undoEditorBtn")?.addEventListener("click", undoLiveEditorChange);
  document.getElementById("resetEditorBtn")?.addEventListener("click", resetLiveEditorContent);
  document.getElementById("copyEditorBtn")?.addEventListener("click", copyLiveEditorContent);
  document.getElementById("downloadEditorBtn")?.addEventListener("click", downloadLiveEditorContent);
  document.getElementById("editorSuggestionSearch")?.addEventListener("input", debounce((event) => {
    liveEditorState.search = event.target.value || "";
    renderLiveEditorSuggestions();
  }, 120));
  document.getElementById("editorSuggestionFilter")?.addEventListener("change", (event) => {
    liveEditorState.filter = event.target.value || "missing";
    renderLiveEditorSuggestions();
  });
  document.addEventListener("app:languagechange", () => {
    if (liveEditorState.evaluation) {
      gradeLiveEditorContent();
      updateEditorToggleLabel(document.getElementById("toggleEditorBtn")?.classList.contains("editor-active"));
    }
  });

  const closeEvidenceBtn = document.getElementById("closeEvidenceBtn");
  if (closeEvidenceBtn) {
    closeEvidenceBtn.addEventListener("click", () => {
      closeModal(document.getElementById("evidenceModal"));
    });
  }

  document.getElementById("filterCluster").addEventListener("change", applyFilters);
  document.getElementById("filterType").addEventListener("change", applyFilters);
  document.getElementById("filterPriority").addEventListener("change", applyFilters);
  document.getElementById("filterTier").addEventListener("change", applyFilters);
  document.getElementById("clustersGapsOnly").addEventListener("change", () => {
    if (_lastAnalysisData) renderClusters(_lastAnalysisData);
  });

  // --- Theme / interaction polish ---
  initAdvancedSettings();
  initDragAndDrop();
  initRevealAnimations();

  document.getElementById("gapsBody").addEventListener("click", handleEvidenceRowClick);
  document.getElementById("masterBody").addEventListener("click", handleEvidenceRowClick);
  document.getElementById("gapsBody").addEventListener("keydown", handleEvidenceRowKeydown);
  document.getElementById("masterBody").addEventListener("keydown", handleEvidenceRowKeydown);

  document.querySelectorAll(".modal-overlay").forEach((modal) => {
    modal.addEventListener("mousedown", (event) => {
      if (event.target === modal) closeModal(modal);
    });
  });

  document.addEventListener("keydown", (event) => {
    const modal = document.querySelector(".modal-overlay.visible");
    if (!modal) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeModal(modal);
      return;
    }
    trapModalFocus(event, modal);
  });
});

// The API key + model choice are shared by both tools (Entity + SERP), so
// they live in one collapsible panel instead of the tab-specific forms.
// Expanded by default until a key has actually been saved, so first-time
// users can find it; collapsed automatically afterwards to declutter the
// main form for returning sessions.
function handleEvidenceRowClick(event) {
  const row = event.target.closest("tr[data-evidence-entity]");
  if (!row) return;
  let terms = [];
  try { terms = JSON.parse(decodeURIComponent(row.dataset.evidenceTerms || "%5B%5D")); } catch (_) {}
  showEvidence(row.dataset.evidenceEntity || "", terms);
}

function handleEvidenceRowKeydown(event) {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest("tr[data-evidence-entity]");
  if (!row) return;
  event.preventDefault();
  row.click();
}

function initDragAndDrop() {
  document.querySelectorAll(".input-box").forEach((box) => {
    const input = box.querySelector('input[type="file"]');
    if (!input) return;
    ["dragenter", "dragover"].forEach(type => box.addEventListener(type, (event) => {
      event.preventDefault();
      box.classList.add("is-dragging");
    }));
    ["dragleave", "drop"].forEach(type => box.addEventListener(type, (event) => {
      event.preventDefault();
      box.classList.remove("is-dragging");
    }));
    box.addEventListener("drop", (event) => {
      const files = event.dataTransfer?.files;
      if (!files?.length) return;
      const transfer = new DataTransfer();
      const maxFiles = input.multiple ? Math.min(files.length, 30) : 1;
      for (let i = 0; i < maxFiles; i += 1) transfer.items.add(files[i]);
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
}

function initRevealAnimations() {
  const candidates = document.querySelectorAll(".hero, .settings-card, .tab-nav, .tab-pane .card");
  if (!("IntersectionObserver" in window)) {
    candidates.forEach(el => el.classList.add("is-visible"));
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.08 });
  candidates.forEach((el, index) => {
    el.classList.add("reveal-item");
    el.style.setProperty("--reveal-delay", `${Math.min(index * 55, 330)}ms`);
    observer.observe(el);
  });
}

function initAdvancedSettings() {
  const toggle = document.getElementById("advancedSettingsToggle");
  const panel = document.getElementById("advancedSettingsPanel");
  if (!toggle || !panel) return;

  const setExpanded = (expanded) => {
    panel.classList.toggle("collapsed", !expanded);
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  };

  setExpanded(false);
  toggle.addEventListener("click", () => {
    setExpanded(panel.classList.contains("collapsed"));
  });
}

// Builds a jump-to-section navigation list from the h2/h3 headings of an
// already-rendered semantic document. Long SERP
// wireframes now regularly have 15-20+ headings, so this lets users jump
// straight to a section instead of endlessly scrolling.
function buildTOC(contentContainer, tocContainer) {
  tocContainer.replaceChildren();
  const headings = contentContainer.querySelectorAll("h2, h3");
  const usedSlugs = new Set();

  headings.forEach((heading) => {
    let slug = (heading.textContent || "")
      .trim()
      .toLowerCase()
      .replace(/[^0-9a-z؀-ۿ]+/gi, "-")
      .replace(/^-+|-+$/g, "") || "section";
    let unique = slug;
    let suffix = 2;
    while (usedSlugs.has(unique)) {
      unique = `${slug}-${suffix}`;
      suffix += 1;
    }
    usedSlugs.add(unique);
    heading.id = unique;

    const link = document.createElement("a");
    link.href = `#${unique}`;
    link.textContent = heading.textContent;
    if (heading.tagName === "H3") link.classList.add("toc-h3");
    tocContainer.appendChild(link);
  });
}

document.getElementById("myFile").addEventListener("change", function () {
  const box = document.getElementById("myBox");
  const statusEl = document.getElementById("myFileStatus");
  if (this.files.length > 0) {
    document.getElementById("myUrl").value = "";
    statusEl.textContent = t("file_selected", { name: this.files[0].name });
    box.classList.add("has-file");
  } else {
    statusEl.textContent = t("my_site_status");
    box.classList.remove("has-file");
  }
});

document.getElementById("compFiles").addEventListener("change", function () {
  if (this.files.length > 0) {
    const statusEl = document.getElementById("compFileStatus");
    statusEl.textContent = t("files_selected", { count: this.files.length });
    document.getElementById("compBox").classList.add("has-file");
  }
});

let _serpCsvRows = 0;

document.getElementById("serpCsvFile").addEventListener("change", function () {
  _serpCsvRows = 0;
  if (this.files.length > 0) {
    const statusEl = document.getElementById("serpCsvStatus");
    statusEl.textContent = t("file_selected", { name: this.files[0].name });
    document.getElementById("serpBox").classList.add("has-file");
    // Count the rows so the readiness panel reports how many competitors the
    // run will actually cover, not just "1 file".
    this.files[0].text().then(text => {
      const lines = text.split(/\r?\n/).filter(line => line.trim());
      _serpCsvRows = Math.max(0, lines.length - 1);
      updatePreflight();
    }).catch(() => { _serpCsvRows = 1; updatePreflight(); });
  }
  updatePreflight();
});

const SERP_FILE_LIMIT = 100;
document.getElementById("serpFiles").addEventListener("change", function () {
  const statusEl = document.getElementById("serpFilesStatus");
  if (this.files.length > SERP_FILE_LIMIT) {
    statusEl.textContent = t("serp_files_too_many", { limit: SERP_FILE_LIMIT });
    document.getElementById("serpFilesBox").classList.remove("has-file");
    this.value = "";
    return;
  }
  if (this.files.length > 0) {
    statusEl.textContent = t("files_selected", { count: this.files.length });
    document.getElementById("serpFilesBox").classList.add("has-file");
  }
  updatePreflight();
});

document.getElementById("myUrl").addEventListener("input", function () {
  const fileInput = document.getElementById("myFile");
  const box = document.getElementById("myBox");
  const status = document.getElementById("myFileStatus");
  if (this.value.trim()) {
    if (fileInput.files.length) fileInput.value = "";
    status.textContent = t("url_selected");
    box.classList.add("has-file");
  } else {
    status.textContent = t("my_site_status");
    box.classList.remove("has-file");
  }
});

const MAX_COMP_URLS = 30;

function addCompetitorUrlField() {
  const count = document.querySelectorAll(".comp-url-input").length;
  if (count >= MAX_COMP_URLS) return;

  const row = document.createElement("div");
  row.className = "comp-url-row";

  const input = document.createElement("input");
  input.type = "url";
  input.className = "url-input comp-url-input";
  input.placeholder = t("comp_url_placeholder_n", { n: count + 1 });

  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "btn-remove-url";
  removeBtn.title = t("remove");
  removeBtn.innerHTML = '<i class="fa-solid fa-xmark"></i>';
  removeBtn.addEventListener("click", () => {
    row.remove();
    document.getElementById("addCompUrlBtn").disabled = false;
  });

  row.appendChild(input);
  row.appendChild(removeBtn);
  document.getElementById("extraCompUrls").appendChild(row);

  if (count + 1 >= MAX_COMP_URLS) {
    document.getElementById("addCompUrlBtn").disabled = true;
  }
}

function renderProgressValue(value) {
  const safeValue = Math.max(0, Math.min(100, Number(value) || 0));
  const bar = document.getElementById("progressBar");
  if (bar) bar.style.width = `${safeValue}%`;
  const percent = document.getElementById("progressPercent");
  if (percent) percent.textContent = `${Math.round(safeValue)}%`;
  const wrap = document.getElementById("progressWrap");
  if (wrap) wrap.setAttribute("aria-valuenow", String(Math.round(safeValue)));
}

function stopProgressAnimation() {
  if (_progressAnimationFrame) cancelAnimationFrame(_progressAnimationFrame);
  _progressAnimationFrame = null;
  _progressFrameAt = 0;
}

function animateProgress(timestamp) {
  const now = Number(timestamp) || performance.now();
  const frameDelta = _progressFrameAt ? Math.min(80, now - _progressFrameAt) : 16;
  _progressFrameAt = now;

  const waitingMs = Math.max(0, now - _progressUpdatedAt);
  const creepRatio = Math.min(0.88, 1 - Math.exp(-waitingMs / 15000));
  const desired = _progressTarget >= 100
    ? 100
    : _progressTarget + ((_progressCeiling - _progressTarget) * creepRatio);
  const gap = Math.max(0, desired - _progressDisplay);

  if (gap > 0.015) {
    const speed = gap > 20 ? 30 : gap > 8 ? 18 : 7;
    const easedStep = Math.max(0.035, Math.min(gap, (speed * frameDelta / 1000) + (gap * 0.045)));
    _progressDisplay = Math.min(desired, _progressDisplay + easedStep);
    renderProgressValue(_progressDisplay);
  }

  const canCreep = _progressTarget < 100 && _progressCeiling > _progressTarget + 0.05;
  if (gap > 0.015 || canCreep) {
    _progressAnimationFrame = requestAnimationFrame(animateProgress);
  } else {
    _progressAnimationFrame = null;
  }
}

function updateLoadingStage(stage, pct) {
  const nodes = [...document.querySelectorAll("[data-progress-stage]")];
  if (!nodes.length) return;
  const stageIndex = {
    sources: 0,
    analysis: 1,
    scan: 1,
    profiles: 1,
    strategy: 2,
    outline: 2,
    repair: 2,
    export: 3,
    done: 3,
  };
  let activeIndex = stageIndex[stage];
  if (!Number.isInteger(activeIndex)) {
    const numericPct = Number(pct) || 0;
    activeIndex = numericPct >= 90 ? 3 : numericPct >= 72 ? 2 : numericPct >= 45 ? 1 : 0;
  }
  nodes.forEach((node, index) => {
    node.classList.toggle("is-active", index === activeIndex);
    node.classList.toggle("is-complete", index < activeIndex || stage === "done");
  });
}

function updateLoadingMeta(meta = {}) {
  const counter = document.getElementById("loadingCounter");
  const detail = document.getElementById("loadingDetail");
  const current = Number(meta.current);
  const total = Number(meta.total);
  if (counter) {
    const hasCounter = Number.isFinite(current) && Number.isFinite(total) && total > 0;
    counter.textContent = hasCounter ? `${Math.max(0, current)}/${total}` : "—";
    counter.classList.toggle("has-value", hasCounter);
  }
  if (detail) {
    const text = String(meta.detail || "").trim();
    detail.textContent = text;
    detail.hidden = !text;
  }
}

function setProgress(pct, msg, meta = {}) {
  const safePct = Math.max(0, Math.min(100, Number(pct) || 0));
  if (safePct <= 0) {
    stopProgressAnimation();
    _progressDisplay = 0;
    _progressTarget = 0;
    _progressCeiling = 0;
    _progressUpdatedAt = performance.now();
    renderProgressValue(0);
    updateLoadingMeta();
    updateLoadingStage("sources", 0);
  } else if (safePct >= 100) {
    stopProgressAnimation();
    _progressDisplay = 100;
    _progressTarget = 100;
    _progressCeiling = 100;
    renderProgressValue(100);
    updateLoadingStage("done", 100);
  } else {
    _progressTarget = Math.max(_progressTarget, safePct);
    const suppliedCeiling = Math.max(safePct, Math.min(99.5, Number(meta.ceiling) || safePct));
    _progressCeiling = Math.max(_progressTarget, suppliedCeiling);
    _progressUpdatedAt = performance.now();
    updateLoadingStage(meta.stage, safePct);
    if (!_progressAnimationFrame) _progressAnimationFrame = requestAnimationFrame(animateProgress);
  }

  updateLoadingMeta(meta);
  if (msg) {
    const step = document.getElementById("loadingStep");
    step.textContent = msg;
    step.classList.remove("step-pulse");
    requestAnimationFrame(() => step.classList.add("step-pulse"));
  }
}

// "ping" events arrive while the backend is stuck waiting on a slow/rate-limited
// AI call, well before the next real "progress" event — without this the status
// text would sit frozen for as long as a minute or more (AI retry/rate-limit backoff can
// take time per retry) and look like the app hung.
function setStatusText(msg) {
  if (msg) document.getElementById("loadingStep").textContent = msg;
}

function formatElapsedTime(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function updateLoadingClock() {
  const elapsed = document.getElementById("loadingElapsedValue");
  if (elapsed && _loadingStartedAt) elapsed.textContent = formatElapsedTime(Date.now() - _loadingStartedAt);
}

function setLoadingTitle(text) {
  document.getElementById("loadingTitle").textContent = text;
}

function showError(msg) {
  const banner = document.getElementById("errorBanner");
  document.getElementById("errorText").textContent = msg;
  banner.classList.add("visible");
  banner.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function hideError() {
  document.getElementById("errorBanner").classList.remove("visible");
}

function uiError(msg) {
  showError(msg);
  setLoading(false);
}

function setLoading(on) {
  const loading = document.getElementById("loadingCard");
  for (const id of ["analyzeBtn", "analyzeSerpBtn", "previewBtn", "previewSerpBtn"]) {
    const button = document.getElementById(id);
    if (button) button.disabled = on;
  }
  const cancel = document.getElementById("cancelBtn");
  if (cancel) cancel.style.display = on ? "inline-flex" : "none";
  loading.style.display = on ? "block" : "none";
  document.body.classList.toggle("is-processing", on);
  document.body.setAttribute("aria-busy", on ? "true" : "false");
  if (on) {
    _loadingStartedAt = Date.now();
    clearInterval(_loadingClock);
    updateLoadingClock();
    _loadingClock = setInterval(updateLoadingClock, 1000);
    updateLoadingMeta();
    updateLoadingStage("sources", 0);
    loading.classList.remove("loading-enter");
    requestAnimationFrame(() => loading.classList.add("loading-enter"));
    loading.scrollIntoView({ behavior: "smooth", block: "center" });
  } else {
    clearInterval(_loadingClock);
    _loadingClock = null;
    _loadingStartedAt = 0;
    setProgress(0, "");
    updatePreflight();
  }
}

// Parses one raw SSE event block ("event: X\ndata: Y") from the /analyze stream.
function parseSSEEvent(rawBlock) {
  let eventName = null;
  const dataLines = [];
  for (const line of rawBlock.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  return { eventName, dataText: dataLines.join("\n") };
}

async function runAnalysis() {
  hideError();

  const myFile        = document.getElementById("myFile").files[0];
  const myUrl         = document.getElementById("myUrl").value.trim();
  const compFiles     = document.getElementById("compFiles").files;
  const compUrls      = [...document.querySelectorAll(".comp-url-input")]
                          .map(el => el.value.trim())
                          .filter(Boolean);
  const aiModel       = document.getElementById("aiModel").value;
  const aiLang        = document.getElementById("contentLanguage").value;
  const genBrief      = false;
  const verifyGaps    = true;
  const deepRead      = true;
  const keyword       = document.getElementById("keyword").value.trim();
  const targetCountry = document.getElementById("targetCountry").value.trim();

  if (_appMeta && !_appMeta.has_server_api_key) {
    showError(t("err_api_key_required"));
    return;
  }

  if (!myFile && !myUrl && compFiles.length === 0 && compUrls.length === 0) {
    showError(t("err_need_one_input_analyze"));
    return;
  }

  _activeMode = "entity";
  _activeJobId = createJobId();
  setLoading(true);
  document.getElementById("resultsArea").style.display = "none";
  document.getElementById("resultsHeader").style.display = "flex";
  _excelUrl  = "";
  _briefText = "";
  _evidenceToken = "";
  _lastAnalysisData = null;
  document.getElementById("filterCard").style.display = "none";
  const costBadge = document.getElementById("costTrackerBadge");
  if (costBadge) costBadge.style.display = "none";

  setProgress(2, t("status_connecting"));
  setLoadingTitle(t("status_analyzing_content"));

  const formData = new FormData();
  if (myFile)   formData.append("my_article", myFile);
  if (myUrl)    formData.append("my_url",     myUrl);
  for (const f of compFiles) formData.append("competitors", f);
  for (const url of compUrls) formData.append("comp_urls", url);

  formData.append("job_id", _activeJobId);
  formData.append("model", aiModel);
  formData.append("language", aiLang);
  formData.append("ui_language", currentLang);
  formData.append("generate_brief", genBrief ? "true" : "false");
  formData.append("verify_gaps", verifyGaps ? "true" : "false");
  formData.append("deep_read", deepRead ? "true" : "false");
  if (keyword)       formData.append("keyword", keyword);
  if (targetCountry) formData.append("target_country", targetCountry);

  try {
    _activeAbortController = new AbortController();
    startAnalysisWatchdog(_activeAbortController);
    const resp = await fetch("/analyze", {
      method: "POST",
      body: formData,
      signal: _activeAbortController.signal,
    });

    _lastRequestId = resp.headers.get("x-request-id") || "";
    const contentType = resp.headers.get("content-type") || "";

    // Basic validation errors (missing api_key, no input) are answered
    // directly as JSON before the stream opens.
    if (contentType.includes("application/json")) {
      const data = await resp.json();
      throw new Error(data.error || t("err_generic_server", { status: resp.status }));
    }

    if (!resp.ok || !resp.body) {
      throw new Error(t("err_unexpected_response", { status: resp.status }));
    }

    // Real progress: read the Server-Sent Events stream as it arrives
    // instead of guessing with a timer.
    const reader  = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer     = "";
    let finalData  = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      touchAnalysisWatchdog();
      buffer += decoder.decode(value, { stream: true });

      let sepIndex;
      while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
        const rawBlock = buffer.slice(0, sepIndex);
        buffer = buffer.slice(sepIndex + 2);
        const { eventName, dataText } = parseSSEEvent(rawBlock);
        if (!eventName || !dataText) continue;

        const payload = JSON.parse(dataText);
        if (eventName === "job") {
          _activeJobId = payload.job_id || _activeJobId;
          _activeCancelToken = payload.cancel_token || _activeCancelToken;
        } else if (eventName === "progress") {
          setProgress(payload.pct, payload.message, payload);
        } else if (eventName === "ping") {
          setStatusText(payload.message);
        } else if (eventName === "error") {
          throw new Error(payload.error || t("err_unexpected"));
        } else if (eventName === "done") {
          finalData = payload;
        }
      }
    }

    if (!finalData) {
      throw new Error(t("err_stream_interrupted"));
    }

    clearAnalysisWatchdog();
    _activeAbortController = null;
    _activeJobId = "";
    _activeCancelToken = "";
    setProgress(100, t("status_analysis_done"));
    setLoadingTitle(t("status_analysis_done_title"));

    setTimeout(() => {
      setLoading(false);
      renderResults(finalData);
    }, 400);

  } catch (err) {
    clearAnalysisWatchdog();
    _activeAbortController = null;
    _activeJobId = "";
    _activeCancelToken = "";
    if (err.name === "AbortError") { setLoading(false); return; }
    console.error("Analysis error:", err);
    let msg = err.message || t("err_unexpected");
    if (err instanceof TypeError && err.message.includes("fetch")) {
      msg = t("err_connect_failed");
    }
    uiError(formatErrorWithRequestId(msg));
  }
}

function renderPreviewSources(modalBody, sources) {
  if (!sources || sources.length === 0) {
    modalBody.innerHTML = `<div style="text-align: center; color: var(--rose); padding: 2rem;">${escapeHtml(t("err_preview_no_texts"))}</div>`;
    return;
  }

  let html = "";
  for (const src of sources) {
    html += `
      <div class="preview-box">
        <div class="preview-box-title">${escapeHtml(src.label)} ${src.status ? readStatusBadge(src.status) : ""}</div>
    `;
    if (src.error) {
      html += `<div style="color: #991B1B; background: #FEF2F2; padding: 0.75rem; border-radius: var(--radius-sm); font-size: 0.9rem;">
                 <i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(t("err_prefix"))} ${escapeHtml(src.error)}
               </div></div>`;
    } else if (!src.text || !src.text.trim()) {
      html += `<div style="color: #92400E; background: #FFFBEB; padding: 0.75rem; border-radius: var(--radius-sm); font-size: 0.9rem;">
                 <i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(t("err_preview_file_empty"))}
               </div></div>`;
    } else {
      if (src.note) {
        html += `<div style="color:#92400E;background:#FFFBEB;padding:.5rem .75rem;border-radius:var(--radius-sm);font-size:.85rem;margin-bottom:.5rem">${escapeHtml(src.note)}</div>`;
      }
      html += `<pre dir="auto">${escapeHtml(src.text)}</pre>
               <div style="font-size: 0.8rem; color: var(--slate); margin-top: 0.5rem; text-align: left;" dir="ltr">
                 ${escapeHtml(t("length_label", { count: src.text.length }))}
               </div></div>`;
    }
  }
  modalBody.innerHTML = html;
}

async function previewExtraction() {
  hideError();
  const myFile        = document.getElementById("myFile").files[0];
  const myUrl         = document.getElementById("myUrl").value.trim();
  const compFiles     = document.getElementById("compFiles").files;
  const compUrls      = [...document.querySelectorAll(".comp-url-input")]
                          .map(el => el.value.trim())
                          .filter(Boolean);
  const deepRead      = true;

  if (!myFile && !myUrl && compFiles.length === 0 && compUrls.length === 0) {
    showError(t("err_need_one_input_preview"));
    return;
  }

  const modal = document.getElementById("previewModal");
  const modalBody = document.getElementById("previewModalBody");
  openModal(modal);
  modalBody.innerHTML = `<div style="text-align: center; color: var(--slate); padding: 2rem;">${t("extracting")}</div>`;

  const formData = new FormData();
  if (myFile)   formData.append("my_article", myFile);
  if (myUrl)    formData.append("my_url",     myUrl);
  for (const f of compFiles) formData.append("competitors", f);
  for (const url of compUrls) formData.append("comp_urls", url);
  formData.append("deep_read", deepRead ? "true" : "false");
  formData.append("language", currentLang);
  formData.append("ui_language", currentLang);

  try {
    const resp = await fetch("/preview", { method: "POST", body: formData });
    if (!resp.ok) throw new Error(t("err_preview_server_fail"));
    const data = await resp.json();
    renderPreviewSources(modalBody, data.sources);
  } catch (err) {
    console.error("Preview error:", err);
    let errorMsg = err.message || t("err_preview_unknown");
    if (err.message.includes("404")) {
        errorMsg = t("err_preview_route_missing");
    }
    modalBody.innerHTML = `<div style="text-align: center; color: var(--rose); padding: 2rem;">
                             <i class="fa-solid fa-triangle-exclamation" style="font-size: 2rem; margin-bottom: 1rem;"></i><br/>
                             ${escapeHtml(t("err_preview_generic"))}<br/><br/>
                             <strong>${escapeHtml(errorMsg)}</strong>
                           </div>`;
  }
}

function typeBadge(entityType) {
  const cls = "badge badge-" + (entityType || "concept").toLowerCase();
  return `<span class="${escapeHtml(cls)}">${escapeHtml(entityType || "Concept")}</span>`;
}

function importanceBadge(imp) {
  const labels = { high: t("importance_high"), medium: t("importance_medium"), low: t("importance_low") };
  const cls    = "badge badge-" + (imp || "medium");
  return `<span class="${escapeHtml(cls)}">${escapeHtml(labels[imp] || t("importance_medium"))}</span>`;
}

function priorityBadge(prio) {
  const labels = { critical: t("priority_critical"), high: t("priority_high"), medium: t("priority_medium") };
  const cls    = prio === "critical" ? "badge badge-critical"
               : prio === "high"     ? "badge badge-high"
               :                       "badge badge-medium";
  return `<span class="${escapeHtml(cls)}">${escapeHtml(labels[prio] || prio)}</span>`;
}

function salienceBar(score) {
  const pct = Math.round((score || 0) * 100);
  return `<span style="font-size:.78rem;font-weight:700;color:#1D4ED8">${pct}%</span>`;
}

function evidenceScoreBadge(score, level = "") {
  const pct = Math.round(Math.max(0, Math.min(1, Number(score) || 0)) * 100);
  const tone = pct >= 78 ? "high" : pct >= 50 ? "medium" : "low";
  const title = level || (tone === "high" ? "verified" : tone === "medium" ? "supported" : "limited");
  return `<span class="evidence-score ${tone}" title="${escapeHtml(title)}"><i class="fa-solid fa-shield-halved"></i>${pct}%</span>`;
}

function renderStats(data) {
  document.getElementById("statsRow").style.display = "grid";
  const master = data.master_entities || [];
  const gaps   = data.semantic_gaps   || [];
  const comps  = data.competitor_breakdowns || [];
  const mySite = data.my_site_entities || [];

  const criticalGaps = gaps.filter(g => g.priority === "critical").length;

  const statsEl = document.getElementById("statsRow");
  statsEl.innerHTML = `
    <div class="stat-card">
      <div class="stat-val">${master.length}</div>
      <div class="stat-label">${escapeHtml(t("stat_master"))}</div>
    </div>
    <div class="stat-card">
      <div class="stat-val">${mySite.length}</div>
      <div class="stat-label">${escapeHtml(t("stat_mysite"))}</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" style="color:var(--rose)">${gaps.length}</div>
      <div class="stat-label">${escapeHtml(t("stat_gaps"))}</div>
    </div>
    <div class="stat-card">
      <div class="stat-val" style="color:#B91C1C">${criticalGaps}</div>
      <div class="stat-label">${escapeHtml(t("stat_critical"))}</div>
    </div>
    <div class="stat-card">
      <div class="stat-val">${comps.length}</div>
      <div class="stat-label">${escapeHtml(t("stat_competitors"))}</div>
    </div>
  `;
}

function renderGaps(gaps) {
  const card = document.getElementById("gapsCard");
  const body = document.getElementById("gapsBody");

  if (!gaps || gaps.length === 0) {
    card.style.display = "none";
    return;
  }
  card.style.display = "block";

  body.innerHTML = gaps.slice(0, 80).map(g => {
    const termsToSearch = [g.name, ...(g.lsi || [])].filter(Boolean);
    const searchJson = encodeURIComponent(JSON.stringify(termsToSearch));

    return `
    <tr class="evidence-row" tabindex="0" role="button" data-evidence-entity="${escapeHtml(g.name || "")}" data-evidence-terms="${escapeHtml(searchJson)}">
      <td style="font-weight:700; color:var(--blue);"><i class="fa-solid fa-magnifying-glass" style="font-size:0.75rem; margin-inline-end:0.4rem;"></i>${escapeHtml(g.name || "")}</td>
      <td style="font-size:0.8rem; color:var(--slate)">${escapeHtml((g.lsi || []).join(", ") || "—")}</td>
      <td><span class="badge" style="background:#F1F5F9;color:var(--slate)">${escapeHtml(g.cluster || t("label_uncategorized"))}</span></td>
      <td>${typeBadge(g.type)}</td>
      <td>${priorityBadge(g.priority)}</td>
      <td><span class="mentions-pill rose-pill">${g.competitor_count || 0}</span></td>
      <td><span class="mentions-pill rose-pill">${g.total_mentions || 0}</span></td>
      <td>${evidenceScoreBadge(g.confidence, g.evidence_level)}</td>
    </tr>
  `}).join("");
}

function renderMaster(master, myNames, compBreakdowns, hasMyArticle, hasCompetitors) {
  document.getElementById("thMySite").style.display = hasMyArticle  ? "" : "none";

  const headRow = document.getElementById("masterHeadRow");
  const compHeaders = headRow.querySelectorAll(".comp-matrix-th");
  compHeaders.forEach(th => th.remove());
  
  if (hasCompetitors) {
    compBreakdowns.forEach((comp, idx) => {
      const th = document.createElement("th");
      th.className = "comp-matrix-th";
      th.style.textAlign = "center";
      let shortLabel = (comp.source_label || `Comp ${idx + 1}`).split(" ")[0];
      if (shortLabel.length > 15) shortLabel = shortLabel.substring(0, 15) + "...";
      th.title = comp.source_label;
      th.textContent = shortLabel;
      headRow.appendChild(th);
    });
  }

  // Pre-calculate mention maps for each competitor
  const compMaps = compBreakdowns.map(comp => {
    const map = new Map();
    for (const ent of (comp.raw_entities || [])) {
      map.set(normalizeEntityKey(ent.name), ent.mentions || 1);
    }
    return map;
  });

  const body = document.getElementById("masterBody");
  if (!master || master.length === 0) {
    body.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:2rem;color:#94A3B8;font-weight:600">${escapeHtml(t("label_no_data"))}</td></tr>`;
    return;
  }

  const tierLabels = { core: t("tier_core"), supporting: t("tier_supporting"), long_tail: t("tier_long_tail") };
  body.innerHTML = master.slice(0, 200).map(ent => {
    const key      = normalizeEntityKey(ent.name);
    const keys     = [key, ...(ent.lsi || []).map(normalizeEntityKey)];
    const inMySite = hasMyArticle  ? (keys.some(k => myNames.has(k))
                       ? `<span class="check">✓</span>`
                       : `<span class="dash">—</span>`)
                     : "";

    let compCols = "";
    if (hasCompetitors) {
      compCols = compMaps.map(map => {
        const mentions = keys.reduce((found, k) => found || map.get(k) || 0, 0);
        return `<td style="text-align:center">${mentions > 0 ? `<span class="mentions-pill">${mentions}</span>` : `<span class="dash">—</span>`}</td>`;
      }).join("");
    }

    const termsToSearch = [ent.name, ...(ent.lsi || [])].filter(Boolean);
    const searchJson = encodeURIComponent(JSON.stringify(termsToSearch));

    return `
      <tr class="evidence-row" tabindex="0" role="button" data-evidence-entity="${escapeHtml(ent.name || "")}" data-evidence-terms="${escapeHtml(searchJson)}">
        <td style="font-weight:700; color:var(--blue);"><i class="fa-solid fa-magnifying-glass" style="font-size:0.75rem; margin-inline-end:0.4rem;"></i>${escapeHtml(ent.name || "")}${ent.tier ? ` <span class="tier-chip tier-${escapeHtml(ent.tier)}">${escapeHtml(tierLabels[ent.tier] || "")}</span>` : ""}</td>
        <td style="font-size:0.8rem; color:var(--slate)">${escapeHtml((ent.lsi || []).join(", ") || "—")}</td>
        <td><span class="badge" style="background:#F1F5F9;color:var(--slate)">${escapeHtml(ent.cluster || t("label_uncategorized"))}</span></td>
        <td>${typeBadge(ent.entity_type || ent.type)}</td>
        <td>${importanceBadge(ent.importance)}</td>
        <td>${salienceBar(ent.salience)}</td>
        <td>${evidenceScoreBadge(ent.evidence_score, ent.evidence_level)}</td>
        ${hasMyArticle  ? `<td style="text-align:center">${inMySite}</td>` : ""}
        ${compCols}
      </tr>
    `;
  }).join("");
}

function renderBrief(briefHtml) {
  const card = document.getElementById("briefCard");
  const body = document.getElementById("briefBody");
  if (!briefHtml || !briefHtml.trim()) {
    card.style.display = "none";
    return;
  }
  card.style.display = "block";
  _briefHtml = briefHtml;
  renderSemanticHtml(body, briefHtml);
  _briefText = body.innerText.trim();
  buildTOC(body, document.getElementById("briefToc"));
}

function setEditorView(showEditor) {
  const editorArea = document.getElementById("editorArea");
  const reportViews = document.getElementById("reportViews");
  const toggle = document.getElementById("toggleEditorBtn");
  if (!editorArea || !reportViews || !toggle) return;

  editorArea.style.display = showEditor ? "block" : "none";
  reportViews.style.display = showEditor ? "none" : "block";
  toggle.classList.toggle("editor-active", showEditor);
  toggle.setAttribute("aria-pressed", showEditor ? "true" : "false");
  updateEditorToggleLabel(showEditor);
}

function updateEditorToggleLabel(showEditor) {
  const toggle = document.getElementById("toggleEditorBtn");
  if (!toggle) return;
  const icon = document.createElement("i");
  icon.className = showEditor ? "fa-solid fa-chart-pie" : "fa-solid fa-pen-nib";
  const label = document.createElement("span");
  label.textContent = showEditor ? t("editor_report_view") : t("editor_back_to_editor");
  toggle.replaceChildren(icon, label);
}

function normalizeEditorText(value) {
  return normalizeEntityKey(String(value || ""));
}

function buildEditorTermCounts(normalizedContent, normalizedTerms) {
  const terms = [...new Set((normalizedTerms || []).filter(value => value && value.length >= 2))];
  const counts = new Map(terms.map(term => [term, 0]));
  if (!normalizedContent || !terms.length) return counts;

  const root = new Map();
  let maxDepth = 1;
  for (const term of terms) {
    const tokens = term.split(/\s+/).filter(Boolean).slice(0, 14);
    if (!tokens.length) continue;
    maxDepth = Math.max(maxDepth, tokens.length);
    let level = root;
    let finalNode = null;
    for (const token of tokens) {
      if (!level.has(token)) level.set(token, { children: new Map(), outputs: [] });
      finalNode = level.get(token);
      level = finalNode.children;
    }
    if (finalNode) finalNode.outputs.push(term);
  }

  const contentTokens = normalizedContent.split(/\s+/).filter(Boolean);
  for (let start = 0; start < contentTokens.length; start += 1) {
    let level = root;
    for (let offset = 0; offset < maxDepth && start + offset < contentTokens.length; offset += 1) {
      const node = level.get(contentTokens[start + offset]);
      if (!node) break;
      node.outputs.forEach(term => counts.set(term, (counts.get(term) || 0) + 1));
      level = node.children;
    }
  }
  return counts;
}

function editorPriorityRank(value) {
  return ({ critical: 4, high: 3, medium: 2, core: 1 })[String(value || "").toLowerCase()] || 1;
}

function editorPriorityWeight(value) {
  return ({ critical: 6, high: 4, medium: 2.5, core: 1.5 })[String(value || "").toLowerCase()] || 1;
}

function buildLiveEditorItems(data) {
  const items = new Map();
  const gaps = [...(data.semantic_gaps || [])].sort((a, b) => {
    const priority = editorPriorityRank(b.priority) - editorPriorityRank(a.priority);
    if (priority) return priority;
    return (Number(b.competitor_count) || 0) - (Number(a.competitor_count) || 0);
  });

  for (const gap of gaps.slice(0, 180)) {
    const key = normalizeEntityKey(gap.name);
    if (!key) continue;
    items.set(key, {
      key,
      name: String(gap.name || "").trim(),
      aliases: [...new Set((gap.lsi || []).map(value => String(value || "").trim()).filter(Boolean))].slice(0, 6),
      cluster: String(gap.cluster || t("label_uncategorized")),
      priority: String(gap.priority || "medium").toLowerCase(),
      kind: "gap",
      competitorCount: Number(gap.competitor_count) || 0,
      mentions: Number(gap.total_mentions) || 0,
      weight: editorPriorityWeight(gap.priority),
    });
  }

  const masters = [...(data.master_entities || [])].sort((a, b) => {
    const importance = ({ high: 3, medium: 2, low: 1 })[String(b.importance || "").toLowerCase()] -
      ({ high: 3, medium: 2, low: 1 })[String(a.importance || "").toLowerCase()];
    if (importance) return importance;
    return (Number(b.salience) || 0) - (Number(a.salience) || 0);
  });

  for (const entity of masters) {
    if (items.size >= 240) break;
    const key = normalizeEntityKey(entity.name);
    if (!key || items.has(key)) continue;
    const importance = String(entity.importance || "medium").toLowerCase();
    if (importance === "low" && items.size >= 200) continue;
    items.set(key, {
      key,
      name: String(entity.name || "").trim(),
      aliases: [...new Set((entity.lsi || []).map(value => String(value || "").trim()).filter(Boolean))].slice(0, 6),
      cluster: String(entity.cluster || t("label_uncategorized")),
      priority: "core",
      kind: "core",
      competitorCount: 0,
      mentions: Number(entity.mentions) || 0,
      weight: importance === "high" ? 2.2 : 1.5,
    });
  }

  return [...items.values()];
}

function countEditorWords(content) {
  const matches = String(content || "").trim().match(/[\p{L}\p{N}]+(?:['’_-][\p{L}\p{N}]+)*/gu);
  return matches ? matches.length : 0;
}

function countEditorParagraphs(content) {
  return String(content || "").split(/\n\s*\n+/).map(value => value.trim()).filter(Boolean).length;
}

function looksLikeEditorHeading(line) {
  const value = String(line || "").trim().replace(/^#{1,6}\s*/, "");
  if (!value || value.length > 110) return false;
  if (/^(?:https?:\/\/|www\.)/i.test(value)) return false;
  if (/[.!?؟،,;؛:]$/.test(value) && value.length > 45) return false;
  return value.split(/\s+/).length <= 12;
}

function countEditorHeadings(content) {
  const lines = String(content || "").split(/\r?\n/);
  let count = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!looksLikeEditorHeading(line)) continue;
    const beforeBlank = index === 0 || !lines[index - 1].trim();
    const afterBlank = index === lines.length - 1 || !lines[index + 1].trim();
    if (/^#{1,6}\s+/.test(lines[index]) || beforeBlank || afterBlank) count += 1;
  }
  return count;
}

function evaluateLiveEditor(content) {
  const normalizedContent = normalizeEditorText(content);
  const words = countEditorWords(content);
  const paragraphs = countEditorParagraphs(content);
  const headings = countEditorHeadings(content);
  const singleWordLimit = Math.max(5, Math.ceil(words / 220) + 2);
  const phraseLimit = Math.max(4, Math.ceil(words / 350) + 2);

  const aliasesByItem = liveEditorState.analysisItems.map((item) => [item.name, ...item.aliases]
      .map(normalizeEntityKey)
      .filter((value, index, array) => value.length >= 2 && array.indexOf(value) === index));
  const termCounts = buildEditorTermCounts(normalizedContent, aliasesByItem.flat());

  const evaluatedItems = liveEditorState.analysisItems.map((item, itemIndex) => {
    const normalizedAliases = aliasesByItem[itemIndex];
    let occurrences = 0;
    let matchedAlias = "";
    for (const alias of normalizedAliases) {
      const count = termCounts.get(alias) || 0;
      if (count > 0 && !matchedAlias) matchedAlias = alias;
      occurrences += count;
    }
    const phraseWords = normalizeEntityKey(item.name).split(/\s+/).filter(Boolean).length;
    const warningLimit = phraseWords > 1 ? phraseLimit : singleWordLimit;
    return {
      ...item,
      covered: occurrences > 0,
      occurrences,
      matchedAlias,
      warning: occurrences > warningLimit,
      warningLimit,
    };
  });

  const weightedRatio = (list) => {
    const total = list.reduce((sum, item) => sum + item.weight, 0);
    const covered = list.reduce((sum, item) => sum + (item.covered ? item.weight : 0), 0);
    return total ? covered / total : 1;
  };

  const gapItems = evaluatedItems.filter(item => item.kind === "gap");
  const coreItems = evaluatedItems.filter(item => item.kind === "core");
  let gapMax = 0;
  let coreMax = 0;
  if (gapItems.length && coreItems.length) {
    gapMax = 55;
    coreMax = 30;
  } else if (gapItems.length) {
    gapMax = 85;
  } else if (coreItems.length) {
    coreMax = 85;
  }
  const gapRatio = weightedRatio(gapItems);
  const coreRatio = weightedRatio(coreItems);
  const readinessRatio = content.trim() ? Math.min(1, (
    Math.min(words / 700, 1) * 0.46 +
    Math.min(paragraphs / 5, 1) * 0.27 +
    Math.min(headings / 3, 1) * 0.27
  )) : 0;
  const warnings = evaluatedItems.filter(item => item.warning);
  const repetitionPenalty = Math.min(12, warnings.reduce((sum, item) => sum + Math.min(3, item.occurrences - item.warningLimit), 0));
  const rawScore = gapRatio * gapMax + coreRatio * coreMax + readinessRatio * 15 - repetitionPenalty;
  const score = content.trim() ? Math.max(0, Math.min(100, Math.round(rawScore))) : 0;

  return {
    content,
    normalizedContent,
    items: evaluatedItems,
    words,
    paragraphs,
    headings,
    score,
    warnings,
    coveredCount: evaluatedItems.filter(item => item.covered).length,
    missingCount: evaluatedItems.filter(item => !item.covered).length,
    gapRatio,
    coreRatio,
    gapMax,
    coreMax,
    readinessRatio,
    repetitionPenalty,
  };
}

function updateLiveEditorScoreUI(evaluation) {
  const scoreBadge = document.getElementById("liveScoreBadge");
  const scoreRing = document.getElementById("liveScoreRing");
  if (!scoreBadge || !scoreRing) return;
  scoreBadge.textContent = String(evaluation.score);
  scoreRing.style.setProperty("--score-angle", `${evaluation.score * 3.6}deg`);
  scoreRing.classList.remove("score-low", "score-mid", "score-high");
  scoreRing.classList.add(evaluation.score >= 75 ? "score-high" : evaluation.score >= 45 ? "score-mid" : "score-low");
  scoreRing.title = t("editor_score_explainer");

  document.getElementById("editorWordsCount").textContent = evaluation.words.toLocaleString();
  document.getElementById("editorCoveredCount").textContent = evaluation.coveredCount.toLocaleString();
  document.getElementById("editorMissingCount").textContent = evaluation.missingCount.toLocaleString();
  document.getElementById("editorWarningsCount").textContent = evaluation.warnings.length.toLocaleString();
  document.getElementById("editorCharactersCount").textContent = evaluation.content.length.toLocaleString();
  document.getElementById("editorParagraphsCount").textContent = evaluation.paragraphs.toLocaleString();
  document.getElementById("resetEditorBtn").disabled = evaluation.content === liveEditorState.originalContent;
  document.getElementById("undoEditorBtn").disabled = liveEditorState.undoStack.length === 0;

  const breakdown = document.getElementById("editorScoreBreakdown");
  breakdown.replaceChildren();
  const rows = [];
  if (evaluation.gapMax > 0) {
    rows.push([t("editor_coverage_label"), Math.round(evaluation.gapRatio * 100), `${Math.round(evaluation.gapRatio * evaluation.gapMax)}/${evaluation.gapMax}`]);
  }
  if (evaluation.coreMax > 0) {
    rows.push([t("editor_core_label"), Math.round(evaluation.coreRatio * 100), `${Math.round(evaluation.coreRatio * evaluation.coreMax)}/${evaluation.coreMax}`]);
  }
  rows.push([t("editor_readiness_label"), Math.round(evaluation.readinessRatio * 100), `${Math.round(evaluation.readinessRatio * 15)}/15`]);
  if (evaluation.repetitionPenalty > 0) {
    rows.push([t("editor_penalty_label"), Math.min(100, Math.round(evaluation.repetitionPenalty / 12 * 100)), `-${evaluation.repetitionPenalty}`]);
  }
  rows.forEach(([labelText, percent, points]) => {
    const row = document.createElement("div");
    row.className = "editor-score-row";
    const label = document.createElement("span");
    label.textContent = labelText;
    const track = document.createElement("span");
    track.className = "editor-mini-track";
    const fill = document.createElement("span");
    fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    track.appendChild(fill);
    const value = document.createElement("strong");
    value.textContent = points;
    row.append(label, track, value);
    breakdown.appendChild(row);
  });
}

function getEditorBlocks(content) {
  const text = String(content || "");
  const blocks = [];
  const pattern = /\S(?:[\s\S]*?\S)?(?=\n\s*\n|$)/g;
  let match;
  let paragraph = 0;
  let activeHeading = "";
  while ((match = pattern.exec(text)) !== null) {
    const raw = match[0];
    const trimmed = raw.trim();
    if (!trimmed) continue;
    paragraph += 1;
    const firstLine = trimmed.split(/\r?\n/)[0].trim().replace(/^#{1,6}\s*/, "");
    if (looksLikeEditorHeading(firstLine)) activeHeading = firstLine;
    blocks.push({
      start: match.index,
      end: match.index + raw.length,
      text: trimmed,
      paragraph,
      heading: activeHeading,
      normalized: normalizeEditorText(trimmed),
    });
  }
  return blocks;
}

function locateEditorSuggestion(item, content) {
  const blocks = getEditorBlocks(content);
  if (!blocks.length) return { index: 0, label: t("editor_insert_at_start"), paragraph: 0, heading: "" };
  const searchTerms = [item.cluster, ...item.aliases.slice(0, 4)]
    .map(normalizeEntityKey)
    .flatMap(value => value.split(/\s+/))
    .filter(value => value.length >= 3);
  let best = null;
  for (const block of blocks) {
    let score = 0;
    for (const term of searchTerms) {
      if (` ${block.normalized} `.includes(` ${term} `)) score += 1;
    }
    if (block.heading && normalizeEntityKey(block.heading).split(/\s+/).some(term => searchTerms.includes(term))) score += 2;
    if (!best || score > best.score) best = { block, score };
  }
  const selected = best && best.score > 0 ? best.block : blocks[Math.min(blocks.length - 1, Math.max(0, Math.floor(blocks.length * 0.65)))];
  const label = selected.heading
    ? t("editor_after_section", { name: selected.heading.slice(0, 80) })
    : t("editor_after_paragraph", { count: selected.paragraph });
  return { index: selected.end, label, paragraph: selected.paragraph, heading: selected.heading };
}

function createEditorSuggestionCard(item, evaluation) {
  const card = document.createElement("article");
  card.className = "editor-suggestion";
  if (item.covered) card.classList.add("is-covered");
  if (item.warning) card.classList.add("is-warning");

  const top = document.createElement("div");
  top.className = "editor-suggestion-top";
  const titleWrap = document.createElement("div");
  titleWrap.className = "editor-suggestion-title";
  const title = document.createElement("strong");
  title.textContent = item.name;
  const status = document.createElement("small");
  status.textContent = item.warning
    ? `${t("editor_status_warning")} · ${item.occurrences}×`
    : item.covered ? `${t("editor_status_covered")} · ${item.occurrences}×` : t("editor_status_missing");
  titleWrap.append(title, status);
  const priority = document.createElement("span");
  priority.className = `editor-priority ${item.warning ? "high" : item.priority}`;
  const priorityKey = item.warning ? "high" : item.priority;
  priority.textContent = t(`editor_priority_${priorityKey}`);
  top.append(titleWrap, priority);
  card.appendChild(top);

  const location = locateEditorSuggestion(item, evaluation.content);
  const locationBox = document.createElement("div");
  locationBox.className = "editor-location";
  const pin = document.createElement("i");
  pin.className = "fa-solid fa-location-dot";
  const locationText = document.createElement("span");
  locationText.textContent = item.warning ? t("editor_locate") : location.label;
  locationBox.append(pin, locationText);
  card.appendChild(locationBox);

  const reason = document.createElement("p");
  reason.className = "editor-suggestion-reason";
  reason.textContent = item.warning ? t("editor_reason_warning") : item.kind === "gap" ? t("editor_reason_gap") : t("editor_reason_core");
  card.appendChild(reason);

  if (item.aliases.length) {
    const aliases = document.createElement("div");
    aliases.className = "editor-aliases";
    item.aliases.slice(0, 4).forEach((value) => {
      const chip = document.createElement("span");
      chip.textContent = value;
      aliases.appendChild(chip);
    });
    card.appendChild(aliases);
  }

  const actions = document.createElement("div");
  actions.className = "editor-suggestion-actions";
  if (!item.warning) {
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "editor-apply-btn";
    apply.disabled = item.covered;
    const applyIcon = document.createElement("i");
    applyIcon.className = item.covered ? "fa-solid fa-check" : "fa-solid fa-pen-to-square";
    const applyLabel = document.createTextNode(` ${item.covered ? t("editor_applied") : t("editor_apply_edit")}`);
    apply.append(applyIcon, applyLabel);
    apply.addEventListener("click", () => applyLiveEditorSuggestion(item));
    actions.appendChild(apply);
  }
  const locate = document.createElement("button");
  locate.type = "button";
  locate.className = "editor-locate-btn";
  locate.title = t("editor_locate");
  locate.setAttribute("aria-label", t("editor_locate"));
  const locateIcon = document.createElement("i");
  locateIcon.className = "fa-solid fa-crosshairs";
  locate.appendChild(locateIcon);
  locate.addEventListener("click", () => focusLiveEditorLocation(item));
  actions.appendChild(locate);
  card.appendChild(actions);
  return card;
}

function renderLiveEditorSuggestions() {
  const container = document.getElementById("liveEditorEntities");
  const countBadge = document.getElementById("editorSuggestionCount");
  const evaluation = liveEditorState.evaluation;
  if (!container || !evaluation) return;
  container.replaceChildren();

  const search = normalizeEntityKey(liveEditorState.search);
  const filtered = evaluation.items.filter((item) => {
    if (liveEditorState.filter === "missing" && (item.covered || item.warning)) return false;
    if (liveEditorState.filter === "covered" && (!item.covered || item.warning)) return false;
    if (liveEditorState.filter === "warnings" && !item.warning) return false;
    if (search) {
      const searchable = normalizeEntityKey([item.name, item.cluster, ...item.aliases].join(" "));
      if (!searchable.includes(search)) return false;
    }
    return true;
  }).sort((a, b) => {
    if (a.warning !== b.warning) return Number(b.warning) - Number(a.warning);
    if (a.covered !== b.covered) return Number(a.covered) - Number(b.covered);
    const priority = editorPriorityRank(b.priority) - editorPriorityRank(a.priority);
    return priority || b.weight - a.weight || a.name.localeCompare(b.name);
  });

  countBadge.textContent = filtered.length.toLocaleString();
  filtered.slice(0, 100).forEach(item => container.appendChild(createEditorSuggestionCard(item, evaluation)));
  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "editor-empty";
    const icon = document.createElement("i");
    icon.className = "fa-solid fa-circle-check";
    const title = document.createElement("strong");
    title.textContent = t("editor_no_suggestions");
    const desc = document.createElement("p");
    desc.textContent = t("editor_no_suggestions_desc");
    empty.append(icon, title, desc);
    container.appendChild(empty);
  }
}

function gradeLiveEditorContent() {
  const editor = document.getElementById("liveEditorContent");
  if (!editor) return;
  liveEditorState.evaluation = evaluateLiveEditor(editor.value);
  updateLiveEditorScoreUI(liveEditorState.evaluation);
  renderLiveEditorSuggestions();
}

function pushLiveEditorUndoSnapshot(content, selectionStart = 0, selectionEnd = selectionStart) {
  const stack = liveEditorState.undoStack;
  if (stack.length && stack[stack.length - 1].content === content) return;
  stack.push({ content, selectionStart, selectionEnd });
  if (stack.length > 20) stack.shift();
  document.getElementById("undoEditorBtn").disabled = stack.length === 0;
}

function undoLiveEditorChange() {
  const editor = document.getElementById("liveEditorContent");
  const snapshot = liveEditorState.undoStack.pop();
  if (!editor || !snapshot) return;
  editor.value = snapshot.content;
  editor.focus();
  editor.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd);
  gradeLiveEditorContent();
  saveEditorDraft(false);
}

function createLiveEditorScaffold(item) {
  const aliases = item.aliases.slice(0, 3).join(currentLang === "ar" ? "، " : ", ");
  const aliasesSuffix = aliases ? t("editor_aliases_prefix", { aliases }) : "";
  const title = t("editor_scaffold_title", { name: item.name });
  const body = t("editor_scaffold_body", { name: item.name, cluster: item.cluster, aliases: aliasesSuffix });
  return `${title}\n${body}`;
}

function applyLiveEditorSuggestion(item) {
  const editor = document.getElementById("liveEditorContent");
  if (!editor || item.covered) return;
  const before = editor.value;
  const location = locateEditorSuggestion(item, before);
  const scaffold = createLiveEditorScaffold(item);
  const prefix = location.index > 0 && !before.slice(0, location.index).endsWith("\n\n") ? "\n\n" : "";
  const suffix = location.index < before.length && !before.slice(location.index).startsWith("\n\n") ? "\n\n" : "";
  const insertion = `${prefix}${scaffold}${suffix}`;
  pushLiveEditorUndoSnapshot(before, editor.selectionStart, editor.selectionEnd);
  editor.value = `${before.slice(0, location.index)}${insertion}${before.slice(location.index)}`;
  const start = location.index + prefix.length;
  const end = start + scaffold.length;
  editor.focus();
  editor.setSelectionRange(start, end);
  editor.scrollTop = Math.max(0, editor.scrollHeight * (start / Math.max(1, editor.value.length)) - editor.clientHeight / 3);
  gradeLiveEditorContent();
  saveEditorDraft(false);
  showToast(t("editor_applied"));
}

function focusLiveEditorLocation(item) {
  const editor = document.getElementById("liveEditorContent");
  if (!editor) return;
  let start = locateEditorSuggestion(item, editor.value).index;
  if (item.covered) {
    const normalizedName = normalizeEntityKey(item.name);
    const plainIndex = editor.value.toLocaleLowerCase().indexOf(item.name.toLocaleLowerCase());
    if (plainIndex >= 0) start = plainIndex;
    else if (normalizedName) start = Math.min(start, editor.value.length);
  }
  editor.focus();
  editor.setSelectionRange(start, Math.min(editor.value.length, start + item.name.length));
  editor.scrollTop = Math.max(0, editor.scrollHeight * (start / Math.max(1, editor.value.length)) - editor.clientHeight / 3);
}

function scheduleEditorDraftSave() {
  clearTimeout(liveEditorState.saveTimer);
  liveEditorState.saveTimer = setTimeout(() => saveEditorDraft(false), 700);
}

function saveEditorDraft(showConfirmation) {
  const editor = document.getElementById("liveEditorContent");
  const status = document.getElementById("editorSaveStatus");
  if (!editor || !liveEditorState.draftKey) return;
  try {
    const payload = JSON.stringify({ content: editor.value, savedAt: Date.now() });
    if (payload.length > 3_500_000) throw new Error("draft-too-large");
    sessionStorage.setItem(liveEditorState.draftKey, payload);
    status.textContent = t("editor_saved_now");
    setTimeout(() => {
      if (status) status.textContent = t("editor_session_only");
    }, 1800);
    if (showConfirmation) showToast(t("editor_saved_now"));
  } catch (error) {
    console.warn("Editor session save failed:", error);
    status.textContent = t("editor_not_saved");
    if (showConfirmation) showToast(t("editor_not_saved"), "error");
  }
}

function readEditorDraft() {
  if (!liveEditorState.draftKey) return "";
  try {
    const raw = sessionStorage.getItem(liveEditorState.draftKey);
    if (!raw) return "";
    const payload = JSON.parse(raw);
    return typeof payload.content === "string" ? payload.content : "";
  } catch {
    return "";
  }
}

function resetLiveEditorContent() {
  const editor = document.getElementById("liveEditorContent");
  if (!editor || editor.value === liveEditorState.originalContent) return;
  if (!window.confirm(t("editor_reset_confirm"))) return;
  pushLiveEditorUndoSnapshot(editor.value, editor.selectionStart, editor.selectionEnd);
  editor.value = liveEditorState.originalContent;
  try { sessionStorage.removeItem(liveEditorState.draftKey); } catch {}
  gradeLiveEditorContent();
  showToast(t("editor_original_restored"));
}

async function copyLiveEditorContent() {
  const editor = document.getElementById("liveEditorContent");
  if (!editor) return;
  try {
    await navigator.clipboard.writeText(editor.value);
    showToast(t("editor_copied"));
  } catch {
    editor.focus();
    editor.select();
    const copied = document.execCommand && document.execCommand("copy");
    showToast(copied ? t("editor_copied") : t("err_copy_failed"), copied ? "success" : "error");
  }
}

function downloadLiveEditorContent() {
  const editor = document.getElementById("liveEditorContent");
  if (!editor) return;
  const blob = new Blob([editor.value], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `semantic-content-${new Date().toISOString().slice(0, 10)}.txt`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  showToast(t("editor_downloaded"));
}

async function initLiveEditor(data) {
  const editorArea = document.getElementById("editorArea");
  const reportViews = document.getElementById("reportViews");
  const resultsHeader = document.getElementById("resultsHeader");
  const editor = document.getElementById("liveEditorContent");
  const loading = document.getElementById("editorLoadingState");
  if (!editorArea || !reportViews || !resultsHeader || !editor || !loading) return;

  // Make the editor the first and default result workspace.
  if (editorArea.previousElementSibling !== resultsHeader) resultsHeader.after(editorArea);
  setEditorView(true);
  liveEditorState.loadNonce += 1;
  const nonce = liveEditorState.loadNonce;
  liveEditorState.analysisItems = buildLiveEditorItems(data);
  liveEditorState.evaluation = null;
  liveEditorState.originalContent = "";
  liveEditorState.undoStack = [];
  liveEditorState.search = "";
  liveEditorState.filter = "missing";
  document.getElementById("editorSuggestionSearch").value = "";
  document.getElementById("editorSuggestionFilter").value = "missing";
  editor.value = "";
  editor.disabled = true;
  editor.style.display = "none";
  loading.style.display = "flex";

  const token = String(data.editor_content_token || "");
  liveEditorState.draftKey = token ? `semantic-editor-draft:${token}` : `semantic-editor-draft:manual:${Date.now()}`;
  let source = "";
  if (token) {
    try {
      const response = await fetch("/editor-content", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Editor source failed");
      source = typeof payload.content === "string" ? payload.content : "";
    } catch (error) {
      console.warn("Could not load editor source:", error);
      if (nonce === liveEditorState.loadNonce) showToast(t("editor_source_failed"), "error");
    }
  }
  if (nonce !== liveEditorState.loadNonce) return;

  liveEditorState.originalContent = source;
  const draft = readEditorDraft();
  editor.value = draft || source;
  editor.disabled = false;
  editor.style.display = "block";
  loading.style.display = "none";
  if (draft && draft !== source) showToast(t("editor_draft_restored"));
  else if (!source) showToast(t("editor_source_empty"));
  gradeLiveEditorContent();
}


function clusterStatusLabel(status) {
  return ({
    covered: t("cluster_status_covered"),
    partial: t("cluster_status_partial"),
    missing: t("cluster_status_missing"),
  })[status] || t("cluster_status_na");
}

function clusterMeter(label, ratio, mine) {
  const pct = Math.round(Math.max(0, Math.min(1, Number(ratio) || 0)) * 100);
  return `<div class="cluster-meter"><span>${escapeHtml(label)}</span><strong>${pct}%</strong>
    <div class="cluster-bar${mine ? " mine" : ""}" style="grid-column:1 / -1"><span style="width:${pct}%"></span></div></div>`;
}

function renderClusters(data) {
  const card = document.getElementById("clustersCard");
  const body = document.getElementById("clustersBody");
  const clusters = Array.isArray(data.semantic_clusters) ? data.semantic_clusters : [];
  if (!clusters.length) {
    card.style.display = "none";
    return;
  }
  card.style.display = "block";
  const gapsOnly = document.getElementById("clustersGapsOnly").checked;
  const shown = clusters.filter(c => !gapsOnly || c.is_gap).slice(0, 60);
  if (!shown.length) {
    body.innerHTML = `<div style="padding:1rem;color:var(--slate);font-weight:600">${escapeHtml(t("clusters_empty"))}</div>`;
    return;
  }
  body.innerHTML = shown.map(c => {
    const supporting = (c.supporting_entities || []).slice(0, 18).map(e =>
      `<span class="cluster-chip ${e.covered_by_me ? "covered" : ""}" title="${escapeHtml(String(e.competitor_count || 0))}">${escapeHtml(e.name || "")}</span>`
    ).join("");
    const lsi = (c.lsi_terms || []).slice(0, 14).map(term => `<span class="cluster-chip lsi">${escapeHtml(term)}</span>`).join("");
    const missing = (c.missing_entities || []).slice(0, 14).map(term => `<span class="cluster-chip missing">${escapeHtml(term)}</span>`).join("");
    const hasMine = c.status && c.status !== "n/a";
    return `
      <article class="cluster-card ${c.is_gap ? "gap" : ""}">
        <h4><span>${escapeHtml(c.cluster_name || "")}</span>
          <span style="display:flex;gap:.35rem;flex-wrap:wrap">
            ${c.is_gap ? `<span class="badge badge-critical">${escapeHtml(t("cluster_gap_badge"))}</span>${priorityBadge(c.priority)}` : ""}
            <span class="badge" style="background:#F1F5F9;color:var(--slate)">${escapeHtml(clusterStatusLabel(c.status))}</span>
          </span>
        </h4>
        <div><div class="cluster-label">${escapeHtml(t("cluster_pillar"))}</div>
          <div class="cluster-chips"><span class="cluster-chip pillar">${escapeHtml(c.pillar_entity || "")}</span></div></div>
        ${supporting ? `<div><div class="cluster-label">${escapeHtml(t("cluster_supporting"))}</div><div class="cluster-chips">${supporting}</div></div>` : ""}
        ${lsi ? `<div><div class="cluster-label">${escapeHtml(t("cluster_lsi"))}</div><div class="cluster-chips">${lsi}</div></div>` : ""}
        ${missing ? `<div><div class="cluster-label">${escapeHtml(t("cluster_missing"))}</div><div class="cluster-chips">${missing}</div></div>` : ""}
        ${clusterMeter(`${t("cluster_competitors")} (${Number(c.competitor_count) || 0}/${Number(c.competitor_total) || 0})`, c.competitor_coverage, false)}
        ${hasMine ? clusterMeter(t("cluster_mine"), c.my_coverage, true) : ""}
      </article>`;
  }).join("");
}

function readStatusBadge(status) {
  const labels = {
    ok: t("read_status_ok"),
    partial: t("read_status_partial"),
    blocked: t("read_status_blocked"),
    failed: t("read_status_failed"),
  };
  const safe = labels[status] ? status : "failed";
  return `<span class="read-badge ${safe}">${escapeHtml(labels[safe])}</span>`;
}

function renderSourceReport(data) {
  const card = document.getElementById("sourcesCard");
  const body = document.getElementById("sourcesBody");
  const report = Array.isArray(data.extraction_report) ? data.extraction_report : [];
  if (!report.length) {
    card.style.display = "none";
    return;
  }
  card.style.display = "block";
  body.innerHTML = report.map(item => `
    <tr>
      <td style="font-weight:700">${escapeHtml(item.label || "")}<div style="font-size:.72rem;color:var(--slate);direction:ltr;text-align:start">${escapeHtml(item.url || "")}</div></td>
      <td>${readStatusBadge(item.status)}</td>
      <td>${escapeHtml(item.tier || "—")}</td>
      <td>${Number(item.chars) || 0}</td>
      <td style="font-size:.78rem;color:var(--slate)">${escapeHtml(item.reason || item.provider || "")}</td>
    </tr>`).join("");
}

function renderResults(data) {
  _lastAnalysisData = data;
  _evidenceToken = data.evidence_token || "";
  
  updateFilterDropdowns(data);
  
  applyFilters();

  renderStats(data);
  renderQualityDashboard(data);
  renderClusters(data);
  renderSourceReport(data);
  renderBrief(data.content_brief || "");
  
  document.getElementById("filterCard").style.display = "block";

  // Show master entities card if hidden by SERP analysis
  document.getElementById("masterCard").style.display = "block";
  document.getElementById("serpResultCard").style.display = "none";
  document.getElementById("resultsHeader").style.display = "flex";

  document.getElementById("exportJsonBtn").disabled = false;
  if (data.excel_file) {
    _excelUrl = data.excel_file;
    document.getElementById("exportBtn").disabled = false;
  }

  const resultsEl = document.getElementById("resultsArea");
  resultsEl.style.display = "block";
  resultsEl.classList.remove("results-enter");
  requestAnimationFrame(() => resultsEl.classList.add("results-enter"));
  resultsEl.scrollIntoView({ behavior: "smooth", block: "start" });
  
  // Update Live Cost & Token Tracker badge
  if (data.usage_metrics) {
    updateCostTracker("costTrackerBadge", "costTrackerText", data.usage_metrics);
  } else {
    const badge = document.getElementById("costTrackerBadge");
    if (badge) badge.style.display = "none";
  }

  // Show Editor Toggle (Disabled per user request)
  // document.getElementById("toggleEditorBtn").style.display = "flex";
  // initLiveEditor(data);
}

function updateFilterDropdowns(data) {
  const clusters = new Set();
  const types = new Set();
  const priorities = new Set();
  
  const allEntities = [...(data.master_entities || []), ...(data.semantic_gaps || [])];
  
  for (const ent of allEntities) {
    if (ent.cluster) clusters.add(ent.cluster);
    if (ent.entity_type || ent.type) types.add(ent.entity_type || ent.type);
    if (ent.priority) priorities.add(ent.priority);
  }
  
  const clusterSelect = document.getElementById("filterCluster");
  const typeSelect = document.getElementById("filterType");
  const prioritySelect = document.getElementById("filterPriority");
  
  // Keep the first option ("All")
  clusterSelect.innerHTML = `<option value="">${t("filter_all_clusters") || "All Clusters"}</option>`;
  typeSelect.innerHTML = `<option value="">${t("filter_all_types") || "All Types"}</option>`;
  prioritySelect.innerHTML = `<option value="">${t("filter_all_priorities") || "All Priorities"}</option>`;
  
  Array.from(clusters).sort().forEach(c => {
    const opt = document.createElement("option"); opt.value = c; opt.textContent = c;
    clusterSelect.appendChild(opt);
  });
  
  Array.from(types).sort().forEach(tVal => {
    const opt = document.createElement("option"); opt.value = tVal; opt.textContent = tVal;
    typeSelect.appendChild(opt);
  });
  
  Array.from(priorities).sort().forEach(p => {
    const opt = document.createElement("option"); opt.value = p; opt.textContent = p;
    prioritySelect.appendChild(opt);
  });
}

function applyFilters() {
  if (!_lastAnalysisData) return;
  
  const filterCluster = document.getElementById("filterCluster").value;
  const filterType = document.getElementById("filterType").value;
  const filterPriority = document.getElementById("filterPriority").value;
  const filterTier = document.getElementById("filterTier").value;
  const tierMatch = (ent) => !filterTier || !ent.tier
    || (filterTier === "core" ? ent.tier === "core" : ent.tier !== "long_tail");
  
  const matchFilter = (ent) => {
    if (filterCluster && (ent.cluster !== filterCluster)) return false;
    const type = ent.entity_type || ent.type;
    if (filterType && (type !== filterType)) return false;
    if (filterPriority && ent.priority && (ent.priority !== filterPriority)) return false;
    return true;
  };
  
  const filteredGaps = (_lastAnalysisData.semantic_gaps || []).filter(matchFilter);
  const filteredMaster = (_lastAnalysisData.master_entities || []).filter(matchFilter).filter(tierMatch);
  
  renderGaps(filteredGaps);
  
  const mySiteEntities = _lastAnalysisData.my_site_entities || [];
  const myNames = new Set(mySiteEntities.map(e => normalizeEntityKey(e.name)));
  
  renderMaster(
    filteredMaster,
    myNames,
    _lastAnalysisData.competitor_breakdowns || [],
    _lastAnalysisData.has_my_article,
    _lastAnalysisData.has_competitors
  );
}

function downloadExcel() {
  if (!_excelUrl) return;
  window.location.href = _excelUrl;
}

function downloadSerpWord() {
  if (!_docxUrl) return;
  window.location.href = _docxUrl;
}

function appendHighlightedText(parent, text, matchedTerm) {
  const value = String(text || "");
  const term = String(matchedTerm || "");
  if (!term) {
    parent.textContent = value;
    return;
  }
  const lower = value.toLocaleLowerCase();
  const needle = term.toLocaleLowerCase();
  let cursor = 0;
  let index = lower.indexOf(needle);
  while (index !== -1) {
    parent.appendChild(document.createTextNode(value.slice(cursor, index)));
    const mark = document.createElement("mark");
    mark.textContent = value.slice(index, index + term.length);
    parent.appendChild(mark);
    cursor = index + term.length;
    index = lower.indexOf(needle, cursor);
  }
  parent.appendChild(document.createTextNode(value.slice(cursor)));
}

async function showEvidence(entityName, searchTerms = []) {
  if (!_evidenceToken) {
    showToast(t("no_evidence_found") || "Evidence is not available for this result.", "error");
    return;
  }

  const modal = document.getElementById("evidenceModal");
  const title = document.getElementById("evidenceTitle");
  const body = document.getElementById("evidenceModalBody");
  title.textContent = `${t("evidence_drawer_for") || "Evidence for"}: ${entityName}`;
  body.replaceChildren();
  const loading = document.createElement("div");
  loading.className = "evidence-loading";
  loading.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i>';
  const loadingText = document.createElement("span");
  loadingText.textContent = t("loading_evidence") || "Loading evidence...";
  loading.appendChild(loadingText);
  body.appendChild(loading);
  openModal(modal);

  const params = new URLSearchParams({ entity: entityName });
  [...new Set([entityName, ...(searchTerms || [])].filter(Boolean))].slice(0, 12)
    .forEach(term => params.append("term", term));

  try {
    const response = await fetch(`/evidence/${encodeURIComponent(_evidenceToken)}?${params.toString()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Evidence lookup failed");
    body.replaceChildren();

    if (!data.results?.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = t("no_evidence_found") || "No exact string matches found in the extracted text.";
      body.appendChild(empty);
      return;
    }

    data.results.forEach((source) => {
      const sourceBox = document.createElement("section");
      sourceBox.className = "evidence-source";
      const sourceTitle = document.createElement("h3");
      const icon = document.createElement("i");
      icon.className = "fa-solid fa-file-lines";
      const label = document.createElement("span");
      label.textContent = source.source;
      sourceTitle.append(icon, label);
      sourceBox.appendChild(sourceTitle);

      source.snippets.forEach((snippet) => {
        const paragraph = document.createElement("p");
        paragraph.className = "evidence-snippet";
        appendHighlightedText(paragraph, snippet.text, snippet.matched_term || entityName);
        sourceBox.appendChild(paragraph);
      });
      body.appendChild(sourceBox);
    });
  } catch (error) {
    body.replaceChildren();
    const failed = document.createElement("div");
    failed.className = "empty-state error-state";
    failed.textContent = error.message;
    body.appendChild(failed);
  }
}

// Copies both clean plain text and semantic HTML. Pasting into Word,
// Google Docs, or Gmail keeps real headings, lists, emphasis, and tables;
// plain editors receive readable text without Markdown markers. The HTML
// source always comes from the app's allow-listed semantic renderer.
async function copyRichText(plainText, htmlSource) {

  if (typeof ClipboardItem !== "undefined" && navigator.clipboard.write) {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/plain": new Blob([plainText], { type: "text/plain" }),
          "text/html": new Blob([htmlSource], { type: "text/html" }),
        }),
      ]);
      return;
    } catch (err) {
      console.warn("Rich clipboard copy failed, falling back to plain text:", err);
    }
  }
  await navigator.clipboard.writeText(plainText);
}

async function copyBrief() {
  if (!_briefText) return;
  try {
    await copyRichText(_briefText, document.getElementById("briefBody").innerHTML);
    const btn = document.getElementById("copyBriefBtn");
    const orig = btn.innerHTML;
    btn.innerHTML = `<i class='fa-solid fa-check'></i> ${escapeHtml(t("copied"))}`;
    setTimeout(() => { btn.innerHTML = orig; }, 2000);
    showToast(t("copied"));
  } catch {
    showError(t("err_copy_failed"));
  }
}

async function copySerpBrief() {
  if (!_serpBriefText) return;
  try {
    await copyRichText(_serpBriefText, document.getElementById("serpResultBody").innerHTML);
    // Find the specific button in the SERP card
    const btn = document.getElementById("copySerpBriefBtn");
    if(btn) {
      const orig = btn.innerHTML;
      btn.innerHTML = `<i class='fa-solid fa-check'></i> ${escapeHtml(t("copied"))}`;
      setTimeout(() => { btn.innerHTML = orig; }, 2000);
      showToast(t("copied"));
    }
  } catch {
    showSerpError(t("err_copy_failed"));
  }
}

function showSerpError(msg) {
  const banner = document.getElementById("serpErrorBanner");
  document.getElementById("serpErrorText").textContent = msg;
  banner.classList.add("visible");
}

function hideSerpError() {
  document.getElementById("serpErrorBanner").classList.remove("visible");
}

async function runSerpAnalysis() {
  hideSerpError();

  const csvFile = document.getElementById("serpCsvFile").files[0];
  const serpFiles = Array.from(document.getElementById("serpFiles").files || []);
  const aiModel = document.getElementById("aiModel").value;
  const aiLang = document.getElementById("serpLanguage").value;
  const targetType = document.getElementById("serpTargetType").value;
  const topicContext = document.getElementById("serpTopic").value.trim();
  const deepRead = document.getElementById("serpDeepRead").checked;

  if (_appMeta && !_appMeta.has_server_api_key) {
    showSerpError(t("err_api_key_required"));
    return;
  }

  if (!csvFile && serpFiles.length === 0 && (!window._fetchedSerpUrls || window._fetchedSerpUrls.length === 0)) {
    showSerpError(t("err_serp_no_source"));
    return;
  }
  const checkedCount = document.querySelectorAll(".competitor-cb:checked").length;
  const fetchedCount = checkedCount > 0 ? checkedCount : (window._fetchedSerpUrls ? window._fetchedSerpUrls.length : 0);
  if (serpFiles.length + fetchedCount > SERP_FILE_LIMIT) {
    showSerpError(t("serp_files_too_many", { limit: SERP_FILE_LIMIT }));
    return;
  }
  if (!topicContext) {
    showSerpError(t("err_serp_topic_required"));
    document.getElementById("serpTopic").focus();
    return;
  }

  _activeMode = "serp";
  _activeJobId = createJobId();
  setLoading(true);
  document.getElementById("resultsArea").style.display = "none";
  document.getElementById("gapsCard").style.display = "none";
  document.getElementById("briefCard").style.display = "none";
  document.getElementById("statsRow").style.display = "none";
  document.getElementById("resultsHeader").style.display = "none";
  // We only show the SERP result card in this mode
  document.getElementById("serpResultCard").style.display = "none";
  const serpCostBadge = document.getElementById("serpCostTrackerBadge");
  if (serpCostBadge) serpCostBadge.style.display = "none";

  _serpBriefText = "";
  _serpBriefHtml = "";
  _docxUrl = "";

  setProgress(2, t("status_connecting"));
  setLoadingTitle(t("status_analyzing_serp"));

  const formData = new FormData();
  if (csvFile) formData.append("serp_csv", csvFile);
  serpFiles.forEach(file => formData.append("serp_files", file));
  
  // Collect checked URLs if auto-fetch was used
  const cbs = document.querySelectorAll(".competitor-cb:checked");
  if (cbs.length > 0) {
      const selectedUrls = Array.from(cbs).map(cb => cb.value);
      formData.append("serp_urls", JSON.stringify(selectedUrls));
  } else if (window._fetchedSerpUrls && window._fetchedSerpUrls.length > 0) {
      formData.append("serp_urls", JSON.stringify(window._fetchedSerpUrls));
  }

  formData.append("job_id", _activeJobId);
  formData.append("model", aiModel);
  formData.append("language", aiLang);
  formData.append("ui_language", currentLang);
  formData.append("target_type", targetType);
  formData.append("deep_read", deepRead ? "true" : "false");
  if (topicContext) {
    formData.append("topic_context", topicContext);
  }

  try {
    _activeAbortController = new AbortController();
    startAnalysisWatchdog(_activeAbortController);
    const resp = await fetch("/analyze-serp", {
      method: "POST",
      body: formData,
      signal: _activeAbortController.signal,
    });

    _lastRequestId = resp.headers.get("x-request-id") || "";
    const contentType = resp.headers.get("content-type") || "";

    if (contentType.includes("application/json")) {
      const data = await resp.json();
      throw new Error(data.error || t("err_generic_server", { status: resp.status }));
    }

    if (!resp.ok || !resp.body) {
      throw new Error(t("err_unexpected_response", { status: resp.status }));
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let finalData = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      touchAnalysisWatchdog();
      buffer += decoder.decode(value, { stream: true });

      let sepIndex;
      while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
        const rawBlock = buffer.slice(0, sepIndex);
        buffer = buffer.slice(sepIndex + 2);
        const { eventName, dataText } = parseSSEEvent(rawBlock);
        if (!eventName || !dataText) continue;

        const payload = JSON.parse(dataText);
        if (eventName === "job") {
          _activeJobId = payload.job_id || _activeJobId;
          _activeCancelToken = payload.cancel_token || _activeCancelToken;
        } else if (eventName === "progress") {
          setProgress(payload.pct, payload.message, payload);
        } else if (eventName === "ping") {
          setStatusText(payload.message);
        } else if (eventName === "error") {
          throw new Error(payload.error || t("err_unexpected"));
        } else if (eventName === "done") {
          finalData = payload;
        }
      }
    }

    if (!finalData) {
      throw new Error(t("err_stream_interrupted"));
    }

    clearAnalysisWatchdog();
    _activeAbortController = null;
    _activeJobId = "";
    _activeCancelToken = "";
    setProgress(100, t("status_analysis_done"));
    setLoadingTitle(t("status_structure_done_title"));

    setTimeout(() => {
      setLoading(false);
      renderSerpResults(finalData);
    }, 400);

  } catch (err) {
    clearAnalysisWatchdog();
    _activeAbortController = null;
    _activeJobId = "";
    _activeCancelToken = "";
    if (err.name === "AbortError") { setLoading(false); return; }
    console.error("SERP Analysis error:", err);
    let msg = err.message || t("err_unexpected");
    if (err instanceof TypeError && err.message.includes("fetch")) {
      msg = t("err_connect_failed_serp");
    }
    showSerpError(formatErrorWithRequestId(msg));
    setLoading(false);
  }
}

function renderSerpResults(data) {
  _lastAnalysisData = data;
  document.getElementById("exportJsonBtn").disabled = false;
  renderQualityDashboard(null);
  const card = document.getElementById("serpResultCard");
  const body = document.getElementById("serpResultBody");

  const structureHtml = data.structure_html || data.structure || "";
  if (!structureHtml) {
    showSerpError(t("err_serp_no_structure"));
    return;
  }
  
  card.style.display = "block";
  // Update Live Cost & Token Tracker badge for SERP
  if (data.usage_metrics) {
    updateCostTracker("serpCostTrackerBadge", "serpCostTrackerText", data.usage_metrics);
  } else {
    const sBadge = document.getElementById("serpCostTrackerBadge");
    if (sBadge) sBadge.style.display = "none";
  }
  _serpBriefHtml = structureHtml;
  renderSemanticHtml(body, structureHtml);
  _serpBriefText = data.structure_text || body.innerText.trim();
  buildTOC(body, document.getElementById("serpResultToc"));

  const coverage = data.coverage || {};
  const coverageMeta = document.getElementById("serpCoverageMeta");
  if (coverageMeta) {
    coverageMeta.replaceChildren();
    const quality = coverage.outline_quality || {};
    const counts = quality.counts || {};
    const topicCoverage = coverage.topic_coverage || {};
    const totalSources = coverage.sources || data.sources_total || data.sources_analyzed || 0;
    const representedSources = coverage.sources_represented || totalSources;
    const items = [
      ["fa-link", `${representedSources}/${totalSources} ${currentLang === "ar" ? "منافس ممثل" : "competitors represented"}`],
      ["fa-file-lines", `${Number(coverage.characters_read || 0).toLocaleString()} ${currentLang === "ar" ? "حرف مقروء" : "characters read"}`],
      ["fa-spell-check", `${Number(coverage.words_scanned || 0).toLocaleString()} ${currentLang === "ar" ? "كلمة ممسوحة" : "words scanned"}`],
      ["fa-layer-group", `${coverage.ai_profile_batches || 0} ${currentLang === "ar" ? "دفعات AI" : "AI batches"}`],
      ["fa-heading", `${counts.h2 || 0} H2 · ${counts.h3 || 0} H3`],
      [quality.valid ? "fa-shield-check" : "fa-triangle-exclamation", quality.valid
        ? (currentLang === "ar" ? "اجتازت فحص الهيكلة" : "structure quality passed")
        : (currentLang === "ar" ? "أفضل نسخة متاحة" : "best available version")],
    ];
    if (coverage.sources_limited) {
      items.push(["fa-circle-info", `${coverage.sources_limited} ${currentLang === "ar" ? "مصدر بأدلة محدودة" : "limited-evidence sources"}`]);
    }
    if (typeof topicCoverage.topic_ratio === "number") {
      items.push(["fa-bullseye", `${Math.round(topicCoverage.topic_ratio * 100)}% ${currentLang === "ar" ? "تغطية موضوعات" : "topic coverage"}`]);
    }
    if (coverage.fallback_chunks) {
      items.push(["fa-life-ring", `${coverage.fallback_chunks} ${currentLang === "ar" ? "جزء احتياطي" : "fallback chunks"}`]);
    }
    items.forEach(([iconName, labelText]) => {
      const chip = document.createElement("span");
      chip.className = "coverage-chip";
      const icon = document.createElement("i");
      icon.className = `fa-solid ${iconName}`;
      chip.append(icon, document.createTextNode(labelText));
      coverageMeta.appendChild(chip);
    });
    coverageMeta.style.display = "flex";
  }

  if (data.docx_file) {
    _docxUrl = data.docx_file;
    document.getElementById("exportSerpDocxBtn").disabled = false;
  } else {
    document.getElementById("exportSerpDocxBtn").disabled = true;
  }

  const resultsEl = document.getElementById("resultsArea");
  resultsEl.style.display = "block";
  
  // Hide the master entities table which is normally always shown
  const masterTables = document.querySelectorAll("#masterCard");
  masterTables.forEach(el => el.style.display = "none");

  resultsEl.classList.remove("results-enter");
  requestAnimationFrame(() => resultsEl.classList.add("results-enter"));
  resultsEl.scrollIntoView({ behavior: "smooth", block: "start" });
}

function switchTab(tabId) {
  _activeMode = tabId === "serp" ? "serp" : "entity";
  // Update buttons
  document.getElementById("tabEntityBtn").classList.toggle("active", tabId === 'entity');
  document.getElementById("tabSerpBtn").classList.toggle("active", tabId === 'serp');

  // Update panes
  document.getElementById("paneEntity").classList.toggle("active", tabId === 'entity');
  document.getElementById("paneSerp").classList.toggle("active", tabId === 'serp');
  
  // Hide results area when switching tabs
  document.getElementById("resultsArea").style.display = "none";
  updatePreflight();
}


async function previewSerpExtraction() {
  hideError();
  const csvFile = document.getElementById("serpCsvFile").files[0] || null;
  const pageFiles = [...(document.getElementById("serpFiles").files || [])];
  const deepRead = document.getElementById("serpDeepRead").checked;

  if (!csvFile && pageFiles.length === 0) {
    showError(t("err_serp_no_csv_preview"));
    return;
  }

  const modal = document.getElementById("previewModal");
  const modalBody = document.getElementById("previewModalBody");
  openModal(modal);
  modalBody.innerHTML = `<div style="text-align: center; color: var(--slate); padding: 2rem;">${t('extracting')}</div>`;

  const formData = new FormData();
  if (csvFile) formData.append("competitors", csvFile);
  // Preview every page that will be analysed, so the check matches the run.
  pageFiles.slice(0, SERP_FILE_LIMIT).forEach(file => formData.append("competitors", file));
  formData.append("deep_read", deepRead ? "true" : "false");
  formData.append("language", currentLang);
  formData.append("ui_language", currentLang);

  try {
    const resp = await fetch("/preview", { method: "POST", body: formData });
    if (!resp.ok) throw new Error(t("err_preview_server_fail"));
    const data = await resp.json();
    renderPreviewSources(modalBody, data.sources);
  } catch (err) {
    modalBody.innerHTML = `<div style="text-align: center; color: var(--rose); padding: 2rem;">${escapeHtml(err.message)}</div>`;
  }
}

// ── Production UX v2: runtime health, deterministic quality, preflight, and safe exports ──
function formatBytes(bytes) {
  const value = Math.max(0, Number(bytes) || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function setSystemStatus(state, label, title = "") {
  const status = document.getElementById("systemStatus");
  const text = document.getElementById("systemStatusText");
  if (!status || !text) return;
  status.classList.remove("online", "offline");
  if (state) status.classList.add(state);
  text.textContent = label;
  status.title = title || label;
}

async function initializeRuntimeMeta() {
  if (!navigator.onLine) {
    setSystemStatus("offline", t("system_offline"));
    updatePreflight();
    return;
  }
  setSystemStatus("", t("system_checking"));
  try {
    const [metaResponse, healthResponse] = await Promise.all([
      fetch("/api/meta", { cache: "no-store" }),
      fetch("/health", { cache: "no-store" }),
    ]);
    if (!metaResponse.ok || !healthResponse.ok) throw new Error("health check failed");
    _appMeta = await metaResponse.json();
    const health = await healthResponse.json();
    const detail = `${_appMeta.name || "Semantic SEO Tool"} v${_appMeta.version || health.version || ""} · ${health.active_jobs || 0}/${health.capacity || "—"}`;
    setSystemStatus("online", t("system_online"), detail);

    renderServerModelInfo();
  } catch (error) {
    console.warn("Runtime health check failed", error);
    setSystemStatus("offline", t("system_offline"));
  }
  updatePreflight();
}

// Default-model label and .env key status (re-rendered on language change).
function renderServerModelInfo() {
  if (!_appMeta) return;
  const defaultOption = document.querySelector('#aiModel option[value="default"]');
  if (defaultOption && _appMeta.default_model) {
    defaultOption.textContent = `${t("model_default_option")} — \u200e${_appMeta.default_model}\u200f`;
  }
  const select = document.getElementById("aiModel");
  if (select) {
    let group = select.querySelector('optgroup[data-engine="gemini"]');
    if (_appMeta.gemini_model) {
      if (!group) {
        group = document.createElement("optgroup");
        group.dataset.engine = "gemini";
        const option = document.createElement("option");
        option.value = _appMeta.gemini_model;
        group.appendChild(option);
        select.insertBefore(group, select.firstChild);
      } else if (group.firstElementChild) {
        group.firstElementChild.value = _appMeta.gemini_model;
      }
      group.label = t("model_group_gemini");
      if (group.firstElementChild) {
        group.firstElementChild.textContent = `${t("model_opt_gemini")} — \u200e${_appMeta.gemini_model}\u200f`;
      }
      try {
        const saved = JSON.parse(localStorage.getItem("semantic_seo_preferences_v2") || "{}");
        if (!saved.aiModel || saved.aiModel === "default" || saved.aiModel.startsWith("gemini")) {
          select.value = _appMeta.gemini_model;
        }
      } catch (e) {
        select.value = _appMeta.gemini_model;
      }
    } else if (group) {
      const wasGemini = select.value.startsWith("gemini");
      group.remove();
      if (wasGemini) {
        select.value = "default";
      }
    }
  }
  const badge = document.getElementById("serverKeyBadge");
  const text = document.getElementById("serverKeyBadgeText");
  if (badge && text) {
    const ok = Boolean(_appMeta.has_server_api_key);
    badge.classList.toggle("missing", !ok);
    text.textContent = ok ? t("api_key_server_secured") : t("api_key_server_missing");
    badge.style.display = "inline-flex";
  }
}

document.addEventListener("app:languagechange", renderServerModelInfo);

function currentPreflightInputs() {
  const mode = document.getElementById("paneSerp")?.classList.contains("active") ? "serp" : "entity";
  if (mode === "serp") {
    // Every uploaded competitor page is a source. Reading only files[0] here
    // is what made nine uploads report "SOURCES 1".
    const csv = document.getElementById("serpCsvFile")?.files?.[0] || null;
    const pages = [...(document.getElementById("serpFiles")?.files || [])];
    // _serpCsvRows is filled when the CSV is chosen; until then a CSV counts
    // as one source rather than pretending to know how many rows it holds.
    const csvSources = csv ? Math.max(1, _serpCsvRows) : 0;
    const sources = csvSources + pages.length;
    return {
      mode,
      files: [...(csv ? [csv] : []), ...pages],
      sources,
      competitors: sources,
      deepRead: Boolean(document.getElementById("serpDeepRead")?.checked),
    };
  }
  const myFile = document.getElementById("myFile")?.files?.[0];
  const myUrl = document.getElementById("myUrl")?.value.trim();
  const compFiles = [...(document.getElementById("compFiles")?.files || [])];
  const compUrls = [...document.querySelectorAll(".comp-url-input")].map(input => input.value.trim()).filter(Boolean);
  const hasMy = Boolean(myUrl || myFile);
  return {
    mode,
    files: [...(myFile ? [myFile] : []), ...compFiles],
    sources: (hasMy ? 1 : 0) + compFiles.length + compUrls.length,
    competitors: compFiles.length + compUrls.length,
    deepRead: true,
  };
}

function updatePreflight() {
  const card = document.getElementById("preflightCard");
  if (!card) return;
  const values = currentPreflightInputs();
  const totalBytes = values.files.reduce((sum, file) => sum + (file.size || 0), 0);
  const maxFileBytes = _appMeta?.limits?.max_file_bytes || 15 * 1024 * 1024;
  // The SERP tool takes uploaded pages up to its own, higher limit; using the
  // entity tool's competitor cap here would flag a valid 12-page run.
  const maxCompetitors = values.mode === "serp"
    ? (_appMeta?.limits?.max_serp_files || SERP_FILE_LIMIT)
    : (_appMeta?.limits?.max_competitors || 30);
  const oversized = values.files.find(file => file.size > maxFileBytes);
  const tooMany = values.competitors > maxCompetitors;
  const offline = !navigator.onLine;
  const invalid = Boolean(oversized || tooMany || offline);

  document.getElementById("preflightSources").textContent = String(values.sources);
  document.getElementById("preflightSize").textContent = formatBytes(totalBytes);
  const workloadScore = values.sources + (totalBytes / 1024 ** 2) * 1.5 + (values.deepRead ? 4 : 0);
  const workloadKey = workloadScore >= 14 ? "workload_intensive" : workloadScore >= 6 ? "workload_standard" : "workload_light";
  document.getElementById("preflightWorkload").textContent = values.sources ? t(workloadKey) : "—";

  let message = values.sources ? t("preflight_ready") : t("preflight_empty");
  if (oversized) message = t("err_file_too_large", { size: formatBytes(maxFileBytes) });
  else if (tooMany) message = t("err_too_many_sources", { count: maxCompetitors });
  else if (offline) message = t("system_offline");
  document.getElementById("preflightMessage").textContent = message;
  card.classList.toggle("invalid", invalid);

  const processing = document.body.classList.contains("is-processing");
  for (const id of ["analyzeBtn", "analyzeSerpBtn", "previewBtn", "previewSerpBtn"]) {
    const button = document.getElementById(id);
    if (button && !processing) button.disabled = invalid;
  }
}

function renderQualityDashboard(data) {
  const dashboard = document.getElementById("qualityDashboard");
  if (!dashboard) return;
  const quality = data?.quality;
  if (!quality) {
    dashboard.style.display = "none";
    return;
  }
  dashboard.style.display = "grid";
  const score = Math.max(0, Math.min(100, Number(quality.score) || 0));
  document.getElementById("qualityScoreRing").style.setProperty("--quality-pct", `${score}%`);
  document.getElementById("qualityScoreValue").textContent = String(score);
  // Content coverage is a SEPARATE number from the analysis score above: the
  // score says how trustworthy the extraction is, this says how much of the
  // competitors' shared agenda the page actually covers.
  const sharedTopics = Number(quality.shared_topics) || 0;
  const coveragePct = Math.round((Number(quality.coverage_rate) || 0) * 100);
  const metrics = [
    [sharedTopics
      ? `${coveragePct}%`
      : "—", t("quality_coverage"), "coverage"],
    [sharedTopics
      ? `${Number(quality.covered_topics) || 0}/${sharedTopics}`
      : "—", t("quality_shared_topics")],
    [quality.supported_entities || 0, t("quality_supported")],
    [`${Math.round((quality.verified_rate || 0) * 100)}%`, t("quality_verified")],
    [quality.unverified_entities || 0, t("quality_unverified")],
    [quality.high_confidence_gaps || 0, t("quality_high_gaps")],
    // A source that failed extraction or AI analysis used to just disappear,
    // so "8 competitors in, 7 in the report" had no explanation on screen.
    [Number(quality.requested_sources || 0) > Number(quality.source_count || 0)
      ? `${quality.source_count || 0}/${quality.requested_sources}`
      : (quality.source_count || 0), t("quality_sources")],
    [`${Number(data.processing_seconds || 0).toFixed(1)}s`, t("quality_runtime")],
  ];
  const metricsHost = document.getElementById("qualityMetrics");
  metricsHost.replaceChildren(...metrics.map(([value, label, variant]) => {
    const item = document.createElement("div");
    item.className = variant === "coverage" ? "quality-metric quality-metric--coverage" : "quality-metric";
    const strong = document.createElement("strong");
    strong.textContent = String(value);
    const span = document.createElement("span");
    span.textContent = label;
    item.append(strong, span);

    // Shared topics are a countable set, so coverage is drawn one mark per
    // topic — solid where the page covers it, hollow where it does not.
    // A percentage hides whether "69%" means 9 of 13 or 90 of 130.
    if (variant === "coverage" && sharedTopics > 0) {
      const covered = Math.max(0, Math.min(sharedTopics, Number(quality.covered_topics) || 0));
      const scale = document.createElement("div");
      scale.className = "coverage-scale";
      scale.setAttribute("role", "img");
      scale.setAttribute("aria-label",
        t("coverage_aria", { covered, total: sharedTopics }));
      for (let i = 0; i < Math.min(sharedTopics, 120); i += 1) {
        const tick = document.createElement("i");
        if (i >= covered) tick.className = "is-gap";
        scale.appendChild(tick);
      }
      const legend = document.createElement("div");
      legend.className = "coverage-legend";
      legend.textContent = t("coverage_legend", {
        covered, gaps: sharedTopics - covered,
      });
      item.append(scale, legend);
    }
    return item;
  }));
  const warningsHost = document.getElementById("qualityWarnings");
  warningsHost.replaceChildren(...(quality.warnings || []).map(message => {
    const row = document.createElement("div");
    row.className = "quality-warning";
    const icon = document.createElement("i");
    icon.className = "fa-solid fa-circle-info";
    const text = document.createElement("span");
    text.textContent = message;
    row.append(icon, text);
    return row;
  }));
}

function exportAnalysisJson() {
  if (!_lastAnalysisData) return;
  const safePayload = typeof structuredClone === "function" ? structuredClone(_lastAnalysisData) : JSON.parse(JSON.stringify(_lastAnalysisData));
  delete safePayload.editor_content_token;
  delete safePayload.evidence_token;
  const blob = new Blob([JSON.stringify(safePayload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `semantic-seo-analysis-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  showToast(t("export_json_done"));
}

function formatErrorWithRequestId(message) {
  return _lastRequestId ? `${message} · ID: ${_lastRequestId}` : message;
}

function clearAnalysisWatchdog() {
  if (_analysisWatchdog) clearTimeout(_analysisWatchdog);
  _analysisWatchdog = null;
}

function startAnalysisWatchdog(controller) {
  clearAnalysisWatchdog();
  _analysisWatchdog = setTimeout(() => {
    requestServerCancellation();
    controller?.abort();
    if (_activeMode === "serp") showSerpError(t("err_stream_timeout"));
    else showError(t("err_stream_timeout"));
  }, 180_000);
}

function touchAnalysisWatchdog() {
  if (!_activeAbortController) return;
  startAnalysisWatchdog(_activeAbortController);
}

function restoreSafePreferences() {
  try {
    const data = JSON.parse(localStorage.getItem("semantic_seo_preferences_v2") || "{}");
    const assignments = {
      aiModel: data.aiModel,
      contentLanguage: data.contentLanguage,
      serpLanguage: data.serpLanguage,
    };
    for (const [id, value] of Object.entries(assignments)) {
      const element = document.getElementById(id);
      if (element && value && [...element.options].some(option => option.value === value)) element.value = value;
    }
    for (const id of ["serpDeepRead"]) {
      if (typeof data[id] === "boolean" && document.getElementById(id)) document.getElementById(id).checked = data[id];
    }
  } catch (error) {
    console.warn("Preference restore failed", error);
  }
}

function saveSafePreferences() {
  try {
    const data = {};
    for (const id of ["aiModel", "contentLanguage", "serpLanguage"]) data[id] = document.getElementById(id)?.value;
    for (const id of ["serpDeepRead"]) data[id] = Boolean(document.getElementById(id)?.checked);
    localStorage.setItem("semantic_seo_preferences_v2", JSON.stringify(data));
  } catch (error) {
    console.warn("Preference save failed", error);
  }
}

function initializeV2Experience() {
  restoreSafePreferences();
  initializeRuntimeMeta();

  document.getElementById("exportJsonBtn")?.addEventListener("click", exportAnalysisJson);

  const watchedIds = ["myFile", "myUrl", "compFiles", "serpCsvFile", "serpFiles", "serpDeepRead", "aiModel", "contentLanguage", "serpLanguage"];
  watchedIds.forEach(id => {
    const element = document.getElementById(id);
    if (!element) return;
    element.addEventListener("input", updatePreflight);
    element.addEventListener("change", () => { updatePreflight(); saveSafePreferences(); });
  });
  document.getElementById("extraCompUrls")?.addEventListener("input", updatePreflight);
  document.getElementById("extraCompUrls")?.addEventListener("click", () => setTimeout(updatePreflight));

  window.addEventListener("online", initializeRuntimeMeta);
  window.addEventListener("offline", () => { setSystemStatus("offline", t("system_offline")); updatePreflight(); });
  window.addEventListener("beforeunload", () => {
    if (_activeJobId && _activeCancelToken && navigator.sendBeacon) {
      const data = new FormData();
      data.append("token", _activeCancelToken);
      navigator.sendBeacon(`/cancel/${encodeURIComponent(_activeJobId)}`, data);
    }
  });
  document.addEventListener("app:languagechange", () => {
    updatePreflight();
    if (document.getElementById("systemStatus")?.classList.contains("online")) setSystemStatus("online", t("system_online"));
    else if (document.getElementById("systemStatus")?.classList.contains("offline")) setSystemStatus("offline", t("system_offline"));
    if (_lastAnalysisData) renderQualityDashboard(_lastAnalysisData);
  });
  updatePreflight();
}

document.addEventListener("DOMContentLoaded", initializeV2Experience);

// --- ALMASTER SCRAPER INTEGRATION ---
let _locationsData = [];
let _fetchedSerpUrls = [];

fetch('locations.json').then(r => r.json()).then(data => { _locationsData = data; }).catch(e => console.warn("Failed to load locations.json", e));

const locInput = document.getElementById("autoSerpLocation");
const locDropdown = document.getElementById("locationDropdown");
const canonInput = document.getElementById("autoSerpCanonical");

if(locInput && locDropdown) {
    let debounceTimer;
    
    const ARABIC_MAP = {
        "السعودية": "saudi arabia", "الرياض": "riyadh", "جدة": "jeddah", "مكة": "mecca", "المدينة": "medina", "الدمام": "dammam",
        "مصر": "egypt", "القاهرة": "cairo", "الاسكندرية": "alexandria", "الجيزة": "giza",
        "الامارات": "united arab emirates", "دبي": "dubai", "ابوظبي": "abu dhabi", "الشارقة": "sharjah",
        "الكويت": "kuwait", "قطر": "qatar", "الدوحة": "doha", "البحرين": "bahrain", "المنامة": "manama",
        "عمان": "oman", "مسقط": "muscat", "الاردن": "jordan", "عمان ": "amman", "لبنان": "lebanon",
        "المغرب": "morocco", "الدار البيضاء": "casablanca", "الرباط": "rabat", "تونس": "tunisia", "الجزائر": "algeria"
    };

    locInput.addEventListener("input", function() {
        clearTimeout(debounceTimer);
        const q = this.value.trim().toLowerCase();
        canonInput.value = "";
        if(q.length < 2) { locDropdown.style.display = "none"; return; }
        
        debounceTimer = setTimeout(async () => {
            let searchQ = q;
            // Check if contains Arabic
            if(/[\u0600-\u06FF]/.test(q)) {
                locDropdown.style.display = "block";
                locDropdown.innerHTML = '<div style="padding:10px; text-align:center; font-size:0.8rem;">Translating location... <i class="fa-solid fa-spinner fa-spin"></i></div>';
                
                let mapped = false;
                for (const [ar, en] of Object.entries(ARABIC_MAP)) {
                    if (q.includes(ar)) { searchQ = en; mapped = true; break; }
                }

                if (!mapped) {
                    try {
                        const nomRes = await fetch(`https://api.mymemory.translated.net/get?q=${encodeURIComponent(q)}&langpair=ar|en`);
                        const nomData = await nomRes.json();
                        if(nomData && nomData.responseData && nomData.responseData.translatedText) {
                            searchQ = nomData.responseData.translatedText.toLowerCase();
                        }
                    } catch(e) { console.warn('Translation error', e); }
                }
            }
            
            const matches = _locationsData.filter(loc => loc[0].toLowerCase().includes(searchQ) || loc[1].toLowerCase().includes(searchQ)).slice(0, 15);
            
            locDropdown.innerHTML = "";
            if(!matches.length) {
                locDropdown.innerHTML = '<div style="padding:10px; text-align:center; font-size:0.8rem;">No locations found</div>';
            } else {
                matches.forEach(place => {
                    const div = document.createElement("div");
                    div.style.cssText = "padding: 8px 12px; cursor: pointer; border-bottom: 1px solid var(--border);";
                    div.innerHTML = `<div style="font-weight:700; font-size:0.85rem;">${place[0]}</div><div style="font-size:0.7rem; color:var(--slate);">${place[1]}</div>`;
                    div.onclick = () => {
                        locInput.value = place[0];
                        canonInput.value = place[1];
                        locDropdown.style.display = "none";
                    };
                    locDropdown.appendChild(div);
                });
            }
            locDropdown.style.display = "block";
        }, 500);
    });

    document.addEventListener("click", e => {
        if(!locInput.contains(e.target) && !locDropdown.contains(e.target)) locDropdown.style.display = "none";
    });
}

function generateUULE(canonicalName) {
  if (!canonicalName) return "";
  const secret = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  const keyLength = canonicalName.length;
  const key = secret[keyLength] || secret[keyLength % secret.length];
  const b64 = btoa(unescape(encodeURIComponent(canonicalName))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  return "w+CAIQICI" + key + b64;
}

const autoBtn = document.getElementById("autoSerpBtn");
if(autoBtn) {
    autoBtn.addEventListener("click", async () => {
        const query = document.getElementById("autoSerpQuery").value.trim();
        const rawLocation = document.getElementById("autoSerpLocation").value.trim(); // Get the Arabic typed text
        const canon = document.getElementById("autoSerpCanonical").value.trim();
        const status = document.getElementById("autoSerpStatus");
        const urlsContainer = document.getElementById("fetchedUrlsContainer");
        
        if(!query) { alert("Please enter a search keyword"); return; }
        
        let glParam = "eg"; // Default to Egypt
        if (canon && window._locationsData && window._locationsData.length > 0) {
            const match = window._locationsData.find(loc => loc[1] === canon);
            if (match && match[2]) {
                glParam = match[2].toLowerCase();
            }
        }

        autoBtn.disabled = true;
        autoBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Fetching...';
        status.style.display = "block";
        status.innerHTML = "Fetching top 40 results. This takes a few seconds...";
        if(urlsContainer) urlsContainer.style.display = "none";
        
        try {
            const res = await fetch("/api/scrape-serp", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    query: query,
                    location: rawLocation, // Send the original Arabic location (e.g. الرياض)
                    gl: glParam, // Send the country code (e.g. sa)
                    num: 40
                })
            });
            const data = await res.json();
            if(data.error) throw new Error(data.error);
            if(!data.urls || data.urls.length === 0) throw new Error("No URLs found.");
            
            window._fetchedSerpUrls = data.urls;
            
            if(urlsContainer) {
                let html = '<div style="font-weight:700; margin-bottom: 8px; font-size: 0.9rem;">Select competitors to analyze:</div>';
                data.urls.forEach((url, i) => {
                    const title = data.titles && data.titles[i] ? escapeHtml(data.titles[i]) : escapeHtml(url);
                    html += `<label style="display:flex; align-items:center; gap:8px; margin-bottom:6px; font-size:0.85rem; padding: 6px; background: var(--card-bg); border-radius: 4px; border: 1px solid var(--border); cursor: pointer;">
                        <input type="checkbox" class="competitor-cb" value="${escapeHtml(url)}" checked style="width: 16px; height: 16px;" />
                        <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
                          <strong>${title}</strong><br/>
                          <a href="${escapeHtml(url)}" target="_blank" style="color:var(--blue); font-size:0.75rem; text-decoration: none;">${escapeHtml(url)}</a>
                        </div>
                    </label>`;
                });
                urlsContainer.innerHTML = html;
                urlsContainer.style.display = "block";
            }
            
            status.innerHTML = `<i class="fa-solid fa-check" style="color:var(--signal)"></i> Fetched ${data.urls.length} URLs successfully! Review the list below, then click 'Start SERP Analysis & Outline'.`;
            status.style.color = "var(--signal)";
            
            document.getElementById("serpTopic").value = query;
        } catch(e) {
            status.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> ${e.message}`;
            status.style.color = "var(--rose)";
        } finally {
            autoBtn.disabled = false;
            autoBtn.innerHTML = '<i class="fa-solid fa-robot"></i> Fetch Competitors';
        }
    });
}

// --- SITEMAP INTEGRATION (smart internal linking) ---
function linkEntitiesFromAnalysis(data) {
    if (!data) return [];
    const pick = (list) => (list || []).map(e => ({ name: e.name, lsi: (e.lsi || []).slice(0, 8) }));
    const gaps = pick(data.semantic_gaps);
    const mine = pick([...(data.my_site_entities || [])].sort((a, b) => (b.salience || 0) - (a.salience || 0)).slice(0, 60));
    const combined = [...gaps, ...mine];
    const list = combined.length ? combined : pick((data.master_entities || []).slice(0, 80));
    const seen = new Set();
    return list.filter(e => e.name && !seen.has(e.name) && seen.add(e.name));
}

const fetchSitemapBtn = document.getElementById("fetchSitemapBtn");
if (fetchSitemapBtn) {
    const idleLabel = () => `<i class="fa-solid fa-bolt"></i> ${escapeHtml(t("link_button"))}`;
    fetchSitemapBtn.addEventListener("click", async () => {
        const sitemapUrl = document.getElementById("sitemapUrlInput").value.trim();
        const resultsDiv = document.getElementById("sitemapResults");
        if (!sitemapUrl) {
            showToast(t("link_enter_sitemap"), "error");
            return;
        }
        const entitiesToMatch = linkEntitiesFromAnalysis(_lastAnalysisData);
        if (entitiesToMatch.length === 0) {
            showToast(t("link_no_entities"), "error");
            return;
        }

        fetchSitemapBtn.disabled = true;
        fetchSitemapBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> ${escapeHtml(t("link_loading"))}`;
        resultsDiv.style.display = "block";
        resultsDiv.textContent = t("link_parsing");

        try {
            const res = await fetch("/api/sitemap-match", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    sitemap_url: sitemapUrl,
                    entities: entitiesToMatch,
                    exclude_url: (document.getElementById("myUrl")?.value || "").trim(),
                })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Failed to parse sitemap");

            let html = `<div style="color:var(--signal); font-weight:700; margin-bottom:1rem;">${escapeHtml(
                t("link_success", { total: data.total_urls, scanned: data.scanned_pages }))}</div>`;
            const entries = Object.entries(data.matches || {});
            if (!entries.length) {
                html += `<div>${escapeHtml(t("link_none"))}</div>`;
            } else {
                html += `<div class="table-scroll"><table class="data-table"><thead><tr><th>${escapeHtml(t("link_th_entity"))}</th><th>${escapeHtml(t("link_th_pages"))}</th></tr></thead><tbody>`;
                for (const [entity, pages] of entries) {
                    html += `<tr><td style="font-weight:700;">${escapeHtml(entity)}</td><td>`;
                    for (const page of pages) {
                        const safeUrl = /^https?:\/\//i.test(page.url) ? page.url : "#";
                        html += `<div style="margin-bottom:.5rem"><a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer" style="color:var(--blue); font-weight:600;">${escapeHtml(page.title || page.display_url || page.url)}</a>`
                              + `<div style="font-size:.75rem; color:var(--slate); direction:ltr; text-align:start">${escapeHtml(page.display_url || page.url)}</div>`
                              + `<div style="font-size:.75rem">${escapeHtml(t("link_anchor"))}: <strong>${escapeHtml(page.anchor || entity)}</strong></div></div>`;
                    }
                    html += `</td></tr>`;
                }
                html += "</tbody></table></div>";
            }
            resultsDiv.innerHTML = html;
        } catch (e) {
            resultsDiv.innerHTML = `<span style="color:var(--rose);"><i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(e.message)}</span>`;
        } finally {
            fetchSitemapBtn.disabled = false;
            fetchSitemapBtn.innerHTML = idleLabel();
        }
    });
}
