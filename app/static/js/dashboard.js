(function () {
  const COLORS = ["#6b7c93", "#8a939f", "#a8956e", "#7a9a82", "#9aa3ad"];
  const chartInstances = [];
  let activeJobId = null;
  let polling = false;

  function currentEntityId() {
    if (window.ENTITY_ID != null && window.ENTITY_ID !== "") {
      return Number(window.ENTITY_ID) || null;
    }
    var drawer = document.getElementById("source-drawer");
    var raw = drawer && drawer.getAttribute("data-entity-id");
    if (raw) return Number(raw) || null;
    return null;
  }

  function currentWindowHours() {
    if (typeof window.NEWS_WINDOW_HOURS === "number" && window.NEWS_WINDOW_HOURS > 0) {
      return window.NEWS_WINDOW_HOURS;
    }
    return 24;
  }

  function currentModuleCodes() {
    if (window.PIPELINE_MODULES && window.PIPELINE_MODULES.length) {
      return window.PIPELINE_MODULES.slice();
    }
    return [];
  }

  /** 与后端 job_scope 对齐：近24小时 / 7×24 / 主体各自独立 */
  function collectScopeKey() {
    var eid = currentEntityId();
    if (eid) return "entity:" + eid;
    var codes = currentModuleCodes().map(function (c) { return String(c).toUpperCase(); }).sort();
    var hours = currentWindowHours();
    if (codes.length === 1 && codes[0] === "A") return "entity:all";
    if (codes.length && codes.every(function (c) { return c === "B" || c === "C" || c === "D"; })) {
      return "news:" + hours;
    }
    if (codes.length) return "mod:" + hours + ":" + codes.join(",");
    return "news:" + hours;
  }

  function jobStorageKey() {
    return "pipeline_active_job_id:" + collectScopeKey();
  }

  function refreshMsgKey() {
    return "pipeline_collect_status_msg:" + collectScopeKey();
  }

  function refreshAtKey() {
    return "pipeline_collect_finished_at:" + collectScopeKey();
  }

  function runningQueryUrl() {
    var params = [];
    var hours = currentWindowHours();
    var eid = currentEntityId();
    var codes = currentModuleCodes();
    if (hours) params.push("window_hours=" + encodeURIComponent(hours));
    if (eid) params.push("entity_id=" + encodeURIComponent(eid));
    if (codes.length) params.push("module_codes=" + encodeURIComponent(codes.join(",")));
    return "/api/v1/pipeline/running" + (params.length ? "?" + params.join("&") : "");
  }

  function dataSourcesUrl() {
    var eid = currentEntityId();
    return eid ? "/api/v1/data-sources?entity_id=" + eid : "/api/v1/data-sources";
  }

  function toEchartsOption(spec) {
    const chartType = spec.type || "bar";
    const labels = spec.labels || [];
    const seriesRaw = spec.series || [];
    const series = seriesRaw.map(function (s, idx) {
      return {
        name: s.name || "系列" + (idx + 1),
        type: chartType === "line" ? "line" : "bar",
        data: s.data || [],
        smooth: chartType === "line",
        itemStyle: { color: COLORS[idx % COLORS.length] },
      };
    });
    return {
      title: { text: spec.title || "", left: "center", textStyle: { color: "#4a5160", fontSize: 13 } },
      tooltip: { trigger: chartType === "line" ? "axis" : "item" },
      grid: { left: "8%", right: "4%", bottom: "12%", containLabel: true },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { color: "#7a828e", rotate: labels.length > 6 ? 30 : 0 },
      },
      yAxis: { type: "value", axisLabel: { color: "#7a828e" } },
      series: series,
      color: COLORS,
    };
  }

  function renderEntryCharts() {
    if (!window.echarts || !window.ENTRY_CHARTS) return;
    window.ENTRY_CHARTS.forEach(function (item) {
      const host = document.getElementById("chart-entry-" + item.entry_id);
      if (!host || !item.specs || !item.specs.length) return;
      host.style.height = "220px";
      host.style.marginTop = "0.75rem";
      const chart = echarts.init(host);
      const spec = item.specs[0];
      chart.setOption(toEchartsOption(spec));
      chartInstances.push(chart);
    });
    if (!window.__riskChartsResizeBound) {
      window.__riskChartsResizeBound = true;
      window.addEventListener("resize", function () {
        chartInstances.forEach(function (c) {
          try { c.resize(); } catch (e) { /* ignore */ }
        });
      });
    }
  }

  /* ---------- 数据源面板 / 关于（仅深度研报页存在） ---------- */
  const drawer = document.getElementById("source-drawer");
  const toggleBtn = document.getElementById("btn-toggle-source-drawer");
  const aboutModal = document.getElementById("about-modal");
  const aboutOpen = document.getElementById("btn-open-about");
  const aboutClose = document.getElementById("btn-close-about");
  const addBox = document.getElementById("sources-add-box");
  const addToggle = document.getElementById("btn-toggle-add-sources");
  const emptyAdd = document.getElementById("btn-empty-add-source");
  const STORAGE_KEY = "sources_panel_collapsed";
  const listWrap = document.getElementById("managed-source-list-wrap");
  const fileInput = document.getElementById("global-upload-file");
  const dropzone = document.getElementById("sources-dropzone");
  const uploadNameInput = document.getElementById("global-upload-name");
  const uploadSubmitBtn = document.getElementById("global-upload-submit");
  const ALLOWED_EXTS = [".txt", ".xlsx", ".docx", ".pdf"];

  if (drawer) {
  function isDrawerOpen() {
    return !document.body.classList.contains("sources-collapsed");
  }

  function setDrawerOpen(open) {
    if (!drawer) return;
    document.body.classList.toggle("sources-collapsed", !open);
    drawer.setAttribute("aria-hidden", open ? "false" : "true");
    if (toggleBtn) {
      toggleBtn.setAttribute("aria-expanded", open ? "true" : "false");
      toggleBtn.title = open ? "收起数据源面板" : "展开数据源面板";
      toggleBtn.setAttribute("aria-label", open ? "收起数据源面板" : "展开数据源面板");
    }
    try {
      localStorage.setItem(STORAGE_KEY, open ? "0" : "1");
    } catch (e) { /* ignore */ }
  }

  function setAddBoxOpen(open) {
    if (!addBox) return;
    addBox.hidden = !open;
  }

  function setAboutOpen(open) {
    if (!aboutModal) return;
    aboutModal.classList.toggle("hidden", !open);
    aboutModal.hidden = !open;
  }

  try {
    if (localStorage.getItem(STORAGE_KEY) === "1") setDrawerOpen(false);
    else setDrawerOpen(true);
  } catch (e) {
    setDrawerOpen(true);
  }

  if (toggleBtn) {
    toggleBtn.addEventListener("click", function () {
      setDrawerOpen(!isDrawerOpen());
    });
  }
  if (addToggle) addToggle.addEventListener("click", function () {
    setAddBoxOpen(addBox ? addBox.hidden : true);
  });
  if (emptyAdd) emptyAdd.addEventListener("click", function () {
    setDrawerOpen(true);
    setAddBoxOpen(true);
  });
  if (aboutOpen) aboutOpen.addEventListener("click", function () { setAboutOpen(true); });
  if (aboutClose) aboutClose.addEventListener("click", function () { setAboutOpen(false); });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      setAboutOpen(false);
      var svm = document.getElementById("source-view-modal");
      if (svm) { svm.classList.add("hidden"); svm.hidden = true; }
    }
  });

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function showSourceToast(text) {
    var el = document.getElementById("sources-toast");
    if (!el && listWrap) {
      el = document.createElement("p");
      el.id = "sources-toast";
      el.className = "sources-toast hint";
      listWrap.parentNode.insertBefore(el, listWrap);
    }
    if (!el) return;
    el.textContent = text;
    el.hidden = false;
    clearTimeout(el._hideTimer);
    el._hideTimer = setTimeout(function () {
      el.hidden = true;
    }, 4500);
  }

  function fileExtOk(name) {
    var lower = String(name || "").toLowerCase();
    return ALLOWED_EXTS.some(function (ext) { return lower.endsWith(ext); });
  }

  function syncUploadButton() {
    if (!uploadSubmitBtn || !fileInput) return;
    uploadSubmitBtn.disabled = !(fileInput.files && fileInput.files.length);
    if (fileInput.files && fileInput.files[0] && dropzone) {
      var title = dropzone.querySelector(".sources-dropzone-title");
      if (title) title.textContent = "已选择：" + fileInput.files[0].name;
    }
  }

  async function uploadSelectedFile(file, optionalName) {
    if (!file) throw new Error("请先选择文件");
    if (!fileExtOk(file.name)) {
      throw new Error("不支持的文件类型，允许: " + ALLOWED_EXTS.join(", "));
    }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", optionalName || (uploadNameInput && uploadNameInput.value) || file.name);
    var eid = currentEntityId();
    if (eid) fd.append("entity_id", String(eid));
    const resp = await fetch("/api/v1/data-sources/upload", { method: "POST", body: fd });
    const data = await resp.json().catch(function () { return {}; });
    if (!resp.ok) {
      var detail = data.detail;
      if (Array.isArray(detail)) detail = detail.map(function (d) { return d.msg || d; }).join("; ");
      throw new Error(detail || "上传失败");
    }
    return data;
  }

  function bindDropTarget(el) {
    if (!el) return;
    ["dragenter", "dragover"].forEach(function (evt) {
      el.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        el.classList.add("is-dragover");
      });
    });
    ["dragleave", "drop"].forEach(function (evt) {
      el.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        el.classList.remove("is-dragover");
      });
    });
    el.addEventListener("drop", async function (e) {
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      setDrawerOpen(true);
      setAddBoxOpen(true);
      try {
        const data = await uploadSelectedFile(files[0]);
        showSourceToast(data.message || "数据源已上传");
        if (fileInput) fileInput.value = "";
        if (uploadNameInput) uploadNameInput.value = "";
        syncUploadButton();
        if (dropzone) {
          var title = dropzone.querySelector(".sources-dropzone-title");
          if (title) title.textContent = "点击或拖拽文件到此处上传";
        }
        await refreshSourceList();
      } catch (err) {
        alert(err.message || String(err));
      }
    });
  }

  if (fileInput) {
    fileInput.addEventListener("change", function () {
      syncUploadButton();
    });
  }
  bindDropTarget(dropzone);
  bindDropTarget(listWrap);

  var chipFile = document.getElementById("chip-pick-file");
  var chipUrl = document.getElementById("chip-focus-url");
  if (chipFile && fileInput) {
    chipFile.addEventListener("click", function () {
      setAddBoxOpen(true);
      fileInput.click();
    });
  }
  if (chipUrl) {
    chipUrl.addEventListener("click", function () {
      setAddBoxOpen(true);
      var urlInput = document.querySelector("#global-url-form [name='url']");
      if (urlInput) urlInput.focus();
    });
  }

  function bindSourceItemActions(root) {
    (root || document).querySelectorAll(".view-source").forEach(function (btn) {
      if (btn._bound) return;
      btn._bound = true;
      btn.addEventListener("click", onViewSource);
    });
    (root || document).querySelectorAll(".delete-source").forEach(function (btn) {
      if (btn._bound) return;
      btn._bound = true;
      btn.addEventListener("click", onDeleteSource);
    });
  }

  function renderSourceList(sources) {
    if (!listWrap) return;
    if (!sources || !sources.length) {
      listWrap.innerHTML =
        '<div class="sources-empty" id="managed-source-list">' +
        '<p class="sources-empty-title">保存的数据源将显示在此处</p>' +
        '<p class="sources-empty-desc">可添加文件、网站等。生成深度研报时会优先参考这些权威材料。</p>' +
        '<p class="sources-empty-hint">将文件拖放到此处，或 ' +
        '<button type="button" class="text-link" id="btn-empty-add-source-dyn">添加数据源</button></p>' +
        "</div>";
      var dyn = document.getElementById("btn-empty-add-source-dyn");
      if (dyn) {
        dyn.addEventListener("click", function () {
          setDrawerOpen(true);
          setAddBoxOpen(true);
        });
      }
      return;
    }
    var html = '<ul class="managed-source-list" id="managed-source-list">';
    sources.forEach(function (src) {
      var meta = [src.source_type || "-"];
      if (src.original_filename) meta.push(src.original_filename);
      if (src.url) meta.push("网址");
      if (typeof src.chars === "number") meta.push(src.chars + " 字");
      else if (src.text_preview) meta.push("有正文");
      html +=
        '<li class="managed-source-item" data-id="' + src.id + '">' +
        '<button type="button" class="managed-source-main view-source" data-id="' + src.id + '" title="点击查看">' +
        '<span class="source-name">' + escapeHtml(src.name) + "</span>" +
        '<span class="source-meta">' + escapeHtml(meta.join(" · ")) + "</span>" +
        "</button>" +
        '<button type="button" class="link-btn delete-source" data-id="' + src.id + '">删除</button>' +
        "</li>";
    });
    html += "</ul>";
    listWrap.innerHTML = html;
    bindSourceItemActions(listWrap);
  }

  async function refreshSourceList() {
    try {
      const resp = await fetch(dataSourcesUrl());
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "刷新失败");
      renderSourceList(data);
    } catch (e) {
      showSourceToast("列表刷新失败: " + e.message);
    }
  }

  async function handleSourceMutation(promiseFactory) {
    try {
      const data = await promiseFactory();
      showSourceToast(data.message || "已保存");
      await refreshSourceList();
      setAddBoxOpen(false);
    } catch (e) {
      alert(e.message || String(e));
    }
  }

  document.querySelectorAll(".upload-form").forEach(function (form) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const moduleCode = form.getAttribute("data-module");
      const fd = new FormData(form);
      if (moduleCode) fd.append("module_code", moduleCode);
      await handleSourceMutation(async function () {
        const resp = await fetch("/api/v1/data-sources/upload", { method: "POST", body: fd });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "上传失败");
        return data;
      });
      form.reset();
    });
  });

  const globalUpload = document.getElementById("global-upload-form");
  if (globalUpload) {
    globalUpload.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      if (uploadSubmitBtn) uploadSubmitBtn.disabled = true;
      try {
        await handleSourceMutation(async function () {
          if (!fileInput || !fileInput.files || !fileInput.files[0]) {
            throw new Error("请先选择或拖入文件");
          }
          return uploadSelectedFile(fileInput.files[0]);
        });
        if (fileInput) fileInput.value = "";
        if (uploadNameInput) uploadNameInput.value = "";
        if (dropzone) {
          var title = dropzone.querySelector(".sources-dropzone-title");
          if (title) title.textContent = "点击或拖拽文件到此处上传";
        }
      } finally {
        syncUploadButton();
      }
    });
  }

  document.querySelectorAll(".url-form").forEach(function (form) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const moduleCode = form.getAttribute("data-module");
      const name = form.querySelector('[name="name"]').value;
      const url = form.querySelector('[name="url"]').value;
      const body = { name: name, url: url, priority: 0 };
      if (moduleCode) body.module_code = moduleCode;
      await handleSourceMutation(async function () {
        const resp = await fetch("/api/v1/data-sources/url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "添加失败");
        return data;
      });
      form.reset();
    });
  });

  const globalUrl = document.getElementById("global-url-form");
  if (globalUrl) {
    globalUrl.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const submitBtn = globalUrl.querySelector('button[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;
      try {
        const name = globalUrl.querySelector('[name="name"]').value;
        const url = globalUrl.querySelector('[name="url"]').value;
        await handleSourceMutation(async function () {
          const body = { name: name, url: url, priority: 0 };
          var eid = currentEntityId();
          if (eid) body.entity_id = eid;
          const resp = await fetch("/api/v1/data-sources/url", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          const data = await resp.json();
          if (!resp.ok) throw new Error(data.detail || "添加失败");
          return data;
        });
        globalUrl.reset();
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

  async function onDeleteSource(ev) {
    const btn = ev.currentTarget;
    const id = btn.getAttribute("data-id");
    if (!confirm("确认删除该数据源？")) return;
    await handleSourceMutation(async function () {
      const resp = await fetch("/api/v1/data-sources/" + id, { method: "DELETE" });
      const data = await resp.json().catch(function () { return {}; });
      if (!resp.ok) throw new Error(data.detail || "删除失败");
      return data;
    });
  }

  document.querySelectorAll(".delete-source").forEach(function (btn) {
    btn._bound = true;
    btn.addEventListener("click", onDeleteSource);
  });

  const sourceViewModal = document.getElementById("source-view-modal");
  const sourceViewBody = document.getElementById("source-view-body");
  const sourceViewTitle = document.getElementById("source-view-title");
  const sourceViewClose = document.getElementById("btn-close-source-view");

  function setSourceViewOpen(open) {
    if (!sourceViewModal) return;
    sourceViewModal.classList.toggle("hidden", !open);
    sourceViewModal.hidden = !open;
  }

  if (sourceViewClose) sourceViewClose.addEventListener("click", function () { setSourceViewOpen(false); });

  async function onViewSource(ev) {
    const btn = ev.currentTarget;
    const id = btn.getAttribute("data-id");
    setSourceViewOpen(true);
    if (sourceViewBody) sourceViewBody.innerHTML = '<p class="hint">加载中…</p>';
    try {
      const resp = await fetch("/api/v1/data-sources/item/" + id);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "加载失败");
      if (sourceViewTitle) sourceViewTitle.textContent = data.name || "数据详情";
      const meta = [
        "类型：" + (data.source_type || "-"),
        data.original_filename ? ("文件：" + data.original_filename) : "",
        data.url ? ("链接：" + data.url) : "",
        "字数：" + ((data.extracted_text || "").length),
      ].filter(Boolean).join("<br/>");
      const text = escapeHtml(data.extracted_text || data.text_preview || "（无提取正文）");
      if (sourceViewBody) {
        sourceViewBody.innerHTML =
          '<p class="meta">' + meta + "</p>" +
          '<pre class="source-text-preview">' + text + "</pre>";
      }
    } catch (e) {
      if (sourceViewBody) sourceViewBody.innerHTML = '<p class="empty-module">加载失败：' + escapeHtml(e.message) + "</p>";
    }
  }

  document.querySelectorAll(".view-source").forEach(function (btn) {
    btn._bound = true;
    btn.addEventListener("click", onViewSource);
  });

  const industryUpload = document.getElementById("drawer-industry-upload-form");
  if (industryUpload) {
    industryUpload.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      alert("请使用上方统一上传入口");
    });
  }

  const industryUrl = document.getElementById("drawer-industry-url-form");
  if (industryUrl) {
    industryUrl.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      alert("请使用上方统一添加网址入口");
    });
  }

  document.querySelectorAll(".delete-industry-source").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      const id = btn.getAttribute("data-id");
      if (!confirm("确认删除该行业数据源？")) return;
      const resp = await fetch("/api/v1/industry/data-sources/" + id, { method: "DELETE" });
      if (resp.ok) {
        showSourceToast("已删除");
        await refreshSourceList();
      } else alert("删除失败");
    });
  });
  } // end: source drawer present

  /* ---------- 流水线（异步 + 轮询，不阻断侧栏/上传） ---------- */
  const pipelineBtn = document.getElementById("btn-run-pipeline");

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function setCollectBusy(busy) {
    // 仅禁用本页采集按钮，避免重复提交；其它界面操作保持可用
    if (pipelineBtn) pipelineBtn.disabled = !!busy;
  }

  function rememberJob(jobId) {
    activeJobId = jobId || null;
    try {
      if (jobId) sessionStorage.setItem(jobStorageKey(), jobId);
      else sessionStorage.removeItem(jobStorageKey());
    } catch (e) { /* ignore */ }
  }

  function pipelineMsgEl() {
    return document.getElementById("pipeline-msg");
  }

  /** 东京时间（Asia/Tokyo）24 小时制：YYYY-MM-DD HH:mm:ss */
  function formatRefreshTime(isoOrDate) {
    var d;
    if (isoOrDate instanceof Date) {
      d = isoOrDate;
    } else if (isoOrDate) {
      var s = String(isoOrDate).trim();
      // 后端返回东京墙钟；无时区后缀时按 +09:00 解析，避免被当作浏览器本地/UTC
      if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s) && !/(Z|[+-]\d{2}:?\d{2})$/i.test(s)) {
        s = s.replace(" ", "T") + "+09:00";
      }
      d = new Date(s);
    } else {
      d = new Date();
    }
    if (isNaN(d.getTime())) d = new Date();

    var parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Tokyo",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(d);

    var map = {};
    parts.forEach(function (p) {
      if (p.type !== "literal") map[p.type] = p.value;
    });
    return (
      map.year + "-" + map.month + "-" + map.day +
      " " + map.hour + ":" + map.minute + ":" + map.second
    );
  }

  function persistPipelineMsg(text) {
    try { sessionStorage.setItem(refreshMsgKey(), text); } catch (e) { /* ignore */ }
  }

  function persistRefreshAt(isoOrDate) {
    try {
      if (!isoOrDate) {
        sessionStorage.setItem(refreshAtKey(), new Date().toISOString());
        return;
      }
      if (isoOrDate instanceof Date) {
        sessionStorage.setItem(refreshAtKey(), isoOrDate.toISOString());
        return;
      }
      sessionStorage.setItem(refreshAtKey(), String(isoOrDate));
    } catch (e) { /* ignore */ }
  }

  function setPipelineMsg(msgEl, text) {
    if (msgEl) msgEl.textContent = text;
    persistPipelineMsg(text);
  }

  function setCollectingMsg(msgEl) {
    setPipelineMsg(msgEl, "采集中...");
  }

  function setRefreshSuccessMsg(msgEl, finishedAt) {
    setPipelineMsg(msgEl, "最近刷新时间（" + formatRefreshTime(finishedAt) + "）");
    persistRefreshAt(finishedAt);
  }

  function setCollectFailMsg(msgEl) {
    setPipelineMsg(msgEl, "采集失败，请重新尝试。");
  }

  function whenPageGlobalsReady(fn) {
    // base.html 先加载本文件，再执行 {% block scripts %} 注入 window.*；
    // 依赖全局变量的初始化必须延后，否则补采/恢复轮询会静默跳过。
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      setTimeout(fn, 0);
    }
  }

  whenPageGlobalsReady(function () {
    (function restorePipelineMsg() {
      var msg = pipelineMsgEl();
      if (!msg) return;
      try {
        var at = sessionStorage.getItem(refreshAtKey());
        if (at) {
          setRefreshSuccessMsg(msg, at);
          return;
        }
        var saved = sessionStorage.getItem(refreshMsgKey());
        if (!saved) return;
        saved = saved.replace("最近更新时间", "最近刷新时间");
        // 旧版文案：把 UTC 墙钟误标为本地时间，恢复时按 UTC 转东京
        var m = saved.match(/^最近刷新时间[（(](\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})[）)]$/);
        if (m) {
          setRefreshSuccessMsg(msg, m[1].replace(" ", "T") + "Z");
          return;
        }
        msg.textContent = saved;
      } catch (e) { /* ignore */ }
    })();
  });

  async function pollJob(jobId, msgEl, maxRounds) {
    const rounds = maxRounds || 900; // ~30 分钟（2s 间隔），长任务不再误报超时
    for (let i = 0; i < rounds; i++) {
      const resp = await fetch("/api/v1/pipeline/jobs/" + jobId);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "无法查询任务状态");
      const status = data.status || "";
      if (msgEl) setCollectingMsg(msgEl);
      if (status === "completed" || status === "failed") {
        return data;
      }
      // 前 30 次 2s，之后 3s，减轻轮询压力
      await sleep(i < 30 ? 2000 : 3000);
    }
    throw new Error("等待超时，任务可能仍在后台运行，请稍后刷新页面查看结果");
  }

  async function trackJob(jobId, msgEl) {
    if (polling) return;
    polling = true;
    rememberJob(jobId);
    setCollectBusy(true);
    const target = msgEl || pipelineMsgEl();
    try {
      const done = await pollJob(jobId, target);
      rememberJob(null);
      if (done.status === "failed") {
        setCollectFailMsg(target);
      } else {
        setRefreshSuccessMsg(target, done.finished_at || null);
      }
      // 仅在采集结束后刷新页面以展示新资讯；状态文案经 sessionStorage 恢复
      setTimeout(function () { window.location.reload(); }, 1200);
      return done;
    } catch (e) {
      rememberJob(null);
      setCollectBusy(false);
      setCollectFailMsg(target);
      throw e;
    } finally {
      polling = false;
    }
  }

  async function runPipeline(moduleCodes, msgEl) {
    const target = msgEl || pipelineMsgEl();
    setCollectingMsg(target);
    const payload = { report_date: window.REPORT_DATE || null, async_mode: true };
    payload.window_hours = currentWindowHours();
    if (moduleCodes && moduleCodes.length) {
      payload.module_codes = moduleCodes;
    } else if (window.PIPELINE_MODULES && window.PIPELINE_MODULES.length) {
      payload.module_codes = window.PIPELINE_MODULES;
    }
    var eid = currentEntityId();
    if (eid) payload.entity_id = eid;
    const resp = await fetch("/api/v1/pipeline/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resp.status === 409) {
      const detail = data.detail || {};
      const existingId = detail.job_id;
      // 仅跟随本作用域冲突任务，不接管其它界面的采集
      if (existingId) {
        setCollectingMsg(target);
        return trackJob(existingId, target);
      }
      throw new Error(detail.message || data.detail || "任务冲突");
    }
    if (!resp.ok) {
      const detail = typeof data.detail === "string" ? data.detail : (data.detail && data.detail.message) || "执行失败";
      throw new Error(detail);
    }
    if (data.async_mode && data.job_id) {
      setCollectingMsg(target);
      return trackJob(data.job_id, target);
    }
    setRefreshSuccessMsg(target, null);
    setTimeout(function () { window.location.reload(); }, 1200);
    return data;
  }

  if (pipelineBtn) {
    pipelineBtn.addEventListener("click", async function () {
      const msg = pipelineMsgEl();
      setCollectBusy(true);
      try {
        await runPipeline(null, msg);
      } catch (e) {
        setCollectFailMsg(msg);
        setCollectBusy(false);
      }
    });
  }

  document.querySelectorAll(".btn-reload-module").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      const code = btn.getAttribute("data-module");
      const panel = document.getElementById("module-panel-" + code);
      const statusBox = panel ? panel.querySelector(".module-status") : null;
      setCollectBusy(true);
      if (statusBox) {
        const p = statusBox.querySelector(".empty-module");
        if (p) p.textContent = "正在重新加载…";
      }
      try {
        await runPipeline([code], pipelineMsgEl());
      } catch (e) {
        setCollectBusy(false);
        if (statusBox) {
          const p = statusBox.querySelector(".empty-module");
          if (p) p.textContent = "请求失败，请重新加载";
        }
        alert("重新加载失败: " + e.message);
      }
    });
  });

  function scrollToModulePanel(code) {
    var panel = document.getElementById("module-panel-" + code);
    if (!panel) return false;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    return true;
  }

  function moduleJumpUrl(code) {
    var rail = document.querySelector(".page-rail[data-page-path]");
    var pagePath = (rail && rail.getAttribute("data-page-path")) || window.location.pathname;
    var reportDate = window.REPORT_DATE || "";
    var qs = reportDate ? "?report_date=" + encodeURIComponent(reportDate) : "";
    return pagePath + qs + "#module-panel-" + encodeURIComponent(code);
  }

  document.querySelectorAll("a.mod-jump").forEach(function (link) {
    link.addEventListener("click", function (ev) {
      var code = link.getAttribute("data-module");
      if (!code) return;
      if (scrollToModulePanel(code)) {
        ev.preventDefault();
        if (history.replaceState) {
          history.replaceState(null, "", "#module-panel-" + code);
        }
        return;
      }
      // 当前筛选未展示该模块时，回到全量视图再定位
      ev.preventDefault();
      window.location.href = moduleJumpUrl(code);
    });
  });

  (function scrollToHashModulePanel() {
    var hash = window.location.hash || "";
    var match = hash.match(/^#module-panel-(.+)$/);
    if (!match) return;
    setTimeout(function () {
      scrollToModulePanel(decodeURIComponent(match[1]));
    }, 50);
  })();

  // 页面加载时若有本作用域未完成任务，恢复轮询（不阻断侧栏/其它界面）
  whenPageGlobalsReady(async function resumeActiveJob() {
    try {
      const resp = await fetch(runningQueryUrl());
      const cur = await resp.json();
      let jobId = cur && cur.job_id ? cur.job_id : null;
      if (!jobId) {
        try { jobId = sessionStorage.getItem(jobStorageKey()); } catch (e) { jobId = null; }
      }
      // 兼容旧版全局 key，避免遗留“采集中”幽灵轮询
      if (!jobId) {
        try {
          jobId = sessionStorage.getItem("pipeline_active_job_id");
          if (jobId) sessionStorage.removeItem("pipeline_active_job_id");
        } catch (e) { jobId = null; }
      }
      if (!jobId) return;

      // 确认任务仍在排队/运行（服务重启后遗留 id 会被清掉）
      const jr = await fetch("/api/v1/pipeline/jobs/" + jobId);
      const jd = await jr.json();
      if (!jr.ok || (jd.status !== "queued" && jd.status !== "running")) {
        rememberJob(null);
        // 任务已结束：清掉可能残留的「采集中...」文案
        try {
          var stale = sessionStorage.getItem(refreshMsgKey());
          if (stale && stale.indexOf("采集中") === 0) {
            sessionStorage.removeItem(refreshMsgKey());
            var msgDone = pipelineMsgEl();
            if (msgDone && msgDone.textContent && msgDone.textContent.indexOf("采集中") === 0) {
              if (jd.status === "completed" && jd.finished_at) {
                setRefreshSuccessMsg(msgDone, jd.finished_at);
              } else if (jd.status === "failed") {
                setCollectFailMsg(msgDone);
              }
            }
          }
        } catch (e2) { /* ignore */ }
        return;
      }
      // 任务作用域须与当前页一致，避免接管其它界面采集
      var expected = collectScopeKey();
      var jobScope = jd.scope || (jd.snapshot && jd.snapshot.scope) || null;
      if (jobScope && jobScope !== expected) {
        rememberJob(null);
        return;
      }
      if (!jobScope) {
        var jobHours = Number(jd.window_hours || 24);
        var jobEntity = jd.entity_id != null ? Number(jd.entity_id) : null;
        if (jobEntity && jobEntity !== currentEntityId()) {
          rememberJob(null);
          return;
        }
        if (!jobEntity && jobHours !== currentWindowHours()) {
          rememberJob(null);
          return;
        }
      }

      const msg = pipelineMsgEl();
      setCollectingMsg(msg);
      trackJob(jobId, msg).catch(function () { /* 已在 trackJob 内提示 */ });
    } catch (e) { /* ignore */ }
  });

  // 后台整点采集：数据采完后刷新主内容；手动采集中则不打断
  whenPageGlobalsReady(function syncBackgroundRefreshTime() {
    var page = window.ACTIVE_PAGE || "";
    if (page !== "daily_news" && page !== "news_7x24") return;
    if (currentEntityId()) return;

    var lastFinishedAt = null;
    var bootstrapped = false;
    var CONTENT_REFRESH_KEY = "pipeline_content_refreshed_at:" + collectScopeKey();

    function isUserCollectingMsg(text) {
      var t = String(text || "");
      return t.indexOf("采集中") === 0 || t.indexOf("采集失败") === 0;
    }

    function userCollectBusy() {
      if (polling) return true;
      var msg = pipelineMsgEl();
      return !!(msg && isUserCollectingMsg(msg.textContent));
    }

    function softReloadForNewData(finishedAt) {
      try {
        var prev = sessionStorage.getItem(CONTENT_REFRESH_KEY);
        if (prev && prev === String(finishedAt)) return;
        sessionStorage.setItem(CONTENT_REFRESH_KEY, String(finishedAt));
      } catch (e) { /* ignore */ }
      // 仅刷新当前页主内容；不改 URL，不抢手动采集
      window.location.reload();
    }

    async function tick() {
      if (userCollectBusy()) return;
      try {
        var hours = currentWindowHours() || 24;
        var codes = (currentModuleCodes() || []).join(",") || "B,C,D";
        var resp = await fetch(
          "/api/v1/pipeline/last-refresh?window_hours=" +
            encodeURIComponent(hours) +
            "&module_codes=" +
            encodeURIComponent(codes)
        );
        var data = await resp.json();
        if (!resp.ok || !data) return;
        // 后台整点任务进行中：不锁按钮、不改「采集中」文案，等结束后再同步
        if (data.running) return;
        if (!data.finished_at) return;
        if (userCollectBusy()) return;

        var finishedAt = String(data.finished_at);
        var msg = pipelineMsgEl();
        setRefreshSuccessMsg(msg, finishedAt);

        if (!bootstrapped) {
          bootstrapped = true;
          lastFinishedAt = finishedAt;
          return;
        }
        if (lastFinishedAt && lastFinishedAt !== finishedAt) {
          lastFinishedAt = finishedAt;
          softReloadForNewData(finishedAt);
          return;
        }
        lastFinishedAt = finishedAt;
      } catch (e) { /* ignore */ }
    }

    tick();
    setInterval(tick, 30000);
  });

  // 7×24：某日无快照时自动补采并写回数据库
  whenPageGlobalsReady(function autoBackfillDaySnapshot() {
    if (!window.AUTO_BACKFILL) return;
    if (window.ACTIVE_PAGE !== "news_7x24") return;
    var key = "news_7x24_backfill:" + (window.REPORT_DATE || "");
    try {
      if (sessionStorage.getItem(key) === "1") return;
      sessionStorage.setItem(key, "1");
    } catch (e) { /* ignore */ }
    var msg = pipelineMsgEl();
    if (msg) setCollectingMsg(msg);
    setCollectBusy(true);
    runPipeline(null, msg).catch(function () {
      setCollectBusy(false);
      setCollectFailMsg(msg);
      try { sessionStorage.removeItem(key); } catch (e2) { /* ignore */ }
    });
  });

  renderEntryCharts();
})();
