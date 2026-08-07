# 企业风险情报与日报系统

汇总近 24 小时重要资讯，整理后入库展示，并可导出 Word。

各页面功能对应的数据源与接入方式，见 **[DATA_SOURCES.md](DATA_SOURCES.md)**。

---

## 启动

```powershell
cd risk-intel-system
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python scripts\init_db.py
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

在 `.env` 中填写 `MITA_API_KEY`、`DEEPSEEK_API_KEY`；主体评估的结构化分析还需 `GEMINI_API_KEY`。

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
| 主体评估 | 按主体汇总可追溯公开信息与舆情预警灯号（模块 A），可导出 Word |
| 深度研报 | 报告草稿、专属数据源、版本派生、在线预览与 Word 导出 |
| 国际评级 | 发行体评级监测（`GET/POST /api/v1/intl-ratings` + 流水线），可导出 Excel |

主体评估灯号：正常 → 关注 → 预警 → 高风险。灯号只用于公开信息复核触发，不是内部信用评级或授信审批结论。

业务模块：`A` 主体评估 · `B` 中东 · `C` 日本企业 · `D` 宏观市场。

采集失败时尽量保留上次结果；主体评估默认不会因空结果生成演示样本。仅在显式设置 `ENTITY_DEMO_MODE=true` 或执行 `python scripts/init_db.py --demo` 时写入演示数据，且演示数据不参与灯号计算和正式导出。

### 深度研报工作流

1. 在左侧填写行业名称和可选企业名称，点击“创建报告草稿”。
2. 在右侧数据源栏上传文件或添加网址；数据源只属于当前草稿。
3. 点击“生成当前报告”。开始生成后材料被冻结，完成报告保持只读。
4. 如需补充材料，点击“补充数据并生成新版”。系统会复制出独立的数据源记录和文件，再进入新草稿。
5. 在新版草稿中添加材料并重新生成；旧报告、旧材料和旧生成结果不会变化。

PDF 支持原生文本提取和扫描页 OCR。网址以 `.pdf` 结尾、响应类型为 PDF 或文件头为 `%PDF-` 时，均按 PDF 处理。单文件上限为 25 MB，扫描 PDF 最多 OCR 30 页。

在 `legacy` 模式勾选“生成时补充网络搜索”后，系统会通过秘塔搜索最多获取 8 条结果，并在调用 DeepSeek 前自动写入当前报告的数据源组。该功能依赖 `MITA_API_KEY`；未配置或使用 `grounded` 模式时页面会禁用该选项。

#### 正式报告生成模式

已有部署默认使用 `legacy`。将 `.env` 中的 `INDUSTRY_REPORT_GENERATION_MODE` 改为 `grounded` 并重启后，正式生成会依次执行就绪检查、Evidence Packet 生成、引用校验和最多一次定向修复。`GROUNDED_REPORT_ALLOW_LEGACY_FALLBACK` 必须保持 `false`。

---

## 数据源

通用 RSS 配置：[`config/rss_feeds.yaml`](config/rss_feeds.yaml)（改后可 `POST /api/v1/pipeline/rss-config/reload` 或重启）。主体清单及其专属来源配置：[`config/entity_targets.yaml`](config/entity_targets.yaml)（改后重启）。

### 采集通道

| 通道 | 说明 |
|------|------|
| 用户上传 | 深度研报右侧栏的报告专属文件 / 网址 |
| Google News RSS | YAML `queries`；默认中国版，模块 C 为日本版 `ja/JP` |
| 直连 RSS | YAML `feeds` 固定 Feed |
| TDnet | 模块 C：適時開示列表 + PDF 正文 |
| 主体专属来源 | 模块 A：主体官网、监管、交易所披露、行业背景和跨媒体检索 |
| 秘塔搜索 | 主源候选不足或主源故障时补充 |
| Gemini / DeepSeek | 主体评估使用 Gemini；其它资讯模块使用 DeepSeek 整理字段 |

完整查询词见 YAML。域名规则见 `app/services/domain_rules.py`。

---

## 配置（常用）

| 变量 | 作用 | 默认 |
|------|------|------|
| `NEWS_WINDOW_HOURS` | 资讯时间窗口（小时） | `24` |
| `PIPELINE_ASYNC_DEFAULT` | 是否默认异步采集 | `true` |
| `RSS_CONFIG_PATH` | 订阅与检索配置 | `config/rss_feeds.yaml` |
| `ENTITY_TARGETS_CONFIG_PATH` | 主体清单与专属信源目录 | `config/entity_targets.yaml` |
| `ENTITY_DEMO_MODE` | 是否显式启用主体演示数据 | `false` |
| `ENTITY_WARNING_LOOKBACK_DAYS` | 舆情预警信号观察期（天） | `90` |
| `DAILY_PIPELINE_CRON` | 定时采集 | `0 6 * * *` |
| `INDUSTRY_REPORT_GENERATION_MODE` | 深度研报模式：`legacy` 或 `grounded` | `legacy` |
| `GROUNDED_REPORT_REQUIRE_APPROVAL` | grounded 候选是否需要人工晋升 | `true` |
| `GROUNDED_REPORT_ALLOW_LEGACY_FALLBACK` | grounded 失败自动回退（须保持关闭） | `false` |

完整环境变量见 [`.env.example`](.env.example)。

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

| 表 | 存什么 |
|------|--------|
| `news_articles` | 日报资讯 |
| `target_entities` / `entity_risks` / `credit_updates` | 监控主体、可追溯公开信息事件与预警灯号变化 |
| `industry_reports` | 深度研报 |
| `industry_data_sources` | 按 `report_id` 隔离的研报专属数据源 |
| `module_data_sources` | 侧栏权威数据源 |
| `pipeline_jobs` / `report_runs` | 采集任务与运行状态 |

升级后研报上传文件存放于 `data/uploads/industry_reports/{report_id}/`。

---

## API（常用）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/news` | 资讯列表 |
| POST | `/api/v1/pipeline/run` | 开始采集 |
| GET | `/api/v1/pipeline/jobs/{job_id}` | 任务状态 |
| POST | `/api/v1/pipeline/rss-config/reload` | 重载 RSS 配置 |
| POST | `/api/v1/industry/reports/drafts` | 创建深度研报草稿 |
| POST | `/api/v1/industry/reports/{id}/generate` | 生成报告 |
| GET | `/api/v1/industry/export/docx/{id}` | 导出研报 Word |
| GET | `/api/v1/export/docx` | 导出日报 |
| GET | `/api/v1/entities/{id}/export/docx` | 导出主体评估 |
| GET | `/api/v1/entities/{id}/source-catalog` | 查看主体配置的信息源目录 |
| GET | `/api/v1/entities/{id}/risks` | 查看主体事件；默认排除演示数据 |
| GET | `/api/v1/intl-ratings` | 国际评级快照 |

本次主体评估完善记录见 [`changelog_20260807100746.zh.md`](changelog/changelog_20260807100746.zh.md)。

---

## 升级与协作

拉取更新后执行：

```powershell
pip install -r requirements.txt
python scripts\init_db.py
```
