# 企业风险情报与日报系统

汇总近 24 小时重要资讯，整理后入库展示，并可导出 Word。

各页面功能对应的数据源与接入方式，见 **[DATA_SOURCES.md](DATA_SOURCES.md)**。

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

在 `.env` 中填写 `MITA_API_KEY`、`DEEPSEEK_API_KEY`。

---

## 页面

| 页面 | 地址 |
|------|------|
| 新闻日报 | http://127.0.0.1:8000/daily-news |
| 主体评估 | http://127.0.0.1:8000/entity-assessment |
| 深度研报 | http://127.0.0.1:8000/deep-reports |
| 国际评级 | http://127.0.0.1:8000/intl-ratings |

首页 `/` 进入新闻日报。

---

## 功能概要

| 页面 | 做什么 |
|------|--------|
| 新闻日报 | 按模块 B/C/D 汇总近 24 小时资讯，可导出 Word |
| 主体评估 | 监控企业风险与授信变化（模块 A），可导出 Word |
| 深度研报 | 按行业/主体生成长篇报告；右侧可上传权威材料 |
| 国际评级 | 发行体评级监测表（当前为演示数据），可导出 Excel |

授信等级：正常 → 关注 → 预警 → 高风险。

业务模块：`A` 主体评估 · `B` 中东 · `C` 日本企业 · `D` 宏观市场。

采集失败时尽量保留上次结果；主体评估无真实结果时可能回填演示样本。

---

## 配置（常用）

| 变量 | 作用 | 默认 |
|------|------|------|
| `NEWS_WINDOW_HOURS` | 资讯时间窗口（小时） | `24` |
| `PIPELINE_ASYNC_DEFAULT` | 是否默认异步采集 | `true` |
| `RSS_CONFIG_PATH` | 订阅与检索配置 | `config/rss_feeds.yaml` |
| `DAILY_PIPELINE_CRON` | 定时采集 | `0 6 * * *` |

完整环境变量见 [`.env.example`](.env.example)。改 RSS / 检索主题：编辑 `config/rss_feeds.yaml`，再 `POST /api/v1/pipeline/rss-config/reload`，或重启。

---

## 目录

```
app/           页面、接口、采集与分析
config/        rss_feeds.yaml、direct_sites.yaml
scripts/       init_db.py 等
DATA_SOURCES.md  各页功能与数据源说明
```

---

## 数据库（主要表）

| 表 | 存什么 |
|------|--------|
| `news_articles` | 日报资讯 |
| `target_entities` / `entity_risks` / `credit_updates` | 主体与授信 |
| `industry_reports` | 深度研报 |
| `module_data_sources` | 研报权威材料 |
| `pipeline_jobs` / `report_runs` | 采集任务与运行状态 |

---

## API（常用）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/news` | 资讯列表 |
| POST | `/api/v1/pipeline/run` | 开始采集 |
| GET | `/api/v1/pipeline/jobs/{job_id}` | 任务状态 |
| POST | `/api/v1/pipeline/rss-config/reload` | 重载 RSS 配置 |
| POST | `/api/v1/industry/analyze` | 生成深度研报 |
| GET | `/api/v1/export/docx` | 导出日报 |
| GET | `/api/v1/entities/{id}/export/docx` | 导出主体评估 |
