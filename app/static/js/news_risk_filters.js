/* 新闻汇总：按一篇新闻可拥有的多个风险类型标签筛选。 */
(function () {
  "use strict";
  var activeRiskTypes = new Set();
  var activeLevels = new Set();
  var riskButtons = Array.prototype.slice.call(document.querySelectorAll(".news-risk-filter"));
  var levelButtons = Array.prototype.slice.call(document.querySelectorAll(".news-level-filter"));
  if (!riskButtons.length && !levelButtons.length) return;
  function apply() {
    document.querySelectorAll("[data-risk-tags]").forEach(function (card) {
      var tags = String(card.getAttribute("data-risk-tags") || "").split("|").filter(Boolean);
      var riskMatches = !activeRiskTypes.size || tags.some(function (tag) { return activeRiskTypes.has(tag); });
      var levelMatches = !activeLevels.size || activeLevels.has(card.getAttribute("data-risk-level") || "");
      var moduleMatches = card.getAttribute("data-module-match") !== "false";
      card.hidden = !riskMatches || !levelMatches || !moduleMatches;
    });
  }
  window.applyNewsRiskFilter = apply;
  riskButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      var value = button.getAttribute("data-news-risk");
      if (activeRiskTypes.has(value)) activeRiskTypes.delete(value);
      else activeRiskTypes.add(value);
      button.classList.toggle("active", activeRiskTypes.has(value));
      apply();
    });
  });
  levelButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      var value = button.getAttribute("data-news-level");
      if (activeLevels.has(value)) activeLevels.delete(value);
      else activeLevels.add(value);
      button.classList.toggle("active", activeLevels.has(value));
      apply();
    });
  });
})();
