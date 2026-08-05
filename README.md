# 企业风险情报与日报系统

银行级企业风险情报平台：权威数据源优先 + RSS/Google News + TDnet 披露（模块 C）+ MiTa 搜索补充 + DeepSeek 结构化分析 + SQLite 存储 + FastAPI/Jinja2 仪表盘 + Word 导出 + ECharts 图表。

采集侧韧性：异步任务 + collect→analyze→publish 分阶段落盘 + 失败保留上次结果 + LLM 降级入库 + RSS 外置配置。

## 目录结构

```
app/
  main.py                 FastAPI + Jinja2 三页仪表盘（8000 端口）
  config.py               配置、模块 taxonomy、授信等级常量
  schemas.py              API 模型
  api/routes.py           REST API
  database/               ORM、schema.sql、会话（含自动补列迁移）
  services/               MiTa、DeepSeek、流水线、异步任务、RSS、授信、行业分析
  exporters/docx_report.py Word 导出（日报为纯新闻汇总；行业/主体报告可含图表）
  templates/              中文 HTML 页面（base + 三页）
  static/                 CSS / JS（含异步流水线轮询）
config/
  rss_feeds.yaml          RSS / Google News 外置数据源（可热加载）
scripts/init_db.py        初始化与演示数据
```

## 配置

复制 `.env.example` 为 `.env`，填入 `MITA_API_KEY` 与 `DEEPSEEK_API_KEY`。

常用可选变量：

| 变量 | 说明 | 默认 |
|------|------|------|
| `NEWS_WINDOW_HOURS` | 近 N 小时资讯窗口 | `24` |
| `NETWORK_RETRY_ATTEMPTS` | 外网请求重试次数 | `3` |
| `PIPELINE_ASYNC_DEFAULT` | 流水线默认异步 | `true` |
| `PIPELINE_LLM_TOP_K` | 送入 DeepSeek 前粗筛条数 | `8` |
| `PIPELINE_LLM_CACHE_HOURS` | LLM 结果缓存小时数 | `168` |
| `PIPELINE_MITA_QUERY_PAUSE_SECONDS` | 秘塔查询间隔（秒） | `0.3` |
| `PIPELINE_MITA_MIN_ITEMS` | 主源有效候选达标则跳过秘塔；`0` = 跟随 `PIPELINE_LLM_TOP_K` | `0` |
| `PIPELINE_MITA_FORCE_ON_PRIMARY_FAIL` | 主源硬故障时强制秘塔补缺 | `true` |
| `RSS_CONFIG_PATH` | RSS 配置文件路径 | `config/rss_feeds.yaml` |
| `DAILY_PIPELINE_CRON` | 定时采集 cron | `0 6 * * *` |

修改 `config/rss_feeds.yaml` 后可调用 `POST /api/v1/pipeline/rss-config/reload` 热加载，或重启服务。

## 一键安装与启动

```powershell
cd "d:\system_project1(1)"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python scripts\init_db.py
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

浏览器访问（一级页面）：

- 风险日报：`http://127.0.0.1:8000/daily-news`（读 `news_articles`：中东日报 / 日本重点大型企业 / 每日宏观与市场情报）
- 主体评估：`http://127.0.0.1:8000/entity-assessment`（主体 / 风险事件 / 授信）
- 深度研报：`http://127.0.0.1:8000/deep-reports`（读 `industry_reports`）

兼容重定向：`/` → `/daily-news`，`/industry-analysis` → `/deep-reports`。

## 数据库（共用同一实例）

| 表 | 用途 |
|------|------|
| `news_articles` | 风险日报资讯 |
| `target_entities` | 重点监控主体 |
| `entity_risks` | 主体风险事件 |
| `credit_updates` | 授信等级变更日志 |
| `industry_reports` | 深度研报 |
| `report_runs` | 模块运行状态（含 phase / funnel / 是否保留旧结果） |
| `pipeline_jobs` | 异步采集任务 |
| `pipeline_artifacts` | collect / analyze 中间落盘 |
| `llm_response_cache` | DeepSeek 结构化结果缓存 |
| `content_fingerprints` | 跨源稳定去重指纹 |
| `search_logs` | 单次检索日志 |

授信等级（递进）：**正常 → 关注 → 预警 → 高风险**（由事件风险等级 低/中/高/极高 映射）。启动时自动回填旧 `daily_risk_entries` / `industry_analysis_reports` 至新表；流水线双写新旧表。

## 主要功能

