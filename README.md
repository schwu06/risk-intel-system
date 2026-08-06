# 企业风险情报与日报系统

近 24 小时风险情报采集与展示：RSS / Google News / TDnet + 秘塔补充 + DeepSeek 结构化分析，SQLite 存储，FastAPI 仪表盘，Word 导出。

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
| 深度研报 | 报告草稿、专属数据源、版本派生、在线预览与 Word 导出 |
| 研报数据源 | 右侧栏上传 txt / xlsx / docx / pptx / pdf，或添加网页与 PDF 网址 |
| 异步采集 | collect → analyze → publish；失败保留上次结果；LLM 失败则降级入库 |
| RSS 配置 | `config/rss_feeds.yaml`，支持热加载 |

授信等级：正常 → 关注 → 预警 → 高风险。

### 深度研报工作流

1. 在左侧填写行业名称和可选企业名称，点击“创建报告草稿”。
2. 在右侧数据源栏上传文件或添加网址；数据源只属于当前草稿。
3. 点击“生成当前报告”。开始生成后材料被冻结，完成报告保持只读。
4. 如需补充材料，点击“补充数据并生成新版”。系统会复制出独立的数据源记录和文件，再进入新草稿。
5. 在新版草稿中添加材料并重新生成；旧报告、旧材料和旧生成结果不会变化。

PDF 支持原生文本提取和扫描页 OCR。网址以 `.pdf` 结尾、响应类型为 PDF 或文件头为 `%PDF-` 时，均按 PDF 处理。单文件上限为 25 MB，扫描 PDF 最多 OCR 30 页。

新上传的研报资料会登记原始内容哈希、解析文本哈希、来源类别、正文完整性、解析状态及页数/幻灯片数/工作表数等元数据，并生成带页码、幻灯片号、工作表行、DOCX 段落/表格行或网页段落定位的文本切片。旧数据不会在启动时自动重解析；缺少登记元数据或切片的旧来源会保留为兼容数据。

在 `legacy` 模式勾选“生成时补充网络搜索”后，系统会通过秘塔搜索最多获取 8 条结果，并在调用 DeepSeek 前自动写入当前报告的数据源组。每条结果会生成不超过 72 字的摘要标题，类型和详情中明确标记“补充网络搜索功能”。相同 URL 在当前版本及其派生版本中不会重复写入；搜索失败不会阻断已有材料的报告生成。该功能依赖 `MITA_API_KEY`；未配置或使用 `grounded` 模式时页面会禁用该选项。grounded 模式不会在正式生成请求中临时搜索，网络线索需先经过结构化解析、证据提取与门禁。

历史报告列表支持逐版本修改名称。自定义名称只用于报告管理，不会改变行业、企业、正文或数据源；创建新版时默认继承上一版名称。Word 导出文件名优先使用自定义名称。

#### 正式报告生成模式

已有部署默认使用 `legacy`，行为与升级前一致。将 `.env` 中的
`INDUSTRY_REPORT_GENERATION_MODE` 改为 `grounded` 并重启应用后，正式
`POST /api/v1/industry/reports/{report_id}/generate` 会依次执行就绪检查、
Evidence Packet 生成、`grounded-report-v1`、引用校验和最多一次定向修复。
grounded 模式不会把拼接全文交给报告 Prompt，也不会在失败后静默回退 legacy。

`GROUNDED_REPORT_REQUIRE_APPROVAL=true` 时，验证通过的候选进入
`awaiting_approval`，需调用候选晋升接口；设为 `false` 时会在晋升前再次复核
快照、Schema 和引用，然后自动写入正式报告。快速回退应显式把生成模式改回
`legacy` 并重启。`GROUNDED_REPORT_ALLOW_LEGACY_FALLBACK` 必须保持 `false`。

---

## 数据源

配置文件：[`config/rss_feeds.yaml`](config/rss_feeds.yaml)（改后可 `POST /api/v1/pipeline/rss-config/reload` 或重启）。

