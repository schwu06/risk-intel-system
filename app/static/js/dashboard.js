(function () {
  const COLORS = ["#6b7c93", "#8a939f", "#a8956e", "#7a9a82", "#9aa3ad"];

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
      window.addEventListener("resize", function () { chart.resize(); });
    });
  }

  /* ---------- 左侧数据源抽屉 / 关于 ---------- */
  const drawer = document.getElementById("source-drawer");
  const backdrop = document.getElementById("source-drawer-backdrop");
  const openBtn = document.getElementById("btn-open-source-drawer");
  const closeBtn = document.getElementById("btn-close-source-drawer");
  const aboutModal = document.getElementById("about-modal");
  const aboutOpen = document.getElementById("btn-open-about");
  const aboutClose = document.getElementById("btn-close-about");

  function setDrawerOpen(open) {
    if (!drawer) return;
    drawer.classList.toggle("open", open);
    drawer.setAttribute("aria-hidden", open ? "false" : "true");
    if (openBtn) openBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (backdrop) {
      backdrop.classList.toggle("hidden", !open);
      backdrop.hidden = !open;
    }
    document.body.classList.toggle("drawer-open", open);
  }

  function setAboutOpen(open) {
    if (!aboutModal) return;
    aboutModal.classList.toggle("hidden", !open);
    aboutModal.hidden = !open;
  }

  if (openBtn) openBtn.addEventListener("click", function () { setDrawerOpen(true); });
  if (closeBtn) closeBtn.addEventListener("click", function () { setDrawerOpen(false); });
  if (backdrop) backdrop.addEventListener("click", function () {
    setDrawerOpen(false);
    setAboutOpen(false);
    var svm = document.getElementById("source-view-modal");
    if (svm) { svm.classList.add("hidden"); svm.hidden = true; }
  });
  if (aboutOpen) aboutOpen.addEventListener("click", function () { setAboutOpen(true); });
  if (aboutClose) aboutClose.addEventListener("click", function () { setAboutOpen(false); });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      setAboutOpen(false);
      setDrawerOpen(false);
      var svm = document.getElementById("source-view-modal");
      if (svm) { svm.classList.add("hidden"); svm.hidden = true; }
    }
  });

  document.querySelectorAll(".upload-form").forEach(function (form) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const moduleCode = form.getAttribute("data-module");
      const fd = new FormData(form);
      if (moduleCode) fd.append("module_code", moduleCode);
      try {
        const resp = await fetch("/api/v1/data-sources/upload", { method: "POST", body: fd });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "上传失败");
        window.location.reload();
      } catch (e) {
        alert("上传失败: " + e.message);
      }
    });
  });

  const globalUpload = document.getElementById("global-upload-form");
  if (globalUpload) {
    globalUpload.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const fd = new FormData(globalUpload);
      try {
        const resp = await fetch("/api/v1/data-sources/upload", { method: "POST", body: fd });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "上传失败");
        window.location.reload();
      } catch (e) {
        alert("上传失败: " + e.message);
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
      try {
        const resp = await fetch("/api/v1/data-sources/url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "添加失败");
        window.location.reload();
      } catch (e) {
        alert("添加失败: " + e.message);
      }
    });
  });

  const globalUrl = document.getElementById("global-url-form");
  if (globalUrl) {
    globalUrl.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const name = globalUrl.querySelector('[name="name"]').value;
      const url = globalUrl.querySelector('[name="url"]').value;
      try {
        const resp = await fetch("/api/v1/data-sources/url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name, url: url, priority: 0 }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "添加失败");
        window.location.reload();
      } catch (e) {
        alert("添加失败: " + e.message);
      }
    });
  }

  document.querySelectorAll(".delete-source").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      const id = btn.getAttribute("data-id");
      if (!confirm("确认删除该数据源？")) return;
      const resp = await fetch("/api/v1/data-sources/" + id, { method: "DELETE" });
      if (resp.ok) window.location.reload();
      else alert("删除失败");
    });
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

  document.querySelectorAll(".view-source").forEach(function (btn) {
    btn.addEventListener("click", async function () {
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
        const text = (data.extracted_text || data.text_preview || "（无提取正文）")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
        if (sourceViewBody) {
          sourceViewBody.innerHTML =
            '<p class="meta">' + meta + "</p>" +
            '<pre class="source-text-preview">' + text + "</pre>";
        }
      } catch (e) {
        if (sourceViewBody) sourceViewBody.innerHTML = '<p class="empty-module">加载失败：' + e.message + "</p>";
      }
    });
  });

  /* remove old industry-only handlers from drawer — kept no-ops if elements missing */
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
      if (resp.ok) window.location.reload();
      else alert("删除失败");
    });
  });

  /* ---------- 流水线（异步 + 轮询） ---------- */
  const pipelineBtn = document.getElementById("btn-run-pipeline");

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  async function pollJob(jobId, msgEl, maxRounds) {
    const rounds = maxRounds || 180;
    for (let i = 0; i < rounds; i++) {
      const resp = await fetch("/api/v1/pipeline/jobs/" + jobId);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "无法查询任务状态");
      const status = data.status || "";
      if (msgEl) {
        msgEl.textContent = "任务 " + status + "：" + (data.message || "");
      }
      if (status === "completed" || status === "failed") {
        return data;
      }
      await sleep(2000);
    }
    throw new Error("等待超时，请稍后刷新页面查看结果");
  }

  async function runPipeline(moduleCodes, msgEl) {
    const target = msgEl || document.getElementById("pipeline-msg");
    if (target) target.textContent = "正在启动采集任务…";
    const payload = { report_date: window.REPORT_DATE || null, async_mode: true };
    if (moduleCodes && moduleCodes.length) {
      payload.module_codes = moduleCodes;
    } else if (window.PIPELINE_MODULES && window.PIPELINE_MODULES.length) {
      payload.module_codes = window.PIPELINE_MODULES;
    }
    const resp = await fetch("/api/v1/pipeline/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resp.status === 409) {
      const detail = data.detail || {};
      const existingId = detail.job_id;
      if (existingId) {
        if (target) target.textContent = "已有任务在运行，正在等待…";
        const done = await pollJob(existingId, target);
        if (target) target.textContent = "完成：" + JSON.stringify(done.results || {});
        setTimeout(function () { window.location.reload(); }, 1200);
        return done;
      }
      throw new Error(detail.message || data.detail || "任务冲突");
    }
    if (!resp.ok) {
      const detail = typeof data.detail === "string" ? data.detail : (data.detail && data.detail.message) || "执行失败";
      throw new Error(detail);
    }
    if (data.async_mode && data.job_id) {
      if (target) target.textContent = "任务已启动，采集中…";
      const done = await pollJob(data.job_id, target);
      if (target) {
        target.textContent = (done.message || "完成") + " " + JSON.stringify(done.results || {});
      }
      setTimeout(function () { window.location.reload(); }, 1200);
      return done;
    }
    if (target) target.textContent = "完成：" + JSON.stringify(data.results);
    setTimeout(function () { window.location.reload(); }, 1200);
    return data;
  }

  if (pipelineBtn) {
    pipelineBtn.addEventListener("click", async function () {
      const msg = document.getElementById("pipeline-msg");
      pipelineBtn.disabled = true;
      try {
        await runPipeline(null, msg);
      } catch (e) {
        if (msg) msg.textContent = "错误：" + e.message;
        pipelineBtn.disabled = false;
      }
    });
  }

  document.querySelectorAll(".btn-reload-module").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      const code = btn.getAttribute("data-module");
      const panel = document.getElementById("module-panel-" + code);
      const statusBox = panel ? panel.querySelector(".module-status") : null;
      btn.disabled = true;
      if (statusBox) {
        const p = statusBox.querySelector(".empty-module");
        if (p) p.textContent = "正在重新加载…";
      }
      try {
        await runPipeline([code], document.getElementById("pipeline-msg"));
      } catch (e) {
        btn.disabled = false;
        if (statusBox) {
          const p = statusBox.querySelector(".empty-module");
          if (p) p.textContent = "请求失败，请重新加载";
        }
        alert("重新加载失败: " + e.message);
      }
    });
  });

  renderEntryCharts();
})();
