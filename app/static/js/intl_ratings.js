/**
 * 国际评级页面：从 /api/v1/intl-ratings 拉取快照；手动更新触发后台流水线。
 */
(function () {
  "use strict";

  var CATEGORY_SIMPLE = "简易分类债券";
  var CATEGORY_NON_SIMPLE = "非简易分类债券";
  var API = "/api/v1/intl-ratings";

  var rows = [];
  var selectedId = null;
  var groupCollapsed = {};
  groupCollapsed[CATEGORY_SIMPLE] = false;
  groupCollapsed[CATEGORY_NON_SIMPLE] = false;
  var pollTimer = null;

  var els = {
    search: document.getElementById("ir-search-input"),
    clear: document.getElementById("ir-search-clear"),
    tree: document.getElementById("ir-tree"),
    empty: document.getElementById("ir-search-empty"),
    tbody: document.getElementById("ir-table-body"),
    wrap: document.getElementById("ir-table-wrap"),
    msg: document.getElementById("ir-action-msg"),
    btnRefresh: document.getElementById("btn-ir-refresh"),
    btnExport: document.getElementById("btn-ir-export"),
  };

  function setMsg(text, isError) {
    if (!els.msg) return;
    els.msg.textContent = text || "";
    els.msg.classList.toggle("is-error", !!isError);
  }

  function setLoading(on) {
    if (!els.btnRefresh) return;
    els.btnRefresh.classList.toggle("is-loading", !!on);
    els.btnRefresh.disabled = !!on;
  }

  function getQuery() {
    return (els.search && els.search.value ? els.search.value : "").trim().toLowerCase();
  }

  function filteredRows() {
    var q = getQuery();
    if (!q) return rows.slice();
    return rows.filter(function (r) {
      return (r.issuer || "").toLowerCase().indexOf(q) !== -1;
    });
  }

  function groupRows(list) {
    var groups = [
      { name: CATEGORY_SIMPLE, items: [] },
      { name: CATEGORY_NON_SIMPLE, items: [] },
    ];
    list.forEach(function (r) {
      if (r.category === CATEGORY_SIMPLE) groups[0].items.push(r);
      else groups[1].items.push(r);
    });
    return groups;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function cellHtml(text, extraClass) {
    var t = text == null || text === "" ? "—" : String(text);
    var cls = "ir-cell" + (extraClass ? " " + extraClass : "");
    return (
      '<td class="' +
      cls +
      '" title="' +
      escapeHtml(t) +
      '"><span class="ir-cell-text">' +
      escapeHtml(t) +
      "</span></td>"
    );
  }

  function renderTable(list) {
    if (!els.tbody) return;
    if (!list.length) {
      els.tbody.innerHTML =
        '<tr class="ir-empty-row"><td colspan="11">暂无匹配数据</td></tr>';
      return;
    }
    els.tbody.innerHTML = list
      .map(function (r) {
        var active = r.id === selectedId ? " is-active" : "";
        return (
          '<tr class="ir-row' +
          active +
          '" data-issuer-id="' +
          escapeHtml(r.id) +
          '" id="row-' +
          escapeHtml(r.id) +
          '">' +
          cellHtml(r.issuer, "col-issuer") +
          cellHtml(r.moodys, "col-rating") +
          cellHtml(r.sp, "col-rating") +
          cellHtml(r.fitch, "col-rating") +
          cellHtml(r.loss, "col-yn") +
          cellHtml(r.listed, "col-yn") +
          cellHtml(r.delisted, "col-yn") +
          cellHtml(r.priceDrop, "col-price") +
          cellHtml(r.noRatingReason, "col-reason") +
          cellHtml(r.ratingChanged, "col-change") +
          '<td class="ir-cell col-rss">' +
          '<button type="button" class="btn small ir-rss-btn" data-issuer-id="' +
          escapeHtml(r.id) +
          '">打开 RSS</button>' +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function renderTree(list) {
    if (!els.tree) return;
    var groups = groupRows(list);
    var total = list.length;
    if (els.empty) {
      els.empty.hidden = total !== 0;
    }
    if (!total) {
      els.tree.innerHTML = "";
      return;
    }

    els.tree.innerHTML = groups
      .map(function (g) {
        if (!g.items.length && getQuery()) return "";
        var collapsed = !!groupCollapsed[g.name];
        var count = g.items.length;
        var itemsHtml = g.items
          .map(function (r) {
            var active = r.id === selectedId ? " active" : "";
            return (
              '<li role="none">' +
              '<button type="button" class="ir-tree-item' +
              active +
              '" role="treeitem" data-issuer-id="' +
              escapeHtml(r.id) +
              '" title="' +
              escapeHtml(r.issuer) +
              '">' +
              escapeHtml(r.issuer) +
              "</button></li>"
            );
          })
          .join("");
        return (
          '<div class="ir-tree-group' +
          (collapsed ? " is-collapsed" : "") +
          '" data-group="' +
          escapeHtml(g.name) +
          '">' +
          '<button type="button" class="ir-tree-group-title" data-toggle-group="' +
          escapeHtml(g.name) +
          '">' +
          '<span class="ir-tree-caret" aria-hidden="true"></span>' +
          escapeHtml(g.name) +
          ' <span class="ir-tree-count">' +
          count +
          "</span></button>" +
          '<ul class="ir-tree-list" role="group">' +
          itemsHtml +
          "</ul></div>"
        );
      })
      .join("");
  }

  function refresh() {
    var list = filteredRows();
    renderTree(list);
    renderTable(list);
    if (els.clear) {
      els.clear.hidden = !getQuery();
    }
  }

  function selectIssuer(issuerId, scroll) {
    selectedId = issuerId;
    document.querySelectorAll(".ir-tree-item").forEach(function (el) {
      el.classList.toggle("active", el.getAttribute("data-issuer-id") === issuerId);
    });
    document.querySelectorAll(".ir-row").forEach(function (el) {
      el.classList.toggle("is-active", el.getAttribute("data-issuer-id") === issuerId);
    });
    if (scroll) {
      var row = document.getElementById("row-" + issuerId);
      if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function handleOpenRssFeed(issuerId) {
    var row = rows.find(function (r) {
      return r.id === issuerId;
    });
    var name = row ? row.issuer : issuerId;
    var url = row && row.rssUrl ? row.rssUrl : "";
    if (url) {
      window.open(url, "_blank", "noopener,noreferrer");
      return;
    }
    setMsg("「" + name + "」暂无官方 RSS 链接，请后续在映射表补充。", true);
  }

  function exportExcel() {
    if (typeof XLSX === "undefined") {
      setMsg("导出组件未加载", true);
      return;
    }
    var list = filteredRows();
    if (!list.length) {
      setMsg("无数据可导出", true);
      return;
    }
    var header = [
      "发行体",
      "分类",
      "穆迪评级",
      "标普评级",
      "惠誉评级",
      "债务人最近一期決算是否亏损(是/否)",
      "是否上市（是/否）",
      "若上市，债务人是否被上市废止(是/否)",
      "债券价格是否大幅下跌（月环比跌幅超过5%）等",
      "皆无评级的話请写明理由",
      "评级是否变化",
    ];
    var data = [header].concat(
      list.map(function (r) {
        return [
          r.issuer,
          r.category,
          r.moodys,
          r.sp,
          r.fitch,
          r.loss,
          r.listed,
          r.delisted,
          r.priceDrop,
          r.noRatingReason,
          r.ratingChanged,
        ];
      })
    );
    var ws = XLSX.utils.aoa_to_sheet(data);
    var wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "国际评级");
    var stamp = new Date();
    var pad = function (n) {
      return n < 10 ? "0" + n : String(n);
    };
    var filename =
      "国际评级_" +
      stamp.getFullYear() +
      pad(stamp.getMonth() + 1) +
      pad(stamp.getDate()) +
      "_" +
      pad(stamp.getHours()) +
      pad(stamp.getMinutes()) +
      ".xlsx";
    XLSX.writeFile(wb, filename);
    setMsg("已导出 " + list.length + " 条记录：" + filename);
  }

  function applySnapshot(data) {
    rows = (data.rows || []).slice();
    refresh();
    var tip = data.message || "";
    if (data.updated_at) {
      tip = (tip ? tip + " " : "") + "更新于 " + data.updated_at + " · 共 " + rows.length + " 家";
    } else if (!tip) {
      tip = "共 " + rows.length + " 家发行体";
    }
    setMsg(tip, data.source === "skeleton");
  }

  function loadSnapshot() {
    return fetch(API)
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(applySnapshot)
      .catch(function (err) {
        setMsg("加载评级数据失败：" + (err.message || err), true);
      });
  }

  function stopPoll() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function pollJob(jobId) {
    stopPoll();
    pollTimer = window.setInterval(function () {
      fetch(API + "/jobs/" + encodeURIComponent(jobId))
        .then(function (res) {
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.json();
        })
        .then(function (job) {
          setMsg(job.message || job.status);
          if (job.status === "succeeded" || job.status === "failed") {
            stopPoll();
            setLoading(false);
            if (job.status === "failed") {
              setMsg("更新失败：" + (job.error || job.message), true);
            }
            return loadSnapshot().then(function () {
              if (job.status === "succeeded") {
                setMsg(job.message || "评级数据已更新");
              }
            });
          }
        })
        .catch(function (err) {
          stopPoll();
          setLoading(false);
          setMsg("查询任务失败：" + (err.message || err), true);
        });
    }, 2000);
  }

  function manualRefresh() {
    setLoading(true);
    setMsg("正在启动评级流水线（快速模式）…");
    // quick=true：网页刷新跳过 Playwright，避免长时间阻塞
    fetch(API + "/refresh?quick=true", { method: "POST" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        setMsg(data.message || "任务已启动");
        if (data.job_id) {
          pollJob(data.job_id);
        } else {
          setLoading(false);
        }
      })
      .catch(function (err) {
        setLoading(false);
        setMsg("启动刷新失败：" + (err.message || err), true);
      });
  }

  function bindEvents() {
    if (els.search) {
      els.search.addEventListener("input", function () {
        refresh();
      });
    }
    if (els.clear) {
      els.clear.addEventListener("click", function () {
        if (els.search) els.search.value = "";
        refresh();
        if (els.search) els.search.focus();
      });
    }
    if (els.tree) {
      els.tree.addEventListener("click", function (ev) {
        var toggle = ev.target.closest("[data-toggle-group]");
        if (toggle) {
          var g = toggle.getAttribute("data-toggle-group");
          groupCollapsed[g] = !groupCollapsed[g];
          renderTree(filteredRows());
          return;
        }
        var item = ev.target.closest(".ir-tree-item");
        if (item) {
          selectIssuer(item.getAttribute("data-issuer-id"), true);
        }
      });
    }
    if (els.tbody) {
      els.tbody.addEventListener("click", function (ev) {
        var rssBtn = ev.target.closest(".ir-rss-btn");
        if (rssBtn) {
          ev.stopPropagation();
          handleOpenRssFeed(rssBtn.getAttribute("data-issuer-id"));
          return;
        }
        var row = ev.target.closest(".ir-row");
        if (row) {
          selectIssuer(row.getAttribute("data-issuer-id"), false);
        }
      });
    }
    if (els.btnRefresh) {
      els.btnRefresh.addEventListener("click", manualRefresh);
    }
    if (els.btnExport) {
      els.btnExport.addEventListener("click", exportExcel);
    }
  }

  bindEvents();
  setMsg("正在加载评级数据…");
  loadSnapshot();
})();
