# 企业风险情报与日报系统

汇总近 24 小时重要资讯，整理后入库展示，并可导出 Word。

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
| 风险日报 | http://127.0.0.1:8000/daily-news |
| 主体评估 | http://127.0.0.1:8000/entity-assessment |
| 深度研报 | http://127.0.0.1:8000/deep-reports |
| 国际评级 | http://127.0.0.1:8000/intl-ratings |

首页 `/` 进入风险日报。

---

## 功能

1. **风险日报**：按模块汇总近 24 小时资讯，可导出 Word。
2. **主体评估**：查看监控企业、风险事件与授信变化。
3. **深度研报**：生成行业或主体长篇报告，可在线阅读并导出。
4. **国际评级**：按简易/非简易分类查看发行体国际信用评级，支持搜索、导出 Excel。
5. **权威材料**：仅深度研报页右侧可上传文件或网址，生成报告时优先参考。
6. **一键采集**：后台异步执行；失败时尽量保留上次结果。

授信等级：正常 → 关注 → 预警 → 高风险。

业务模块：

| 代码 | 内容 |
|------|------|
| A | 主体评估 |
| B | 中东日报 |
| C | 日本重点大型企业 |
| D | 每日宏观与市场情报 |

---

## 数据怎么来

一次采集按下面顺序进行。

### 1. 权威材料（仅深度研报）

深度研报页右侧「数据源」里的文件、网址会优先进入分析。有上传内容时，系统先看这些材料。

### 2. 再从固定渠道拉取新闻

风险日报与主体评估按模块从配置好的来源取近 24 小时内容：

1. **媒体订阅**：路透、BBC、NHK 等固定订阅地址。
2. **新闻检索**：按主题在 Google 新闻中检索（日本企业模块用日本地区设置）。
3. **日本企业披露**（仅模块 C）：从 TDnet 拉取相关企业的公开披露。

订阅地址与检索主题写在 [`config/rss_feeds.yaml`](config/rss_feeds.yaml)。修改后可热加载，或重启服务。

当前已接入的媒体订阅：

| 来源 | 用于模块 |
|------|----------|
| Reuters Business | A、C、D |
| BBC World | B、D |
| NHK 経済 | C、D |
| NHK 国際 | B、D |

各模块主要检索方向：

| 模块 | 关注什么 |
|------|----------|
| A | Godiva、普洛斯等主体动态 |
| B | 中东局势与政策 |
| C | 重点日本企业的披露、官网与财经报道 |
| D | 原油、央行、贵金属与股市 |

### 3. 不够时再补充搜索

若上面拿到的有效内容偏少，或主渠道整体失败，再调用秘塔搜索补齐。需要有效的 `MITA_API_KEY`。

### 4. 整理后入库展示

1. 尽量抓取原文正文。
2. 用 DeepSeek 整理成标题、摘要、分类、重要度等字段。
3. 风险日报与主体评估写入业务库并展示；权威材料上传仅用于深度研报。
4. 模型失败时，仍会用原始摘要入库，避免页面空白。

### 5. 改配置

- 增删订阅或检索主题：编辑 `config/rss_feeds.yaml`，再调用 `POST /api/v1/pipeline/rss-config/reload`，或重启。
- 日本企业证券代码：见 `app/services/scrapers/tdnet_collector.py`。
- 域名允许 / 屏蔽名单：见 `app/services/domain_rules.py`。

---

## 配置

| 变量 | 作用 | 默认 |
|------|------|------|
| `NEWS_WINDOW_HOURS` | 取最近多少小时的资讯 | `24` |
| `NETWORK_RETRY_ATTEMPTS` | 外网失败重试次数 | `3` |
| `PIPELINE_ASYNC_DEFAULT` | 是否默认异步采集 | `true` |
| `PIPELINE_LLM_TOP_K` | 送入模型前保留的条数上限 | `8` |
| `PIPELINE_LLM_CACHE_HOURS` | 模型结果缓存时长（小时） | `168` |
| `PIPELINE_MITA_QUERY_PAUSE_SECONDS` | 秘塔两次查询的间隔（秒） | `0.3` |
| `PIPELINE_MITA_MIN_ITEMS` | 主渠道够多少条就不再用秘塔；`0` 表示跟上面的条数上限一致 | `0` |
| `PIPELINE_MITA_FORCE_ON_PRIMARY_FAIL` | 主渠道全挂时是否强制用秘塔 | `true` |
| `RSS_CONFIG_PATH` | 订阅与检索配置文件 | `config/rss_feeds.yaml` |
| `DAILY_PIPELINE_CRON` | 定时采集时间 | `0 6 * * *` |

---

## 目录

```
app/
  main.py                  页面入口（端口 8000）
  config.py                业务模块与配置
  schemas.py               接口数据格式
  api/routes.py            接口
  database/                数据库
  services/                采集、分析、导出相关逻辑
  exporters/docx_report.py Word 导出
  templates/               页面模板
  static/                  样式与脚本
config/
  rss_feeds.yaml           订阅与检索配置
scripts/init_db.py         初始化数据库
```

---

## 数据库

| 表 | 存什么 |
|------|--------|
| `news_articles` | 日报资讯 |
| `target_entities` | 监控主体 |
| `entity_risks` | 主体风险事件 |
| `credit_updates` | 授信变更记录 |
| `industry_reports` | 深度研报 |
| `report_runs` | 各模块运行状态 |
| `pipeline_jobs` | 异步采集任务 |
| `pipeline_artifacts` | 采集中间结果 |
| `llm_response_cache` | 模型结果缓存 |
| `content_fingerprints` | 去重用指纹 |
| `search_logs` | 检索记录 |
| `module_data_sources` | 深度研报权威材料（文件/网址） |

---

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/news` | 资讯列表 |
| GET | `/api/v1/sources/rss` | 近 24 小时 RSS 列表 |
| GET | `/api/v1/data-sources` | 权威材料列表 |
| POST | `/api/v1/data-sources/upload` | 上传文件 |
| POST | `/api/v1/data-sources/url` | 添加网址 |
| DELETE | `/api/v1/data-sources/{id}` | 删除材料 |
| GET | `/api/v1/entities` | 主体列表 |
| GET | `/api/v1/entities/{id}/risks` | 主体风险事件 |
| GET | `/api/v1/entities/{id}/credit-updates` | 授信变更 |
| GET | `/api/v1/entities/{id}/export/docx` | 导出主体评估 |
| POST | `/api/v1/pipeline/run` | 开始采集（默认异步） |
| GET | `/api/v1/pipeline/jobs/{job_id}` | 查看任务状态 |
| GET | `/api/v1/pipeline/rss-config` | 查看订阅配置 |
| POST | `/api/v1/pipeline/rss-config/reload` | 重新加载订阅配置 |
| POST | `/api/v1/industry/analyze` | 生成深度研报 |
| GET | `/api/v1/industry/reports` | 研报列表 |
| GET | `/api/v1/industry/export/docx/{id}` | 导出研报 |
| GET | `/api/v1/export/docx` | 导出日报（可指定 `module_codes`） |
