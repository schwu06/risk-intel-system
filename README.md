# 企业风险情报与日报系统

汇总近 24 小时重要资讯，整理后入库展示，并可导出 Word。

各页面功能对应的数据源与接入方式，见 **[DATA_SOURCES.md](DATA_SOURCES.md)**。

---

## 启动

两台开发电脑和 Render 共用一份 `requirements.txt`。`.venv` 不进 Git，每台电脑自己建。完整多端说明见 **[MULTI_DEV.md](MULTI_DEV.md)**。

### 中文电脑

```powershell
cd risk-intel-system
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python scripts\init_db.py
python scripts\run_dev.py
```

### 日文电脑

不要拷贝中文电脑的 `.venv`。本机新建后用 `python.exe -m pip`，避免终端代码页 932 下直接打 `uvicorn` 失败。

```powershell
cd risk-intel-system
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python.exe scripts\init_db.py
.\dev.ps1
```

Cursor 解释器选 `.venv (3.12.10) .\.venv\Scripts\python.exe`。

`run_dev.py` 等价于：

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0 --port 8000` 表示监听本机所有网卡的 8000 端口。启动后打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。开发时优先用 `run_dev.py`：只监视 `app/` 和 `config/`，并等文件写完再重载，减少半成品代码把服务打挂。

在 `.env` 中填写 `MITA_API_KEY`、`DEEPSEEK_API_KEY`；主体评估与行业分析还需 `GEMINI_API_KEY`。同一 Gemini / DeepSeek 密钥可按任务填写不同模型名，见「大模型对接」。常用变量见下方「配置（常用）」；完整列表见 `[.env.example](.env.example)`。

---



## 页面


| 页面   | 地址                                                                                 |
| ---- | ---------------------------------------------------------------------------------- |
| 新闻汇总 | [http://127.0.0.1:8000/daily-news](http://127.0.0.1:8000/daily-news)               |
| 主体评估 | [http://127.0.0.1:8000/entity-assessment](http://127.0.0.1:8000/entity-assessment) |
| 行业分析 | [http://127.0.0.1:8000/deep-reports](http://127.0.0.1:8000/deep-reports)           |
| 国际评级 | [http://127.0.0.1:8000/intl-ratings](http://127.0.0.1:8000/intl-ratings)           |


首页 `/` 进入新闻汇总。

---



## 功能概要


| 页面   | 做什么                                                      |
| ---- | -------------------------------------------------------- |
| 新闻汇总 | 按模块 B/C/D 汇总近 24 小时资讯，可导出 Word                           |
| 主体评估 | 按主体汇总近三个月可追溯公开信息与舆情预警灯号（模块 A），左侧按分类索引浏览，可导出 Word；采集一次覆盖全部监控主体 |
| 行业分析 | 报告草稿、专属数据源、版本派生、在线预览与 Word 导出                            |
| 国际评级 | 发行体评级监测（`GET/POST /api/v1/intl-ratings` + 流水线），可导出 Excel |


主体评估灯号：正常 → 关注 → 预警 → 高风险。灯号只用于公开信息复核触发，不是内部信用评级或授信审批结论。

业务模块：`A` 主体评估 · `B` 中东 · `C` 日本企业 · `D` 宏观市场。

采集失败时尽量保留上次结果；主体评估默认不会因空结果生成演示样本。仅在显式设置 `ENTITY_DEMO_MODE=true` 或执行 `python scripts/init_db.py --demo` 时写入演示数据，且演示数据不参与灯号计算和正式导出。

### 行业分析工作流

每个行业分类使用独立 SQLite（`data/industry_dbs/{sector}.sqlite3`）。可在左侧「行业索引」中切换、重命名或删除；点击加号新建行业（浏览器对话框输入名称）。行业清单保存在本地 `data/industry_sectors.json`。

1. 选择行业后，在「创建报告」中确认或修改行业名称，点击“创建报告草稿”。
2. 在右侧数据源栏上传文件或添加网址；数据源只属于当前草稿。
3. 点击“生成当前报告”。开始生成后材料被冻结，完成报告保持只读。
4. 如需补充材料，在右侧点击“添加数据源”。系统会复制出独立的新版草稿与数据源，补充后再点击“开始生成”。
5. 在新版草稿中添加材料并重新生成；旧报告、旧材料和旧生成结果不会变化。
6. 「历史创建记录」跨行业显示；点击其他行业记录会进入对应工作区，当前行业未提交的草稿仍保留在原行业库中。

PDF 支持原生文本提取和扫描页 OCR。网址以 `.pdf` 结尾、响应类型为 PDF 或文件头为 `%PDF-` 时，均按 PDF 处理。单文件上限为 25 MB，扫描 PDF 最多 OCR 30 页。

在 `legacy` 模式勾选“生成时补充网络搜索”后，系统会通过秘塔搜索最多获取 8 条结果，并在调用 Gemini 前自动写入当前报告的数据源组。该功能依赖 `MITA_API_KEY`；未配置或使用 `grounded` 模式时页面会禁用该选项。

#### 正式报告生成模式

已有部署默认使用 `legacy`。将 `.env` 中的 `INDUSTRY_REPORT_GENERATION_MODE` 改为 `grounded` 并重启后，正式生成会依次执行就绪检查、Evidence Packet 生成、引用校验和最多一次定向修复。`GROUNDED_REPORT_ALLOW_LEGACY_FALLBACK` 必须保持 `false`。

---



## 数据源

通用 RSS 配置：`[config/rss_feeds.yaml](config/rss_feeds.yaml)`（改后可 `POST /api/v1/pipeline/rss-config/reload` 或重启）。主体清单及其专属来源配置：`[config/entity_targets.yaml](config/entity_targets.yaml)`（改后重启）。主体评估页只展示清单和信源，不提供增删改。

### 采集通道


| 通道                | 说明                                               |
| ----------------- | ------------------------------------------------ |
| 用户上传              | 行业分析右侧栏的报告专属文件 / 网址                              |
| Google News RSS   | YAML `queries`；默认中国版，模块 C 为日本版 `ja/JP`           |
| 直连 RSS            | YAML `feeds` 固定 Feed                             |
| TDnet             | 模块 C：適時開示列表 + PDF 正文                             |
| 主体专属来源            | 模块 A：主体官网、监管、交易所披露、行业背景和跨媒体检索                    |
| 秘塔搜索              | 主源候选不足或主源故障时补充；余额不足时改用 Gemini 联网检索，再不行用 DeepSeek |
| DuckDuckGo 新闻补缺     | 仅模块 C 与日本主体：秘塔空结果或「未找到相关数据」后再搜；不替代行业分析     |
| Gemini / DeepSeek | 同一密钥按任务选用不同模型，见下方「大模型对接」                         |


完整查询词见 YAML。域名规则见 `app/services/domain_rules.py`。

---



## 大模型对接

同一个供应商密钥可以调用该供应商下的不同模型，不必为每个功能再申请一套 API。改 `.env` 中的模型名后重启即可。

当前实际使用 3 个密钥：`MITA_API_KEY`（搜索）、`DEEPSEEK_API_KEY`（整理/翻译）、`GEMINI_API_KEY`（主体评估与行业分析）。不需要再接 Claude 或 OpenAI。


| 功能               | 供应商                  | 环境变量                                    | 当前模型                                       | 说明             |
| ---------------- | -------------------- | --------------------------------------- | ------------------------------------------ | -------------- |
| 补缺搜索             | 秘塔                   | `MITA_API_KEY`                          | 秘塔 Search                                  | 真搜索，不换大模型      |
| 秘塔余额不足时的搜索替代     | Gemini，失败再用 DeepSeek | `GEMINI_FLASH_MODEL` / `DEEPSEEK_MODEL` | `gemini-3-flash-preview` / `deepseek-chat` | 优先 Gemini 联网检索 |
| 新闻汇总 B/C/D 结构化整理 | DeepSeek             | `DEEPSEEK_MODEL`                        | `deepseek-chat`                            | 调用次数多，用便宜快模型   |
| 页面日文/英文翻译        | DeepSeek             | `DEEPSEEK_MODEL`                        | `deepseek-chat`                            | 同上             |
| 国际评级主体对齐与无评级说明   | DeepSeek             | `DEEPSEEK_MODEL`                        | `deepseek-chat`                            | 同上             |
| 主体评估最新消息摘要       | Gemini               | `GEMINI_FAST_MODEL`                     | `gemini-3.1-flash-lite`                    | 要快，超时 8 秒      |
| 财务 PDF 抽数、行业证据抽取 | Gemini               | `GEMINI_FLASH_MODEL`                    | `gemini-3-flash-preview`                   | 中等能力           |
| 主体评估信用信号、行业分析成稿  | Gemini               | `GEMINI_STRONG_MODEL`                   | `gemini-3-flash-preview`                   | 建议改为 Pro，见下方缺口 |


`GEMINI_FAST_MODEL`、`GEMINI_FLASH_MODEL`、`GEMINI_STRONG_MODEL` 为空时，回退到 `GEMINI_MODEL`。

### 缺少的能力

- **Gemini Pro 额度**：同一 `GEMINI_API_KEY` 已能看到 `gemini-3.1-pro-preview`，但当前调用返回 429（额度不足）。开通 Pro 额度后，把 `GEMINI_STRONG_MODEL` 改为 `gemini-3.1-pro-preview` 并重启，主体评估与行业分析会改用 Pro。
- **不必新增密钥**：DeepSeek 同一密钥已可调用 `deepseek-chat`、`deepseek-v4-flash`、`deepseek-v4-pro`。新闻汇总仍用 `deepseek-chat`。
- **可选非大模型**：国际评级的 `OPENFIGI_API_KEY` 未填时，主体对齐会弱一些，与大模型无关。

---



## 配置（常用）


| 变量                                      | 作用                                    | 默认                           |
| --------------------------------------- | ------------------------------------- | ---------------------------- |
| `DEEPSEEK_MODEL`                        | 新闻整理、翻译、国际评级所用 DeepSeek 模型            | `deepseek-chat`              |
| `GEMINI_MODEL`                          | Gemini 回退模型（分任务变量为空时使用）               | `gemini-3.1-flash-lite`      |
| `GEMINI_FAST_MODEL`                     | 主体评估最新消息摘要                            | `gemini-3.1-flash-lite`      |
| `GEMINI_FLASH_MODEL`                    | 财务抽数、证据抽取、搜索替代                        | `gemini-3-flash-preview`     |
| `GEMINI_STRONG_MODEL`                   | 主体评估结构化分析、行业分析成稿                      | `gemini-3-flash-preview`     |
| `PIPELINE_ASYNC_DEFAULT`                | 是否默认异步采集                              | `true`                       |
| `PIPELINE_RSS_FETCH_WORKERS`            | 单模块内 RSS/Google 并发数                   | `6`                          |
| `PIPELINE_CHANNEL_PARALLEL`             | 同模块主源通道（RSS/直连/新浪/TDnet）是否并行          | `true`                       |
| `PIPELINE_LLM_CONCURRENCY`              | 同一模块 LLM 分批并发数                        | `3`                          |
| `PIPELINE_MODULE_PARALLEL`              | 界面一 B/C/D 是否并行（WAL 下可用；侧栏变卡改 `false`） | `true`                       |
| `RSS_CONFIG_PATH`                       | 订阅与检索配置                               | `config/rss_feeds.yaml`      |
| `ENTITY_TARGETS_CONFIG_PATH`            | 主体清单与专属信源目录                           | `config/entity_targets.yaml` |
| `ENTITY_DEMO_MODE`                      | 是否显式启用主体演示数据                          | `false`                      |
| `ENTITY_WARNING_LOOKBACK_DAYS`          | 舆情预警、最新消息与公开信息事件观察期（天）      | `90`                         |
| `DAILY_PIPELINE_CRON`                   | 定时采集（东京时区；进程需常开）                      | `0 6 * * *`                  |
| `INDUSTRY_REPORT_GENERATION_MODE`       | 行业分析模式：`legacy` 或 `grounded`          | `legacy`                     |
| `GROUNDED_REPORT_REQUIRE_APPROVAL`      | grounded 候选是否需要人工晋升                   | `true`                       |
| `GROUNDED_REPORT_ALLOW_LEGACY_FALLBACK` | grounded 失败自动回退（须保持关闭）                | `false`                      |
| `SEARCH_DDG_FALLBACK_ENABLED`           | 秘塔空结果后是否对模块 C / 日本主体用 DuckDuckGo     | `true`                       |
| `SEARCH_DDG_REGION`                     | DuckDuckGo 新闻区域                         | `jp-jp`                      |


完整环境变量见 `[.env.example](.env.example)`。

---



## 目录

```
app/           页面、接口、采集与分析
config/        rss_feeds.yaml、entity_targets.yaml、direct_sites.yaml、intl_ratings.yaml
intl_ratings/  界面四：发行体评级监测流水线（CLI）
data/intl_ratings/  发行体清单 input / 报表 output
scripts/       init_db.py 等
DATA_SOURCES.md  各页功能与数据源说明
```

国际评级流水线：将清单放入 `data/intl_ratings/input/`，执行 `python -m intl_ratings.main`。详见 `intl_ratings/README.md`。

---



## 数据库（主要表）


| 表                                                     | 存什么                      |
| ----------------------------------------------------- | ------------------------ |
| `news_articles`                                       | 日报资讯                     |
| `target_entities` / `entity_risks` / `credit_updates` | 监控主体、可追溯公开信息事件与预警灯号变化    |
| `industry_reports`                                    | 行业分析                     |
| `industry_data_sources`                               | 按 `report_id` 隔离的研报专属数据源 |
| `module_data_sources`                                 | 侧栏权威数据源                  |
| `pipeline_jobs` / `report_runs`                       | 采集任务与运行状态                |


升级后研报上传文件存放于 `data/uploads/industry_reports/{report_id}/`。

---



## API（常用）


| 方法     | 路径                                       | 说明              |
| ------ | ---------------------------------------- | --------------- |
| GET    | `/api/v1/health`                         | 健康检查            |
| GET    | `/api/v1/news`                           | 资讯列表            |
| POST   | `/api/v1/pipeline/run`                   | 开始采集            |
| GET    | `/api/v1/pipeline/jobs/{job_id}`         | 任务状态            |
| POST   | `/api/v1/pipeline/rss-config/reload`     | 重载 RSS 配置       |
| POST   | `/api/v1/industry/sectors`               | 新增行业分类（创建独立库）   |
| PATCH  | `/api/v1/industry/sectors/{key}`         | 重命名行业           |
| DELETE | `/api/v1/industry/sectors/{key}`         | 删除行业及其数据库       |
| POST   | `/api/v1/industry/reports/drafts`        | 创建行业分析草稿        |
| POST   | `/api/v1/industry/reports/{id}/generate` | 生成报告            |
| GET    | `/api/v1/industry/export/docx/{id}`      | 导出研报 Word       |
| GET    | `/api/v1/export/docx`                    | 导出日报            |
| GET    | `/api/v1/entities/{id}/export/docx`      | 导出主体评估          |
| GET    | `/api/v1/entities/{id}/source-catalog`   | 查看主体配置的信息源目录    |
| GET    | `/api/v1/entities/{id}/risks`            | 查看主体事件；默认排除演示数据 |
| GET    | `/api/v1/intl-ratings`                   | 国际评级快照          |


本次主体评估完善记录见 `[changelog_20260807100746.zh.md](changelog/changelog_20260807100746.zh.md)`。

---



## 升级与协作

多端流程见 **[MULTI_DEV.md](MULTI_DEV.md)**。拉取更新后安装依赖（不要同步另一台的 `.venv`）：

中文电脑：

```powershell
pip install -r requirements.txt
python scripts\init_db.py
```

日文电脑：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\init_db.py
```

