# 页面功能与数据源说明

说明各页面功能对应的数据从哪里来、怎么接入。配置改完可热加载或重启服务。

相关配置文件：

| 文件 | 用途 |
|------|------|
| [`config/rss_feeds.yaml`](config/rss_feeds.yaml) | RSS 订阅、Google 新闻检索主题 |
| [`config/direct_sites.yaml`](config/direct_sites.yaml) | 通用无 RSS 站点（CSS 列表） |
| [`app/services/scrapers/godiva_source_collector.py`](app/services/scrapers/godiva_source_collector.py) | Godiva 专用爬虫 |
| [`app/services/scrapers/sina_724_collector.py`](app/services/scrapers/sina_724_collector.py) | 新浪 7×24 |
| [`app/services/scrapers/tdnet_collector.py`](app/services/scrapers/tdnet_collector.py) | 日本 TDnet 披露 |

业务模块：`A` 主体评估 · `B` 中东 · `C` 日本企业 · `D` 宏观市场。

采集顺序（风险日报 / 主体评估）：固定渠道 → 不足时秘塔补缺 → DeepSeek 整理入库。

---

## 1. 新闻日报 `/daily-news`

模块：`B`、`C`、`D`。展示近 24 小时资讯，可按模块筛选，可导出 Word。

| 功能 | 数据来源 | 链接方式 |
|------|----------|----------|
| 查看资讯列表 | 库表 `news_articles` | 页面查询本地库 |
| 采集近 24 小时 | 见下方 B/C/D | 侧栏「采集」→ 异步流水线 |
| 导出 Word | 当前筛选结果 | 本地导出，不另拉外网 |
| 运行状态 | 库表 `report_runs` | 本地库 |

### 模块 B（中东）

| 来源 | 方式 | 地址 / 说明 |
|------|------|-------------|
| BBC World | RSS 直连 | `http://feeds.bbci.co.uk/news/world/rss.xml` |
| NHK 国際 | RSS 直连 | `https://www3.nhk.or.jp/rss/news/cat6.xml` |
| Google 新闻检索 | Google News RSS | 主题见 `rss_feeds.yaml` → `queries.B` |
| 新浪财经 7×24 | JSON API | `https://zhibo.sina.com.cn/api/zhibo/feed`（国际标签） |
| 秘塔搜索 | API | 主源不足或全挂时补缺（需 `MITA_API_KEY`） |

### 模块 C（日本重点企业）

| 来源 | 方式 | 地址 / 说明 |
|------|------|-------------|
| Reuters Business | RSS 直连 | `https://feeds.reuters.com/reuters/businessNews` |
| NHK 経済 | RSS 直连 | `https://www3.nhk.or.jp/rss/news/cat5.xml` |
| Google 新闻（日本版） | Google News RSS | `queries.C`（官网 site:、適時開示等） |
| TDnet 適時開示 | 专用采集 | `release.tdnet.info` 相关接口 |
| EDINET | 门户备注 | 人工核对入口，非正式正文入库 |
| 秘塔搜索 | API | 主源不足时补缺 |

### 模块 D（宏观与市场）

| 来源 | 方式 | 地址 / 说明 |
|------|------|-------------|
| Reuters Business | RSS 直连 | 同上 |
| BBC World | RSS 直连 | 同上 |
| NHK 経済 / 国際 | RSS 直连 | 同上 |
| Google 新闻检索 | Google News RSS | `queries.D`（原油、央行、贵金属等） |
| 新浪财经 7×24 | JSON API | 宏观 / 数据 / 市场 / 央行 / A股 标签 |
| 秘塔搜索 | API | 主源不足时补缺 |

另有 `/daily-news-7x24`：按日快照查看，数据仍来自上述 B/C/D 采集结果。

---

## 2. 主体评估 `/entity-assessment`

模块：`A`。监控主体、风险事件、授信等级；可导出 Word。

| 功能 | 数据来源 | 链接方式 |
|------|----------|----------|
| 监控主体列表 | 库表 `target_entities` | 本地库（默认含 Godiva、普洛斯） |
| 风险事件 / 授信 | `entity_risks`、`credit_updates` | 本地库；采集后由模型整理写入 |
| 采集近 24 小时 | 见下方模块 A | 侧栏「采集」→ 异步流水线 |
| 导出 Word | 当前主体评估内容 | 本地导出 |
| 无真实结果时 | 演示样本 | 自动回填，避免空白（链接含 `example.com/mock`） |

### 模块 A 数据源

#### RSS 直连

| 来源 | 地址 |
|------|------|
| ICI Cocoa Initiative | `https://www.cocoainitiative.org/rss.xml` |
| Cacao.ci | `https://cacao.ci/feed/` |
| Reuters Business | `https://feeds.reuters.com/reuters/businessNews` |

