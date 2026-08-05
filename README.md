# 企业风险情报与日报系统

近 24 小时风险情报采集与展示：RSS / Google News / TDnet + 秘塔补充 + DeepSeek 结构化分析，SQLite 存储，FastAPI 仪表盘，Word 导出。

---

## 启动

```powershell
cd "d:\system_project1(1)"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python scripts\init_db.py
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`.env` 需填写 `MITA_API_KEY`、`DEEPSEEK_API_KEY`。

---

## 页面

| 页面 | 地址 |
|------|------|
| 风险日报 | http://127.0.0.1:8000/daily-news |
| 主体评估 | http://127.0.0.1:8000/entity-assessment |
| 深度研报 | http://127.0.0.1:8000/deep-reports |

`/` → 风险日报；`/industry-analysis` → 深度研报。

---

## 功能

| 功能 | 说明 |
|------|------|
| 风险日报 | 模块 B/C/D 近 24 小时资讯，页面展示与 Word 导出 |
| 主体评估 | 监控主体、风险事件、授信等级与变更日志 |
| 深度研报 | 行业/主体长篇报告，在线预览与 Word 导出 |
| 权威数据源 | 侧栏上传 txt / xlsx / docx / pdf 或网址，采集时优先参考 |
| 异步采集 | collect → analyze → publish；失败保留上次结果；LLM 失败则降级入库 |
| RSS 配置 | `config/rss_feeds.yaml`，支持热加载 |

授信等级：正常 → 关注 → 预警 → 高风险。

---

## 数据源

配置文件：[`config/rss_feeds.yaml`](config/rss_feeds.yaml)（改后可 `POST /api/v1/pipeline/rss-config/reload` 或重启）。

模块：`A` 主体评估 · `B` 中东日报 · `C` 日本重点大型企业 · `D` 每日宏观与市场情报。

### 采集通道

| 通道 | 说明 |
|------|------|
| 用户上传 | 侧栏文件 / 网址，分析优先采用 |
| Google News RSS | YAML `queries`；默认中国版，模块 C 为日本版 `ja/JP` |
| 直连 RSS | YAML `feeds` 固定 Feed |
| TDnet | 模块 C：適時開示列表 + PDF 正文 |
| 秘塔搜索 | 主源候选不足或主源故障时补充；依赖 `MITA_API_KEY` |
| 正文提取 | 对命中条目抓网页 / PDF 正文 |
| DeepSeek | 结构化中文资讯字段；依赖 `DEEPSEEK_API_KEY` |

### 直连 RSS（已启用）

| 名称 | URL | 模块 |
|------|-----|------|
| Reuters Business | https://feeds.reuters.com/reuters/businessNews | A, C, D |
| BBC World | http://feeds.bbci.co.uk/news/world/rss.xml | B, D |
| NHK 経済 | https://www3.nhk.or.jp/rss/news/cat5.xml | C, D |
| NHK 国際 | https://www3.nhk.or.jp/rss/news/cat6.xml | B, D |

### Google News 主题（摘要）

| 模块 | 主题 |
|------|------|
| A | Godiva、普洛斯 GLP |
| B | 中东地缘（英）、中东政策（中） |
| C | 商社 / 电装邮船大和、企业官网 `site:`、TDnet、日経、PR TIMES、英文 IR |
| D | 原油 LNG、央行宏观、贵金属股市 |

完整查询词见 YAML。模块 C 的 TDnet 证券代码见 `app/services/scrapers/tdnet_collector.py`。

### 域名规则

种子名单见 `app/services/domain_rules.py`。

| 类型 | 域名 |
|------|------|
| 白名单 | tdnet.info、disclosure.edinet-fsa.go.jp、digital.go.jp、courts.go.jp、reuters.com、bloomberg.com、gov.cn |
| 黑名单 | reddit.com、twitter.com / x.com |

---

## 配置

| 变量 | 说明 | 默认 |
|------|------|------|
| `NEWS_WINDOW_HOURS` | 近 N 小时资讯窗口 | `24` |
| `NETWORK_RETRY_ATTEMPTS` | 外网重试次数 | `3` |
| `PIPELINE_ASYNC_DEFAULT` | 流水线默认异步 | `true` |
| `PIPELINE_LLM_TOP_K` | 送入 DeepSeek 前粗筛条数 | `8` |
| `PIPELINE_LLM_CACHE_HOURS` | LLM 缓存小时数 | `168` |
| `PIPELINE_MITA_QUERY_PAUSE_SECONDS` | 秘塔查询间隔（秒） | `0.3` |
| `PIPELINE_MITA_MIN_ITEMS` | 主源达标则跳过秘塔；`0` 跟随 Top-K | `0` |
| `PIPELINE_MITA_FORCE_ON_PRIMARY_FAIL` | 主源故障时强制秘塔 | `true` |
| `RSS_CONFIG_PATH` | RSS 配置路径 | `config/rss_feeds.yaml` |
| `DAILY_PIPELINE_CRON` | 定时采集 cron | `0 6 * * *` |

---

## 目录

```
app/
  main.py                 页面路由（8000）
  config.py               配置与模块定义
  schemas.py              API 模型
  api/routes.py           REST API
  database/               ORM / 会话
  services/               采集、分析、流水线、RSS、授信等
  exporters/docx_report.py Word 导出
  templates/              页面模板
  static/                 CSS / JS
config/
  rss_feeds.yaml          RSS / Google News 配置
scripts/init_db.py        初始化数据库
```

---

## 数据库

| 表 | 用途 |
|------|------|
| `news_articles` | 风险日报资讯 |
| `target_entities` | 监控主体 |
| `entity_risks` | 主体风险事件 |
| `credit_updates` | 授信变更日志 |
| `industry_reports` | 深度研报 |
| `report_runs` | 模块运行状态 |
| `pipeline_jobs` | 异步采集任务 |
| `pipeline_artifacts` | 采集中间结果 |
| `llm_response_cache` | LLM 缓存 |
| `content_fingerprints` | 去重指纹 |
| `search_logs` | 检索日志 |
| `module_data_sources` | 侧栏权威数据源 |

---

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/news` | 资讯列表 |
| GET | `/api/v1/sources/rss` | 近 24 小时 RSS 采集列表 |
| GET | `/api/v1/data-sources` | 权威数据源列表 |
| POST | `/api/v1/data-sources/upload` | 上传文件 |
| POST | `/api/v1/data-sources/url` | 添加网址 |
| DELETE | `/api/v1/data-sources/{id}` | 删除数据源 |
| GET | `/api/v1/entities` | 主体列表 |
| GET | `/api/v1/entities/{id}/risks` | 主体风险事件 |
| GET | `/api/v1/entities/{id}/credit-updates` | 授信变更 |
| GET | `/api/v1/entities/{id}/export/docx` | 导出主体评估 Word |
| POST | `/api/v1/pipeline/run` | 启动采集（默认异步，返回 `job_id`） |
| GET | `/api/v1/pipeline/jobs/{job_id}` | 任务状态 |
| GET | `/api/v1/pipeline/rss-config` | RSS 配置摘要 |
| POST | `/api/v1/pipeline/rss-config/reload` | 热加载 RSS 配置 |
| POST | `/api/v1/industry/analyze` | 生成深度研报 |
| GET | `/api/v1/industry/reports` | 研报列表 |
| GET | `/api/v1/industry/export/docx/{id}` | 导出研报 Word |
| GET | `/api/v1/export/docx` | 导出日报 Word（`module_codes=B,C,D`） |
