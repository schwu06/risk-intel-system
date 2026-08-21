/* 新闻汇总：按风险标签、等级、关键词、来源与发布时间组合筛选。 */
(function () {
  "use strict";
  var STATE_KEY = "riskintel:news-filter-state:v1:" + window.location.pathname;
  var activeRiskTypes = new Set();
  var activeLevels = new Set();
  var searchTerm = "";
  var riskButtons = Array.prototype.slice.call(document.querySelectorAll(".news-risk-filter"));
  var levelButtons = Array.prototype.slice.call(document.querySelectorAll(".news-level-filter"));
  var searchInput = document.getElementById("daily-module-search");
  var clearButton = document.getElementById("news-filter-clear");
  var filterCount = document.getElementById("news-filter-count");
  if (!riskButtons.length && !levelButtons.length && !searchInput) return;

  try {
    var stored = JSON.parse(window.localStorage.getItem(STATE_KEY) || "{}");
    (stored.riskTypes || []).forEach(function (value) { activeRiskTypes.add(String(value)); });
    (stored.levels || []).forEach(function (value) { activeLevels.add(String(value)); });
    searchTerm = String(stored.searchTerm || "").trim().toLowerCase();
  } catch (e) { /* 浏览器禁用本地存储时仍可正常筛选 */ }
  if (searchInput) searchInput.value = searchTerm;

  function save() {
    try {
      window.localStorage.setItem(STATE_KEY, JSON.stringify({
        riskTypes: Array.from(activeRiskTypes),
        levels: Array.from(activeLevels),
        searchTerm: searchTerm
      }));
    } catch (e) { /* ignore */ }
  }

  function syncButtons() {
    riskButtons.forEach(function (button) {
      var selected = activeRiskTypes.has(button.getAttribute("data-news-risk"));
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    levelButtons.forEach(function (button) {
      var selected = activeLevels.has(button.getAttribute("data-news-level"));
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    var count = activeRiskTypes.size + activeLevels.size + (searchTerm ? 1 : 0);
    if (filterCount) {
      filterCount.textContent = String(count);
      filterCount.hidden = count === 0;
    }
    if (clearButton) clearButton.disabled = count === 0;
  }

  function apply() {
    var visible = 0;
    document.querySelectorAll("[data-risk-tags]").forEach(function (card) {
      var tags = String(card.getAttribute("data-risk-tags") || "").split("|").filter(Boolean);
      var riskMatches = !activeRiskTypes.size || tags.some(function (tag) { return activeRiskTypes.has(tag); });
      var levelMatches = !activeLevels.size || activeLevels.has(card.getAttribute("data-risk-level") || "");
      var moduleMatches = card.getAttribute("data-module-match") !== "false";
      var searchText = String(card.getAttribute("data-news-search") || card.textContent || "").toLowerCase();
      var searchMatches = !searchTerm || searchText.indexOf(searchTerm) !== -1;
      card.hidden = !riskMatches || !levelMatches || !moduleMatches || !searchMatches;
      if (!card.hidden) visible += 1;
    });

    var hasFilter = Boolean(searchTerm) || activeRiskTypes.size > 0 || activeLevels.size > 0;
    document.querySelectorAll(".module-panel").forEach(function (panel) {
      var cards = panel.querySelectorAll("[data-risk-tags]");
      if (cards.length) {
        panel.hidden = hasFilter && !Array.prototype.some.call(cards, function (card) { return !card.hidden; });
      }
    });
    var empty = document.getElementById("news-search-empty");
    if (empty) empty.hidden = !hasFilter || visible > 0;
  }

  window.applyNewsRiskFilter = apply;
  syncButtons();
  apply();
  riskButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      var value = button.getAttribute("data-news-risk");
      if (activeRiskTypes.has(value)) activeRiskTypes.delete(value);
      else activeRiskTypes.add(value);
      syncButtons();
      save();
      apply();
    });
  });
  levelButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      var value = button.getAttribute("data-news-level");
      if (activeLevels.has(value)) activeLevels.delete(value);
      else activeLevels.add(value);
      syncButtons();
      save();
      apply();
    });
  });
  if (searchInput) {
    searchInput.addEventListener("input", function () {
      searchTerm = searchInput.value.trim().toLowerCase();
      syncButtons();
      save();
      apply();
    });
  }
  if (clearButton) {
    clearButton.addEventListener("click", function () {
      activeRiskTypes.clear();
      activeLevels.clear();
      searchTerm = "";
      if (searchInput) searchInput.value = "";
      syncButtons();
      save();
      apply();
    });
  }
})();
