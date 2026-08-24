/**
 * 主体评估页：导出 Word（Loading）与页面级辅助逻辑。
 * 采集流水线 / 数据源上传由 dashboard.js 统一处理，并读取 window.ENTITY_ID。
 */
(function () {
  var exportBtn = document.getElementById("btn-export-entity-docx");
  var exportMsg = document.getElementById("export-msg");

  function setExportLoading(on) {
    if (!exportBtn) return;
    exportBtn.disabled = !!on || !window.ENTITY_ID;
    exportBtn.classList.toggle("is-loading", !!on);
    exportBtn.textContent = on ? "正在生成…" : "导出公开信息简报";
  }

  function showExportMsg(text, isError) {
    if (!exportMsg) return;
    exportMsg.hidden = !text;
    exportMsg.textContent = text || "";
    exportMsg.classList.toggle("is-error", !!isError);
  }

  async function exportEntityDocx() {
    var entityId = window.ENTITY_ID || (exportBtn && exportBtn.getAttribute("data-entity-id"));
    if (!entityId) {
      showExportMsg("请先选择监控主体", true);
      return;
    }
    var reportDate = window.REPORT_DATE || (exportBtn && exportBtn.getAttribute("data-report-date")) || "";
    var url = "/api/v1/entities/" + entityId + "/export/docx";
    if (reportDate) url += "?report_date=" + encodeURIComponent(reportDate);

    setExportLoading(true);
    showExportMsg("正在生成《企业公开信息风险监测简报》…");
    try {
      var resp = await fetch(url);
      if (!resp.ok) {
        var errBody = await resp.json().catch(function () { return {}; });
        var detail = errBody.detail || ("导出失败 (" + resp.status + ")");
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      var blob = await resp.blob();
      var disp = resp.headers.get("Content-Disposition") || "";
      var filename = "企业公开信息风险监测简报.docx";
      var m = /filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?/i.exec(disp);
      if (m) {
        filename = decodeURIComponent(m[1] || m[2]);
      } else if (window.ENTITY_NAME) {
        filename = "企业公开信息风险监测简报_" + window.ENTITY_NAME + ".docx";
      }
      var a = document.createElement("a");
      var objectUrl = URL.createObjectURL(blob);
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
      showExportMsg("导出完成：" + filename);
      setTimeout(function () { showExportMsg(""); }, 4000);
    } catch (e) {
      showExportMsg("导出失败：" + (e.message || String(e)), true);
    } finally {
      setExportLoading(false);
    }
  }

  if (exportBtn) {
    exportBtn.addEventListener("click", function () {
      exportEntityDocx();
    });
  }

  var searchInput = document.getElementById("entity-search-input");
  var searchClear = document.getElementById("entity-search-clear");
  var searchEmpty = document.getElementById("entity-search-empty");
  var indexScroll = document.getElementById("entity-index-scroll");
  var groups = indexScroll
    ? Array.prototype.slice.call(indexScroll.querySelectorAll(".entity-index-group"))
    : [];
  var collapsedKey = "entity-group-collapsed";

  function currentQuery() {
    return (searchInput && searchInput.value ? searchInput.value : "").trim().toLowerCase();
  }

  function loadCollapsed() {
    try {
      var raw = window.localStorage.getItem(collapsedKey);
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function saveCollapsed(keys) {
    try {
      window.localStorage.setItem(collapsedKey, JSON.stringify(keys));
    } catch (e) {
      /* ignore quota / private mode */
    }
  }

  function setGroupCollapsed(group, collapsed) {
    group.classList.toggle("is-collapsed", collapsed);
    var head = group.querySelector(".entity-index-group-head");
    if (head) head.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }

  function applyStoredCollapse() {
    var collapsed = loadCollapsed();
    var activeGroup = groups.find(function (group) {
      return group.querySelector(".entity-link.active");
    });
    groups.forEach(function (group) {
      var key = group.getAttribute("data-group") || "";
      var shouldCollapse = collapsed.indexOf(key) !== -1 && group !== activeGroup;
      setGroupCollapsed(group, shouldCollapse);
    });
  }

  function applyEntitySearch() {
    var query = currentQuery();
    if (searchClear) searchClear.hidden = !query;
    var visibleCount = 0;

    groups.forEach(function (group) {
      var items = group.querySelectorAll(".entity-index-item");
      var matched = 0;
      items.forEach(function (item) {
        var haystack = (item.getAttribute("data-search") || "").toLowerCase();
        var show = !query || haystack.indexOf(query) !== -1;
        item.hidden = !show;
        if (show) matched += 1;
      });
      var isEmptyGroup = group.getAttribute("data-empty") === "1";
      var showGroup = query ? matched > 0 : true;
      group.hidden = !showGroup;
      if (showGroup) visibleCount += 1;
      var emptyRow = group.querySelector(".entity-index-empty");
      if (emptyRow) emptyRow.hidden = !!query && isEmptyGroup;
      if (query && matched > 0) setGroupCollapsed(group, false);
    });

    if (!query) applyStoredCollapse();
    if (searchEmpty) searchEmpty.hidden = visibleCount > 0 || groups.length === 0;
  }

  groups.forEach(function (group) {
    var head = group.querySelector(".entity-index-group-head");
    if (!head) return;
    head.addEventListener("click", function () {
      if (currentQuery()) return;
      var next = !group.classList.contains("is-collapsed");
      setGroupCollapsed(group, next);
      var key = group.getAttribute("data-group") || "";
      var collapsed = loadCollapsed().filter(function (item) { return item !== key; });
      if (next) collapsed.push(key);
      saveCollapsed(collapsed);
    });
  });

  if (searchInput) {
    searchInput.addEventListener("input", applyEntitySearch);
  }
  if (searchClear) {
    searchClear.addEventListener("click", function () {
      if (searchInput) searchInput.value = "";
      applyEntitySearch();
      if (searchInput) searchInput.focus();
    });
  }
  if (groups.length) {
    applyEntitySearch();
  }

  function replacePanel(id, html) {
    var current = document.getElementById(id);
    if (!current || !html) return;
    var wrap = document.createElement("div");
    wrap.innerHTML = String(html).trim();
    var next = wrap.firstElementChild;
    if (next) current.replaceWith(next);
  }

  function refreshLivePanels() {
    var entityId = window.ENTITY_ID;
    if (!entityId) return;
    var newsPanel = document.getElementById("ea-news-panel");
    var financePanel = document.getElementById("ea-finance-panel");
    if (newsPanel) newsPanel.classList.add("is-refreshing");
    if (financePanel) financePanel.classList.add("is-refreshing");
    var url = "/entity-assessment/live-panels?entity_id=" + encodeURIComponent(entityId);
    if (window.REPORT_DATE) url += "&report_date=" + encodeURIComponent(window.REPORT_DATE);
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (!resp.ok) throw new Error("live panels " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        if (Number(window.ENTITY_ID) !== Number(entityId)) return;
        replacePanel("ea-news-panel", data.news_html);
        replacePanel("ea-finance-panel", data.finance_html);
        var eventList = document.getElementById("risk-event-list");
        if (eventList && data.event_html && data.event_html.trim()) {
          eventList.innerHTML = data.event_html;
          eventList.hidden = false;
          var empty = eventList.previousElementSibling;
          if (empty && empty.classList.contains("ea-empty")) empty.hidden = true;
          if (window.applyEntityRiskTab) window.applyEntityRiskTab("all");
        }
      })
      .catch(function () {
        var news = document.getElementById("ea-news-panel");
        var finance = document.getElementById("ea-finance-panel");
        if (news) news.classList.remove("is-refreshing");
        if (finance) finance.classList.remove("is-refreshing");
      });
  }

  // 首屏内容由服务端从 SQLite 缓存渲染。不要在每次打开页面时再次
  // 请求外部新闻/财报；用户点击“刷新资讯”后由采集流水线更新缓存并刷新页面。
})();
