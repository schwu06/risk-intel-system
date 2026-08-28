/*
 * RiskIntel motion controller
 * 负责：卡片进入视区时的柔和呈现、主要控件的微交互反馈。
 * 样式在 static/css/ui/motion.css；本文件只加 class，不写视觉规则。
 * 不依赖第三方库，且尊重 prefers-reduced-motion。
 * Modified by DingJiaye: 2026-08-26.
 */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion) return;

  var selectors = [
    ".daily-summary-report",
    ".module-panel",
    ".risk-event-card",
    ".timeline-item",
    ".entity-latest-panel",
    ".entity-risk-card",
    ".entity-financial-card",
    ".industry-document-workspace",
    ".report-preview",
    ".ir-overview",
    ".ir-filter-bar",
    ".ir-table-wrap"
  ];

  function uniqueTargets() {
    var seen = new Set();
    var targets = [];
    document.querySelectorAll(selectors.join(",")).forEach(function (node) {
      if (!seen.has(node) && !node.hidden) {
        seen.add(node);
        targets.push(node);
      }
    });
    return targets;
  }

  function revealContent() {
    var targets = uniqueTargets();
    if (!targets.length) return;
    targets.forEach(function (node, index) {
      node.classList.add("motion-reveal");
      node.style.setProperty("--motion-delay", Math.min(index, 7) * 38 + "ms");
    });

    if (!("IntersectionObserver" in window)) {
      requestAnimationFrame(function () {
        targets.forEach(function (node) { node.classList.add("is-visible"); });
      });
      return;
    }

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.06, rootMargin: "0px 0px -24px 0px" });
    targets.forEach(function (node) { observer.observe(node); });
  }

  function addPressFeedback() {
    document.querySelectorAll("button, .btn, .sidebar-action-primary, .sidebar-action-secondary").forEach(function (node) {
      if (node.dataset.motionBound === "1") return;
      node.dataset.motionBound = "1";
      node.classList.add("motion-press");
      node.addEventListener("pointerdown", function () {
        if (!node.disabled) node.classList.add("is-pressed");
      });
      ["pointerup", "pointercancel", "pointerleave"].forEach(function (eventName) {
        node.addEventListener(eventName, function () { node.classList.remove("is-pressed"); });
      });
    });
  }

  function initialise() {
    addPressFeedback();
    revealContent();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialise, { once: true });
  else initialise();
}());
