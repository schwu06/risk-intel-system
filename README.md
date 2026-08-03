# 企业风险情报与日报系统

银行级企业风险情报平台：权威数据源优先 + MiTa 搜索补充 + DeepSeek 结构化分析 + SQLite 存储 + FastAPI/Jinja2 仪表盘 + Word 导出 + ECharts 图表。

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
  exporters/docx_report.py Word 导出（含图表）
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
| `PIPELINE_LLM_TOP_K` | 送入 DeepSeek 前粗筛条数 | `12` |
| `PIPELINE_LLM_CACHE_HOURS` | LLM 结果缓存小时数 | `168` |
| `PIPELINE_MITA_QUERY_PAUSE_SECONDS` | 秘塔查询间隔（秒） | `0.8` |
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
| 近24小时采集 | 点击后异步采集：RSS/Google News + 秘塔 + 正文提取 + DeepSeek；失败保留上次结果；LLM 失败则降级入库原始条目 |
| 分阶段流水线 | collect → analyze → publish；仅有新结果时替换，避免页面变空白 |
| 秘塔搜索 | 作为广度补充，始终与 RSS 并行采集 |
| RSS 外置配置 | `config/rss_feeds.yaml` 管理查询与直连源，支持启用/禁用与优先级 |
| 深度研报 | 长篇结构化报告，在线预览与 Word 导出 |
| 韧性增强 | LLM 缓存、Top-K 粗筛、调度防重入、feed 级成败统计 |

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/news` | 资讯列表（`news_articles`） |
| GET | `/api/v1/entities` | 监控主体列表 |
| GET | `/api/v1/entities/{id}/risks` | 主体风险事件 |
| GET | `/api/v1/entities/{id}/credit-updates` | 授信变更日志 |
| POST | `/api/v1/pipeline/run` | 运行流水线（默认异步，返回 `job_id`；`async_mode=false` 可同步） |
| GET | `/api/v1/pipeline/jobs/{job_id}` | 查询异步任务状态与结果 |
| GET | `/api/v1/pipeline/rss-config` | 查看当前 RSS 配置摘要 |
| POST | `/api/v1/pipeline/rss-config/reload` | 重新加载 `rss_feeds.yaml` |
| POST | `/api/v1/industry/analyze` | 生成深度研报 |
| GET | `/api/v1/export/docx?report_date=...&module_codes=B,C,D` | 导出 Word（B=中东日报，C=日本企业，D=每日宏观） |
| GET | `/api/v1/health` | 健康检查 |