模块：`A` 主体评估 · `B` 中东日报 · `C` 日本重点大型企业 · `D` 每日宏观与市场情报。

### 采集通道

| 通道 | 说明 |
|------|------|
| 用户上传 | 深度研报右侧栏的报告专属文件 / 网址，生成时优先采用 |
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
| `MITA_API_BASE_URL` | 秘塔普通网页搜索 API 基础地址 | `https://metaso.cn/api/v1` |
| `INDUSTRY_REPORT_GENERATION_MODE` | 正式深度研报模式：`legacy` 或 `grounded`；修改后需重启 | `legacy` |
| `GROUNDED_REPORT_REQUIRE_APPROVAL` | grounded候选是否需要人工晋升 | `true` |
| `GROUNDED_REPORT_ALLOW_LEGACY_FALLBACK` | grounded失败自动回退；安全策略只允许关闭 | `false` |

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
| `industry_data_sources` | 按 `report_id` 隔离的研报专属数据源快照 |
| `industry_source_chunks` | 按来源保存的带原文逻辑位置文本切片 |
| `report_runs` | 模块运行状态 |
| `pipeline_jobs` | 异步采集任务 |
| `pipeline_artifacts` | 采集中间结果 |
| `llm_response_cache` | LLM 缓存 |
| `content_fingerprints` | 去重指纹 |
| `search_logs` | 检索日志 |
| `module_data_sources` | 侧栏权威数据源 |

`industry_reports.status` 的主要状态为 `draft`、`running`、`awaiting_approval`、`completed`、`failed`。已完成或等待审批报告的数据源不可修改；`source_manifest_json` 记录正式生成实际使用的来源摘要，不保存完整 Evidence Packet 或客户全文。

### 数据库升级注意事项

本次研报数据源结构从“按行业名称共享”改为“按报告 ID 独占”。旧结构无法准确恢复历史报告使用过的材料，因此应用初始化检测到旧版 `industry_data_sources` 时，会清空并重建深度研报相关数据。该升级不会删除风险日报或主体评估数据。

升级后请重新为报告添加数据源。研报上传文件存放于：

```text
data/uploads/industry_reports/{report_id}/
```

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
| POST | `/api/v1/industry/reports/drafts` | 创建深度研报草稿 |
| GET | `/api/v1/industry/reports` | 研报列表 |
| POST | `/api/v1/industry/reports/{id}/generate` | 冻结该报告数据源并生成报告 |
| GET | `/api/v1/industry/reports/{id}/grounded-readiness` | grounded正式生成前只读检查 |
| POST | `/api/v1/industry/reports/{id}/grounded-runs/{run_id}/promote` | 人工晋升validated候选，JSON body可传`promotion_note` |
| POST | `/api/v1/industry/reports/{id}/fork` | 复制材料并创建独立新版草稿 |
| GET | `/api/v1/industry/reports/{id}/data-sources` | 当前报告的数据源列表 |

grounded审批模式示例：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/industry/reports/1/grounded-readiness
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/industry/reports/1/generate
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"promotion_note":"复核通过"}' http://127.0.0.1:8000/api/v1/industry/reports/1/grounded-runs/2/promote
```
| POST | `/api/v1/industry/reports/{id}/data-sources/upload` | 向草稿上传文件 |
| POST | `/api/v1/industry/reports/{id}/data-sources/url` | 向草稿添加网址 |
| GET | `/api/v1/industry/reports/{id}/data-sources/{source_id}` | 查看报告数据源正文 |
| DELETE | `/api/v1/industry/reports/{id}/data-sources/{source_id}` | 删除草稿中的数据源 |
| GET | `/api/v1/industry/export/docx/{id}` | 导出研报 Word |
| GET | `/api/v1/export/docx` | 导出日报 Word（`module_codes=B,C,D`） |

---

## 升级与协作

拉取本次更新后，在项目虚拟环境中执行：

```powershell
pip install -r requirements.txt
python scripts\init_db.py
```

详细变更、破坏性升级说明和验证结果见 [`CHANGELOG.md`](CHANGELOG.md)。
