(function () {
  const form = document.getElementById("analysis-form");
  const msg = document.getElementById("analysis-msg");

  if (form) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      msg.textContent = "分析生成中，可能需要数分钟…";
      const fd = new FormData(form);
      const body = {
        industry_name: fd.get("industry_name"),
        company_name: fd.get("company_name") || null,
        supplement_search: fd.get("supplement_search") === "on",
      };
      try {
        const resp = await fetch("/api/v1/industry/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "生成失败");
        window.location.href = "/deep-reports?report_id=" + data.id;
      } catch (e) {
        msg.textContent = "错误：" + e.message;
      }
    });

    const industryInput = form.querySelector('[name="industry_name"]');
    if (industryInput) {
      industryInput.addEventListener("change", function () {
        const v = industryInput.value;
        const up = document.getElementById("drawer-upload-industry-name");
        const url = document.getElementById("drawer-url-industry-name");
        if (up) up.value = v;
        if (url) url.value = v;
      });
    }
  }

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
