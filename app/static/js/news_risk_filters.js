/* 新闻汇总：按一篇新闻可拥有的多个风险类型标签筛选。 */
(function () {
  "use strict";
  var active = "all";
  var buttons = Array.prototype.slice.call(document.querySelectorAll(".news-risk-filter"));
  if (!buttons.length) return;
  function apply() {
    document.querySelectorAll("[data-risk-tags]").forEach(function (card) {
      var tags = String(card.getAttribute("data-risk-tags") || "").split("|").filter(Boolean);
      var riskMatches = active === "all" || tags.indexOf(active) !== -1;
      var moduleMatches = card.getAttribute("data-module-match") !== "false";
      card.hidden = !riskMatches || !moduleMatches;
    });
  }
  window.applyNewsRiskFilter = apply;
  buttons.forEach(function (button) {
    button.addEventListener("click", function () {
      active = button.getAttribute("data-news-risk") || "all";
      buttons.forEach(function (item) { item.classList.toggle("active", item === button); });
      apply();
    });
  });
})();
