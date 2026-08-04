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
    exportBtn.textContent = on ? "正在生成…" : "导出 Word";
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
    showExportMsg("正在生成《企业主体风险评估简报》…");
    try {
      var resp = await fetch(url);
      if (!resp.ok) {
        var errBody = await resp.json().catch(function () { return {}; });
        var detail = errBody.detail || ("导出失败 (" + resp.status + ")");
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      var blob = await resp.blob();
      var disp = resp.headers.get("Content-Disposition") || "";
      var filename = "企业主体风险评估简报.docx";
      var m = /filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?/i.exec(disp);
      if (m) {
        filename = decodeURIComponent(m[1] || m[2]);
      } else if (window.ENTITY_NAME) {
        filename = "企业主体风险评估简报_" + window.ENTITY_NAME + ".docx";
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
})();