#### Google 新闻检索

主题见 `queries.A`：Godiva、日本歌帝梵、可可供应链、普洛斯等。

#### 无 RSS 专用爬虫（`godiva_source_collector`）

| 来源 | 方式 | 地址 |
|------|------|------|
| 加纳可可局 COCOBOD | HTML 列表 | `https://cocobod.gh/news/` |
| 国际可可组织 ICCO | WordPress API | `https://www.icco.org/wp-json/wp/v2/posts` |
| ICCO 月度市场报告 | 统计页最新 PDF | `https://www.icco.org/statistics/` |
| 欧盟乳制品观测站 | HTML 资料包 | `https://agriculture.ec.europa.eu/.../milk_en` |
| 欧盟糖观测站 | HTML 资料包 | `https://agriculture.ec.europa.eu/.../sugar_en` |
| 日本百货店协会销售额 | 月度 PDF | `https://www.depart.or.jp/store_sale/` |

#### 补缺

| 来源 | 方式 |
|------|------|
| 秘塔搜索 | API，主源不足时按主体名补缺 |
| DeepSeek | 整理为风险事件与授信依据 |

#### 暂未稳定接入（页面可开，但无法保证准确自动采集）

| 来源 | 原因 |
|------|------|
| ICE Cocoa Futures | 行情接口 403 / 反爬 |
| `shop.godiva.co.jp/stores` | Nuxt 前端渲染，无稳定门店 JSON |
| `conseilcafecacao.ci` | 证书 / 鉴权异常；已用 ICI、Cacao.ci RSS 替代 |

---

## 3. 深度研报 `/deep-reports`

不走模块 A/B/C/D 的日报流水线。按行业或主体生成长篇报告。

| 功能 | 数据来源 | 链接方式 |
|------|----------|----------|
| 上传文件 / 添加网址 | 库表 `module_data_sources` | 页右侧「数据源」；仅本页使用 |
| 生成研报 | 权威材料优先 + 秘塔检索 + DeepSeek | `POST /api/v1/industry/analyze` |
| 在线阅读 / 列表 | 库表 `industry_reports` | 本地库 |
| 导出 Word | 已生成研报 | 本地导出 |

说明：权威材料只服务深度研报，不会写入新闻日报或主体评估列表。

---

## 4. 国际评级 `/intl-ratings`

| 功能 | 数据来源 | 链接方式 |
|------|----------|----------|
| 发行体列表与评级表 | 流水线快照 `data/intl_ratings/latest.json` | `GET /api/v1/intl-ratings` |
| 搜索 / 侧栏索引 | 同上 | 浏览器内过滤 |
| 手动更新 | `intl_ratings` 后台任务（默认 quick） | `POST /api/v1/intl-ratings/refresh` |
| 导出 Excel | 当前表格 | 浏览器端导出 |
| CLI 全量 | `python -m intl_ratings.main` | 含 Playwright 等完整源 |
| 近 90 日评级变动 | **待补充** | `rating_change_feed` |
| 归母净利润 | `yfinance.Ticker.financials['Net Income']`；A股 `ak.stock_financial_abstract`；美股另存 SEC 原文 | SPV 继承母公司 |
| 国内公告 | AkShare 巨潮 `stock_zh_a_disclosure_report_cninfo` 等 | 落盘 `logs/raw_responses/` |
| 美股 SEC 财报 | `sec-edgar-downloader`（10-K/10-Q） | `data/intl_ratings/sec_edgar/`，需 `us_ticker`/`cik` |
| 债券月环比跌幅 | `tvDatafeed.get_hist` 优先；其次 `yfinance.history(period="1mo")` | `issuers.json` 填 `tv_symbol`/`tv_exchange` |
| 皆无评级理由 | DeepSeek `temperature=0.1` JSON | 三大均为 NR 时触发 |
| 导出 Excel | 灰底表头、居中、列宽自适应 | `data/intl_ratings/output/` |
| 溯源 | `logs/raw_responses/`、`logs/error.log` | |

CLI：`python -m intl_ratings.main`。配置：`config/intl_ratings.yaml`、`config/issuers.json`。

说明：`ak.stock_comment_detail_zjl_em` 为资金流接口，**不是**三大机构主体评级，代码仅探测落盘，不写入评级列。

---

## 5. 通用说明

| 项目 | 说明 |
|------|------|
| 热加载 RSS | `POST /api/v1/pipeline/rss-config/reload` |
| 热加载直连站点 | `POST /api/v1/pipeline/direct-sites/reload` |
| 密钥 | `.env` 中 `MITA_API_KEY`、`DEEPSEEK_API_KEY` |
| 改主题 / 订阅 | 编辑 `config/rss_feeds.yaml` |
| 改 Godiva 爬虫 | 编辑 `godiva_source_collector.py` |
