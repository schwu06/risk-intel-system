(function () {
  function apiError(detail, fallback) {
    if (!detail) return fallback;
    if (typeof detail === "string") return detail;
    return [detail.code, detail.message, detail.next_step ? "下一步：" + detail.next_step : ""]
      .filter(Boolean).join(" · ");
  }
  const form = document.getElementById("analysis-form");
  const msg = document.getElementById("analysis-msg");

  if (form) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      msg.textContent = "正在创建报告草稿…";
      const fd = new FormData(form);
      const body = {
        industry_name: fd.get("industry_name"),
        company_name: fd.get("company_name") || null,
        supplement_search: fd.get("supplement_search") === "on",
      };
      try {
        const resp = await fetch("/api/v1/industry/reports/drafts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "创建草稿失败");
        window.location.href = "/deep-reports?report_id=" + data.id;
      } catch (e) {
        msg.textContent = "错误：" + e.message;
      }
    });

  }

  const generateBtn = document.getElementById("btn-generate-report");
  if (generateBtn) {
    generateBtn.addEventListener("click", async function () {
      const reportId = generateBtn.getAttribute("data-report-id");
      const generationMsg = document.getElementById("generation-msg");
      generateBtn.disabled = true;
      if (generationMsg) generationMsg.textContent = "报告生成中，可能需要数分钟，请勿重复提交…";
      try {
        const resp = await fetch("/api/v1/industry/reports/" + reportId + "/generate", { method: "POST" });
        const data = await resp.json();
        if (!resp.ok) throw new Error(apiError(data.detail, "生成失败"));
        window.location.href = "/deep-reports?report_id=" + data.id;
      } catch (e) {
        generateBtn.disabled = false;
        if (generationMsg) generationMsg.textContent = "错误：" + e.message;
      }
    });
  }

  const forkBtn = document.getElementById("btn-fork-report");
  if (forkBtn) {
    forkBtn.addEventListener("click", async function () {
      const reportId = forkBtn.getAttribute("data-report-id");
      forkBtn.disabled = true;
      try {
        const resp = await fetch("/api/v1/industry/reports/" + reportId + "/fork", { method: "POST" });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "创建新版失败");
        window.location.href = "/deep-reports?report_id=" + data.id;
      } catch (e) {
        forkBtn.disabled = false;
        alert("错误：" + e.message);
      }
    });
  }

  const promoteBtn = document.getElementById("btn-promote-grounded");
  if (promoteBtn) {
    promoteBtn.addEventListener("click", async function () {
      const noteInput = document.getElementById("promotion-note");
      const promotionMsg = document.getElementById("promotion-msg");
      const note = noteInput ? noteInput.value.trim() : "";
      if (!note) {
        if (promotionMsg) promotionMsg.textContent = "请先填写审批备注。";
        if (noteInput) noteInput.focus();
        return;
      }
      promoteBtn.disabled = true;
      if (promotionMsg) promotionMsg.textContent = "正在复核证据快照并晋升候选…";
      try {
        const reportId = promoteBtn.getAttribute("data-report-id");
        const runId = promoteBtn.getAttribute("data-run-id");
        const resp = await fetch(
          "/api/v1/industry/reports/" + reportId + "/grounded-runs/" + runId + "/promote",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ promotion_note: note }),
          }
        );
        const data = await resp.json();
        if (!resp.ok) throw new Error(apiError(data.detail, "晋升失败"));
        window.location.href = "/deep-reports?report_id=" + data.id;
      } catch (e) {
        promoteBtn.disabled = false;
        if (promotionMsg) promotionMsg.textContent = "错误：" + e.message;
      }
    });
  }

  function addDetailRow(host, label, value) {
    if (value === null || value === undefined || value === "") return;
    const row = document.createElement("div");
    row.className = "citation-detail-row";
    const term = document.createElement("strong");
    term.textContent = label;
    const content = document.createElement("span");
    content.textContent = String(value);
    row.append(term, content);
    host.appendChild(row);
  }

  function addList(host, title, items, formatter) {
    if (!Array.isArray(items) || !items.length) return;
    const heading = document.createElement("h3");
    heading.textContent = title;
    const list = document.createElement("ul");
    items.forEach(function (item) {
      const li = document.createElement("li");
      li.textContent = formatter ? formatter(item) : String(item);
      list.appendChild(li);
    });
    host.append(heading, list);
  }

  const citationDialog = document.getElementById("citation-dialog");
  const citationDetail = document.getElementById("citation-detail");
  const reportHost = document.querySelector(".industry-content[data-report-id]");
  if (citationDialog && citationDetail && reportHost) {
    const closeButton = citationDialog.querySelector(".citation-close");
    if (closeButton) closeButton.addEventListener("click", function () { citationDialog.close(); });
    citationDialog.addEventListener("click", function (event) {
      if (event.target === citationDialog) citationDialog.close();
    });
    document.addEventListener("click", async function (event) {
      const button = event.target.closest(".citation-ref");
      if (!button) return;
      const reportId = reportHost.getAttribute("data-report-id");
      const status = reportHost.getAttribute("data-report-status");
      const runId = reportHost.getAttribute("data-run-id");
      const code = button.getAttribute("data-evidence-code");
      let url = "/api/v1/industry/reports/" + reportId + "/citations/" + encodeURIComponent(code);
      if (status === "awaiting_approval" && runId) {
        url = "/api/v1/industry/reports/" + reportId + "/grounded-runs/" + runId
          + "/citations/" + encodeURIComponent(code);
      }
      citationDetail.replaceChildren();
      const loading = document.createElement("p");
      loading.textContent = "正在读取证据详情…";
      citationDetail.appendChild(loading);
      citationDialog.showModal();
      try {
        const resp = await fetch(url);
        const data = await resp.json();
        if (!resp.ok) throw new Error(apiError(data.detail, "证据详情不可用"));
        citationDetail.replaceChildren();
        addDetailRow(citationDetail, "引用编号", "[" + data.display_number + "]");
        addDetailRow(citationDetail, "来源", data.source_name);
        addDetailRow(citationDetail, "发布机构", data.source_publisher);
        addDetailRow(citationDetail, "发布日期", data.published_at);
        addDetailRow(citationDetail, "获取时间", data.retrieved_at);
        addDetailRow(citationDetail, "来源类型", data.source_origin);
        addDetailRow(citationDetail, "证据等级", data.evidence_grade);
        addDetailRow(citationDetail, "原文位置", data.locator);
        addDetailRow(citationDetail, "原文摘录", data.original_quote);
        addDetailRow(citationDetail, "标准化事实", data.normalized_claim);
        if (data.url) {
          const linkRow = document.createElement("div");
          linkRow.className = "citation-detail-row";
          const label = document.createElement("strong");
          label.textContent = "网页地址";
          const link = document.createElement("a");
          link.href = data.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = data.url;
          linkRow.append(label, link);
          citationDetail.appendChild(linkRow);
        }
        addList(citationDetail, "资料限制", data.limitations);
        addList(citationDetail, "相关冲突", data.related_conflicts, function (item) {
          return [item.conflict_code, item.status, item.description].filter(Boolean).join(" · ");
        });
      } catch (e) {
        citationDetail.replaceChildren();
        const error = document.createElement("p");
        error.className = "citation-warning";
        error.textContent = "错误：" + e.message;
        citationDetail.appendChild(error);
      }
    });
  }

  document.querySelectorAll(".report-rename-btn").forEach(function (button) {
    button.addEventListener("click", async function () {
      const reportId = button.getAttribute("data-report-id");
      const currentName = button.getAttribute("data-report-name") || button.getAttribute("data-default-name") || "";
      const reportName = window.prompt("请输入新的报告名称（最多 256 个字符）：", currentName);
      if (reportName === null) return;
      const normalized = reportName.trim().replace(/\s+/g, " ");
      if (!normalized) {
        window.alert("报告名称不能为空");
        return;
      }
      if (normalized.length > 256) {
        window.alert("报告名称不能超过 256 个字符");
        return;
      }
      button.disabled = true;
      try {
        const resp = await fetch("/api/v1/industry/reports/" + reportId + "/name", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ report_name: normalized }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "修改名称失败");
        window.location.reload();
      } catch (e) {
        button.disabled = false;
        window.alert("错误：" + e.message);
      }
    });
  });

  document.querySelectorAll(".report-delete-btn").forEach(function (button) {
    button.addEventListener("click", async function () {
      const reportId = button.getAttribute("data-report-id");
      if (!window.confirm("确认删除这份历史报告及其数据源、证据记录吗？此操作不可撤销。")) return;
      button.disabled = true;
      try {
        const resp = await fetch("/api/v1/industry/reports/" + reportId, { method: "DELETE" });
        const data = await resp.json().catch(function () { return {}; });
        if (!resp.ok) throw new Error(data.detail || "删除报告失败");
        window.location.href = "/deep-reports";
      } catch (e) {
        button.disabled = false;
        window.alert(e.message);
      }
    });
  });

  if (window.echarts && window.INDUSTRY_CHARTS) {
    const grid = document.getElementById("industry-charts");
    if (grid) {
      window.INDUSTRY_CHARTS.forEach(function (item, idx) {
        const host = document.createElement("div");
        host.style.height = "260px";
        host.style.marginTop = "1rem";
        grid.appendChild(host);
        const chart = echarts.init(host);
        const labels = item.labels || [];
        const series = (item.series || []).map(function (s) {
          return { name: s.name, type: item.type || "bar", data: s.data || [] };
        });
        chart.setOption({
          title: { text: item.title || ("图 " + (idx + 1)), left: "center", textStyle: { fontSize: 13 } },
          tooltip: { trigger: "axis" },
          xAxis: { type: "category", data: labels },
          yAxis: { type: "value" },
          series: series,
        });
      });
    }
  }
})();
