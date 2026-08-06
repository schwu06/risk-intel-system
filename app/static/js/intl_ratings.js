/**
 * 国际评级页面：侧栏索引、表格联动、手动更新提示、Excel 导出。
 */
(function () {
  "use strict";

  var CATEGORY_SIMPLE = "简易分类债券";
  var CATEGORY_NON_SIMPLE = "非简易分类债券";

  /** 附件发行体列表（已去重，保留分类绑定） */
  var RAW_ISSUERS = [
    // 简易分类债券
    { name: "ABU DHABI COMMERCIAL BANK, ABU DHABI", category: CATEGORY_SIMPLE },
    { name: "AGRICULTURAL DEVELOPMENT BANK OF CHINA, THE, BEIJING", category: CATEGORY_SIMPLE },
    { name: "BARCLAYS BANK PLC (ALL U.K. OFFICES)", category: CATEGORY_SIMPLE },
    { name: "CCBL(Cayman)1 Corporation Limited", category: CATEGORY_SIMPLE },
    { name: "CDBL FUNDING 1", category: CATEGORY_SIMPLE },
    { name: "CHINA CINDA FINANCE (2017) I LIMITED", category: CATEGORY_SIMPLE },
    { name: "CSI_MTN_LIMITED", category: CATEGORY_SIMPLE },
    { name: "DBS Bank Ltd, Australia Branch", category: CATEGORY_SIMPLE },
    { name: "EMIRATES NBD BANK PJSC", category: CATEGORY_SIMPLE },
    { name: "EXPORT-IMPORT BANK OF CHINA, THE, BEIJING", category: CATEGORY_SIMPLE },
    { name: "EXPORT-IMPORT BANK OF KOREA, THE, SEOUL", category: CATEGORY_SIMPLE },
    { name: "FIRST ABU DHABI BANK PJSC H.O.", category: CATEGORY_SIMPLE },
    { name: "ICBCIL FINANCE CO. LIMITED", category: CATEGORY_SIMPLE },
    { name: "INDUSTRIAL BANK OF KOREA", category: CATEGORY_SIMPLE },
    { name: "KEB HANA BANK", category: CATEGORY_SIMPLE },
    { name: "KOREA DEVELOPMENT BANK, THE, SEOUL", category: CATEGORY_SIMPLE },
    { name: "MITSUBISHI HC CAPITAL INC", category: CATEGORY_SIMPLE },
    { name: "MITSUBISHI HC CAPITAL UK PLC", category: CATEGORY_SIMPLE },
    { name: "MIZUHO BANK, LTD", category: CATEGORY_SIMPLE },
    { name: "NORINCHUKIN BANK,THE,TOKYO", category: CATEGORY_SIMPLE },
    { name: "QNB Finance Ltd", category: CATEGORY_SIMPLE },
    { name: "SHINHAN BANK, SEOUL", category: CATEGORY_SIMPLE },
    { name: "SNB Funding Limited", category: CATEGORY_SIMPLE },
    { name: "SOCIETE GENERALE, PARIS", category: CATEGORY_SIMPLE },
    { name: "STANDARD CHARTERED BANK LONDON (ALL U.K. OFFICES)", category: CATEGORY_SIMPLE },
    { name: "Sumitomo Mitsui Finance and Leasing Company, Limited", category: CATEGORY_SIMPLE },
    { name: "WESTPAC BANKING CORPORATION", category: CATEGORY_SIMPLE },
    { name: "交银租赁管理香港有限公司", category: CATEGORY_SIMPLE },
    { name: "沙特阿拉伯王国政府", category: CATEGORY_SIMPLE },
    { name: "三井住友信托银行股份有限公司", category: CATEGORY_SIMPLE },
    { name: "中国光大银行股份有限公司卢森堡分行", category: CATEGORY_SIMPLE },
    { name: "中银航空租赁有限公司", category: CATEGORY_SIMPLE },
    { name: "法国BPCE银行", category: CATEGORY_SIMPLE },
    { name: "法国国民互助信贷银行", category: CATEGORY_SIMPLE },
    { name: "韩国政府", category: CATEGORY_SIMPLE },
    // 非简易分类债券（附件中重复名称已去重）
    { name: "CCCI TREASURE LIMITED", category: CATEGORY_NON_SIMPLE },
    { name: "CHINA HUANENG GROUP CO., LTD.", category: CATEGORY_NON_SIMPLE },
    { name: "CHINA SOUTHERN POWER GRID CO., LTD", category: CATEGORY_NON_SIMPLE },
    { name: "CHINA THREE GORGES CORPORATION", category: CATEGORY_NON_SIMPLE },
    { name: "CNOOC Limited", category: CATEGORY_NON_SIMPLE },
    { name: "DENSO CORPORATION", category: CATEGORY_NON_SIMPLE },
    { name: "Haitong UT Brilliant Limited", category: CATEGORY_NON_SIMPLE },
    { name: "ITOCHU CORPORATION", category: CATEGORY_NON_SIMPLE },
    { name: "MARUBENI CORPORATION", category: CATEGORY_NON_SIMPLE },
    { name: "Mitsubishi Corporation", category: CATEGORY_NON_SIMPLE },
    { name: "MITSUI & CO.,LTD.", category: CATEGORY_NON_SIMPLE },
    { name: "ORIX CORPORATION", category: CATEGORY_NON_SIMPLE },
    { name: "SUMITOMO CORPORATION", category: CATEGORY_NON_SIMPLE },
    { name: "Suntory Holdings Limited", category: CATEGORY_NON_SIMPLE },
    { name: "TAKEDA PHARMACEUTICAL COMPANY LIMITED", category: CATEGORY_NON_SIMPLE },
  ];

  var MOODY_POOL = ["Aaa", "Aa1", "Aa2", "Aa3", "A1", "A2", "A3", "Baa1", "Baa2", "—"];
  var SP_POOL = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "—"];
  var FITCH_POOL = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "—"];
  var YN = ["是", "否"];
  var CHANGE_POOL = ["无变化", "上调", "下调", "新评"];

  function hashStr(s) {
    var h = 0;
    for (var i = 0; i < s.length; i++) {
      h = (h << 5) - h + s.charCodeAt(i);
      h |= 0;
    }
    return Math.abs(h);
  }

  function pick(pool, seed) {
    return pool[seed % pool.length];
  }

  function buildMockRow(issuer, index) {
    var seed = hashStr(issuer.name + "|" + issuer.category);
    var moodys = pick(MOODY_POOL, seed);
    var sp = pick(SP_POOL, seed >> 2);
    var fitch = pick(FITCH_POOL, seed >> 4);
    var allBlank = moodys === "—" && sp === "—" && fitch === "—";
    var listed = pick(YN, seed >> 6);
    var delisted = listed === "是" ? pick(YN, seed >> 8) : "—";
    return {
      id: "ir-" + (index + 1),
      issuer: issuer.name,
      category: issuer.category,
      moodys: moodys,
      sp: sp,
      fitch: fitch,
      loss: pick(YN, seed >> 3),
      listed: listed,
      delisted: delisted,
      priceDrop: pick(YN, seed >> 5),
      noRatingReason: allBlank ? "暂未获公开评级信息，待补充官方披露" : "",
      ratingChanged: pick(CHANGE_POOL, seed >> 7),
      rssUrl: "",
    };
  }

  function dedupeIssuers(list) {
    var seen = {};
    var out = [];
    list.forEach(function (item) {
      var key = item.category + "\0" + item.name;
      if (seen[key]) return;
      seen[key] = true;
      out.push(item);
    });
    return out;
  }

  var rows = dedupeIssuers(RAW_ISSUERS).map(buildMockRow);
  var selectedId = null;
  var groupCollapsed = {};
  groupCollapsed[CATEGORY_SIMPLE] = false;
  groupCollapsed[CATEGORY_NON_SIMPLE] = false;

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

  function getQuery() {
    return (els.search && els.search.value ? els.search.value : "").trim().toLowerCase();
  }

  function filteredRows() {
    var q = getQuery();
    if (!q) return rows.slice();
    return rows.filter(function (r) {
      return r.issuer.toLowerCase().indexOf(q) !== -1;
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
              '<span class="ir-tree-name">' +
              escapeHtml(r.issuer) +
              "</span>" +
              "</button>" +
              "</li>"
            );
          })
          .join("");
        return (
          '<div class="ir-tree-group' +
          (collapsed ? " is-collapsed" : "") +
          '" data-group="' +
          escapeHtml(g.name) +
          '">' +
          '<button type="button" class="ir-tree-group-head" data-toggle-group="' +
          escapeHtml(g.name) +
          '" aria-expanded="' +
          (!collapsed ? "true" : "false") +
          '">' +
          '<span class="ir-tree-chevron" aria-hidden="true"></span>' +
          '<span class="ir-tree-group-title">' +
          escapeHtml(g.name) +
          "</span>" +
          '<span class="ir-tree-count">' +
          count +
          "</span>" +
          "</button>" +
          '<ul class="ir-tree-list" role="group">' +
          itemsHtml +
          "</ul>" +
          "</div>"
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
      if (row) {
        row.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
  }

  /**
   * 预留：对接官方 RSS 模块。
   * @param {string} issuerId
   */
  function handleOpenRssFeed(issuerId) {
    var row = rows.find(function (r) {
      return r.id === issuerId;
    });
    var name = row ? row.issuer : issuerId;
    if (row && row.rssUrl) {
      window.open(row.rssUrl, "_blank", "noopener,noreferrer");
      return;
    }
    setMsg("「" + name + "」官方 RSS 入口尚未配置，后续可在此对接订阅模块。");
  }

  window.handleOpenRssFeed = handleOpenRssFeed;

  function exportExcel() {
    if (typeof XLSX === "undefined") {
      setMsg("导出组件未加载，请检查网络后重试。", true);
      return;
    }
    var list = filteredRows();
    if (!list.length) {
      setMsg("当前无数据可导出。", true);
      return;
    }
    var sheetData = [
      [
        "发行体",
        "分类",
        "穆迪评级",
        "标普评级",
        "惠誉评级",
        "债务人最近一期決算是否亏损(是/否)",
        "是否上市（是/否）",
        "若上市，债务人是否被上市废止(是/否)",
        "债券价格是否大幅下跌（月环比跌幅超过5%）",
        "皆无评级的話请写明理由",
        "评级是否变化",
        "官方 RSS 链接",
      ],
    ];
    list.forEach(function (r) {
      sheetData.push([
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
        r.rssUrl || "",
      ]);
    });
    var wb = XLSX.utils.book_new();
    var ws = XLSX.utils.aoa_to_sheet(sheetData);
    ws["!cols"] = [
      { wch: 42 },
      { wch: 14 },
      { wch: 10 },
      { wch: 10 },
      { wch: 10 },
      { wch: 18 },
      { wch: 12 },
      { wch: 18 },
      { wch: 22 },
      { wch: 28 },
      { wch: 12 },
      { wch: 24 },
    ];
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

  function manualRefresh() {
    if (els.btnRefresh) {
      els.btnRefresh.classList.add("is-loading");
      els.btnRefresh.disabled = true;
    }
    setMsg("正在更新评级数据…");
    window.setTimeout(function () {
      rows = dedupeIssuers(RAW_ISSUERS).map(buildMockRow);
      refresh();
      if (els.btnRefresh) {
        els.btnRefresh.classList.remove("is-loading");
        els.btnRefresh.disabled = false;
      }
      setMsg("评级数据已更新（演示样本）。共 " + rows.length + " 家发行体。");
    }, 600);
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
  refresh();
})();