| 功能 | 说明 |
|------|------|
| 三页架构 | 风险日报 / 主体评估 / 深度研报，共用同一数据库 |
| 授信更新与预警 | 主体页展示最新授信等级与变更日志 |
| 权威数据源 | 各模块支持上传 txt/xlsx/docx/pdf 及网址 |
| 近24小时采集 | 点击后异步采集：RSS/Google News + TDnet（模块 C）+ 秘塔 + 正文提取 + DeepSeek；失败保留上次结果；LLM 失败则降级入库原始条目 |
| 分阶段流水线 | collect → analyze → publish；仅有新结果时替换，避免页面变空白 |
| 秘塔搜索 | 作为广度补充：**主源有效候选不足时才补**；主源硬故障则强制补；依赖 `MITA_API_KEY` |
| RSS 外置配置 | `config/rss_feeds.yaml` 管理查询与直连源，支持启用/禁用与优先级 |
| 深度研报 | 长篇结构化报告，在线预览与 Word 导出 |
| 韧性增强 | LLM 缓存、Top-K 粗筛、调度防重入、feed 级成败统计 |

## 当前数据源 / Current Data Sources

配置主文件：`config/rss_feeds.yaml`（可热加载）。模块：`A` 主体评估 · `B` 中东日报 · `C` 日本重点大型企业 · `D` 每日宏观与市场情报。

### 1. 按采集通道分类 / By Ingestion Channel

| 分类 Category | 说明 Description | 状态 Status |
|---------------|------------------|-------------|
| **用户权威上传 Authoritative upload** | 侧栏上传 txt/xlsx/docx/pdf 或网址；分析时优先采用 / Sidebar uploads & URL notes; authority-first in LLM | 启用 Enabled |
| **Google News RSS 检索** | YAML `queries.*` → Google News RSS；默认 `zh-CN/CN`；**模块 C 为日本版 `ja/JP`** | 启用 Enabled |
| **直连 RSS Direct feeds** | YAML `feeds` 中 `type: direct` 的固定 Feed URL | 见下表 See below |
| **TDnet 適時開示** | 模块 C 专用：やのしん列表 API + 官方日列表页回退；按证券代码拉 PDF 披露 | 启用 Enabled |
| **EDINET** | 法定披露检索入口备注，**不入库正文**（需官方 API Key 后可扩展） | 仅核对 Reference only |
| **秘塔搜索 MiTa / Metaso** | 各模块关键词检索；**RSS/TDnet 有效候选不足或主源硬故障时才调用**；依赖 `MITA_API_KEY` | 启用（密钥有效时；不足才补） |
| **网页/PDF 正文提取** | trafilatura / pypdf 等，对命中条目抓正文再送 LLM | 启用 Enabled |
| **DeepSeek 结构化分析** | 输出中文字段资讯条目；依赖 `DEEPSEEK_API_KEY`；失败则降级原始摘要入库 | 启用（密钥有效时） |

### 2. 直连 RSS（YAML `feeds`）/ Direct RSS Feeds

| 名称 Name | URL | 模块 Modules | 状态 |
|-----------|-----|--------------|------|
| Reuters Business | `https://feeds.reuters.com/reuters/businessNews` | C, D, A | ✅ 启用 |
| BBC World | `http://feeds.bbci.co.uk/news/world/rss.xml` | B, D | ✅ 启用 |
| NHK 経済 | `https://www3.nhk.or.jp/rss/news/cat5.xml` | C, D | ✅ 启用 |
| NHK 国際 | `https://www3.nhk.or.jp/rss/news/cat6.xml` | B, D | ✅ 启用 |
| Reuters World | `https://feeds.reuters.com/Reuters/worldNews` | B, D | ⛔ 禁用 |
| PR TIMES 总线 | `https://prtimes.jp/index.rdf` | C | ⛔ 禁用（改走下方 `site:prtimes.jp` 查询） |

### 3. Google News 查询主题（YAML `queries`）/ Google News Query Topics

**模块 A · 主体评估 / Entity Assessment**（上帝iva、普洛斯；中英检索）

| 标签 Label | 检索主题 Topic |
|------------|----------------|
| Godiva 动态 | Godiva / 诉讼 / recall 等英文新闻 |
| 普洛斯 GLP | 普洛斯 / GLP logistics property |

**模块 B · 中东日报 / Middle East Daily**（默认 Google 中国版）

| 标签 Label | 检索主题 Topic |
|------------|----------------|
| 中东地缘 | Middle East / Gaza / Iran / Israel 等英文 |
| 中东政策中文 | 中东政策、官方声明、制裁等中文 |

**模块 C · 日本重点大型企业 / Japan Large Corporates**（Google **日本版** `ja/JP`）

| 标签 Label | 检索主题 Topic |
|------------|----------------|
| 五大商社 適時開示 | 三菱商事·三井物産·伊藤忠·住友商事·丸紅 × 適時開示/IR/決算 |
| 电装 邮船 大和 | デンソー·日本郵船·大和証券 × 適時開示/IR |
| 企业官网 `site:` | mitsubishicorp / mitsui / itochu / sumitomocorp / marubeni / denso / nyk / daiwa |
| TDnet 披露相关 | 监控企业名 + 適時開示 / TDnet |
| 日経 | `site:www.nikkei.com` + 监控企业名 |
| PR TIMES | `site:prtimes.jp` + 监控企业名 |
| 英文 IR 补充 | Mitsubishi Corp / Mitsui & Co 等英文 IR·earnings |

