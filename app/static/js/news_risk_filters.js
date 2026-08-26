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

  function reorderGroup(buttons, isActive) {
    if (!buttons.length) return;
    var parent = buttons[0].parentNode;
    var selected = [];
    var rest = [];
    buttons.forEach(function (button) {
      if (isActive(button)) selected.push(button);
      else rest.push(button);
    });
    selected.concat(rest).forEach(function (button) {
      parent.appendChild(button);
    });
  }

  function syncButtons() {
    riskButtons.forEach(function (button) {
      button.classList.toggle("active", activeRiskTypes.has(button.getAttribute("data-news-risk")));
    });
    levelButtons.forEach(function (button) {
      button.classList.toggle("active", activeLevels.has(button.getAttribute("data-news-level")));
    });
    reorderGroup(riskButtons, function (button) {
      return activeRiskTypes.has(button.getAttribute("data-news-risk"));
    });
    reorderGroup(levelButtons, function (button) {
      return activeLevels.has(button.getAttribute("data-news-level"));
    });
  }

  function cardMatchesFilters(card) {
    var tags = String(card.getAttribute("data-risk-tags") || "").split("|").filter(Boolean);
    var riskMatches = !activeRiskTypes.size || tags.some(function (tag) { return activeRiskTypes.has(tag); });
    var levelMatches = !activeLevels.size || activeLevels.has(card.getAttribute("data-risk-level") || "");
    var searchText = String(card.getAttribute("data-news-search") || card.textContent || "").toLowerCase();
    var searchMatches = !searchTerm || searchText.indexOf(searchTerm) !== -1;
    return riskMatches && levelMatches && searchMatches;
  }

  function updateIndexCounts() {
    var counts = {};
    document.querySelectorAll("[data-risk-tags]").forEach(function (card) {
      if (!cardMatchesFilters(card)) return;
      var code = card.getAttribute("data-module");
      if (!code) return;
      counts[code] = (counts[code] || 0) + 1;
    });
    document.querySelectorAll("[data-module-count]").forEach(function (badge) {
      var n = counts[badge.getAttribute("data-module-count")] || 0;
      badge.textContent = String(n);
      badge.setAttribute("aria-label", n + "条新闻");
    });
  }

  function updateFilterBadge() {
    var count = activeRiskTypes.size + activeLevels.size + (searchTerm ? 1 : 0);
    var badge = document.getElementById("news-filter-count");
    if (badge) {
      badge.textContent = String(count);
      badge.hidden = count === 0;
    }
    var clearBtn = document.getElementById("news-filter-clear");
    if (clearBtn) clearBtn.disabled = count === 0;
  }

  function apply() {
    var visible = 0;
    document.querySelectorAll("[data-risk-tags]").forEach(function (card) {
      var moduleMatches = card.getAttribute("data-module-match") !== "false";
      card.hidden = !cardMatchesFilters(card) || !moduleMatches;
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
    updateIndexCounts();
    updateFilterBadge();
  }

  function clearAll() {
    activeRiskTypes.clear();
    activeLevels.clear();
    searchTerm = "";
    if (searchInput) searchInput.value = "";
    syncButtons();
    save();
    apply();
  }

  window.applyNewsRiskFilter = apply;
  syncButtons();
  apply();

  var clearBtn = document.getElementById("news-filter-clear");
  if (clearBtn) clearBtn.addEventListener("click", clearAll);

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
      save();
      apply();
    });
  }
})();