**模块 D · 每日宏观与市场情报 / Macro & Markets**

| 标签 Label | 检索主题 Topic |
|------------|----------------|
| 原油 LNG | oil / crude / LNG |
| 央行与宏观 | Fed / BOJ / 美联储 / 日本央行 |
| 贵金属股市 | gold / stock market / 贵金属 / 股市 |

### 4. 日本官方披露（模块 C）/ Japan Official Disclosures (Module C)

| 源 Source | 方式 Method | 监控对象 Targets | 状态 |
|-----------|-------------|------------------|------|
| **TDnet** | やのしん `webapi.yanoshin.jp` 列表 JSON；失败回退 `release.tdnet.info` 日列表 HTML；正文为官方 PDF | 三菱商事 `8058`、三井物産 `8031`、伊藤忠 `8001`、住友商事 `8053`、丸紅 `8002`、デンソー `6902`、日本郵船 `9101`、大和証券 `8601` | ✅ 入库 |
| **EDINET** | `disclosure.edinet-fsa.go.jp` 检索入口写入采集备注 | 同上企业（名称检索） | 🔗 仅人工核对 |
| **企业 IR 官网** | 多数无稳定公开 RSS；经 Google News `site:` 接入（见上表） | 各社官网新闻/IR 页 | ✅ 经 Google |

### 5. 秘塔补充检索要点 / MiTa Supplement Queries

流水线先采 RSS（及模块 C 的 TDnet），再统计近窗内**实质性有效候选** `primary_valid`：

- `primary_valid ≥ target` 且主源无硬故障 → **跳过秘塔**（`mita_skipped=enough`）
- 否则按缺口限流查询条数补齐；达标或用尽查询预算后提前结束
- `target`：默认等于 `PIPELINE_LLM_TOP_K`；模块 A 上限 3；模块 C 已有 TDnet 实质披露时上限 6
- RSS 全挂（模块 C 另需 TDnet API 也失败）且 `PIPELINE_MITA_FORCE_ON_PRIMARY_FAIL=true` → **强制补**

按模块生成近窗检索词（见 `app/config.py` → `module_search_queries`）：

| 模块 | 检索侧重 Query focus |
|------|----------------------|
| A | 主体名 + 司法监管 / 金融经营 / 舆论 / 供应链 等分类 |
| B | 中东政策·官方声明（中）+ Middle East geopolitics（英） |
| C | 日语正式社名 + 適時開示 / ニュースリリース / IR / 決算 |
| D | 原油、LNG、贵金属、股市、日银、美联储、地缘等主题 |

### 6. 域名白/黑名单预置 / Domain Allow & Deny Lists

种子域名见 `app/services/domain_rules.py`（可在库表扩展）：

| 类型 | 域名 Domains |
|------|--------------|
| 白名单 Allow | `tdnet.info`、`disclosure.edinet-fsa.go.jp`、`digital.go.jp`、`courts.go.jp`、`reuters.com`、`bloomberg.com`、`gov.cn` |
| 黑名单 Deny | `reddit.com`、`twitter.com` / `x.com`（社交媒体噪声） |

> 修改数据源：编辑 `config/rss_feeds.yaml` 后调用 `POST /api/v1/pipeline/rss-config/reload`，或重启服务。TDnet 证券代码映射见 `app/services/scrapers/tdnet_collector.py`。

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/news` | 资讯列表（`news_articles`） |
| GET | `/api/v1/sources/rss` | 近 24 小时 RSS 采集动态（供程序查询；侧栏不展示） |
| GET | `/api/v1/entities` | 监控主体列表 |
| GET | `/api/v1/entities/{id}/risks` | 主体风险事件 |
| GET | `/api/v1/entities/{id}/credit-updates` | 授信变更日志 |
| POST | `/api/v1/pipeline/run` | 运行流水线（默认异步，返回 `job_id`；`async_mode=false` 可同步） |
| GET | `/api/v1/pipeline/jobs/{job_id}` | 查询异步任务状态与结果 |
| GET | `/api/v1/pipeline/rss-config` | 查看当前 RSS 配置摘要 |
| POST | `/api/v1/pipeline/rss-config/reload` | 重新加载 `rss_feeds.yaml` |
| POST | `/api/v1/industry/analyze` | 生成深度研报 |
| GET | `/api/v1/export/docx?report_date=...&module_codes=B,C,D` | 导出《24小时核心新闻情报汇总》Word（B=中东日报，C=日本企业，D=每日宏观） |
| GET | `/api/v1/health` | 健康检查 |
