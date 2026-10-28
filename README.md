# Annotation（书注）

Annotation 是一个教材驱动的 AI 学习文档生成项目。

输入一本pdf教材，系统完成：

```text
教材 → 来源解析 → Learning Blueprint → 学习内容 → 基础审核 → 在线学习文档
```

最终页面面向零基础或初学者，重点是循序渐进的讲解、例题、公式、练习、和必要的风险/观点提示。

## 当前技术路线

- 前端：VitePress + Vue 3 + TypeScript + Vite
- 样式：Tailwind CSS、shadcn-vue 风格源码组件、编辑出版物视觉规范
- 后端：Python 3.12、FastAPI、Pydantic v2
- Agent 编排：LangGraph
- 教材解析：PyMuPDF + Annotation Hybrid 局部 OCR/公式候选
- 检索：SQLite FTS5 + LlamaIndex/Chroma 向量召回，保留页/块级 `source_ref`
- 内容契约：JSON Document IR
- 公式：KaTeX
- 测试：pytest + Vitest

## 本地运行

### 可选学科增强

`prompts/` 提供完整的通用流程；`prompt_layers/` 只提供可选学科规则和教学方法。
生成前可在页面选择“通用 · 不指定学科”或“数学”，默认通用。API 的 `POST /api/runs`
可传 `"prompt_profile": "mathematics"`，省略或传 null 则不加载学科包；
`GET /api/prompt-profiles` 列出可选包。Python 入口支持同名 `prompt_profile` 参数。
选择随运行和检查点保存，续跑沿用原选择。自定义包只需新增 Markdown 并注册 manifest，
详见 [学科包说明](src/annotation/prompt_layers/README.md)。

### 任务并发

讲解、可选练习、事实检查和可选组件保留阶段顺序，阶段内独立任务默认以 2 个 worker 并发。
`WORKFLOW_MAX_WORKERS` 可设为 1–8；1 恢复串行。上限按单次运行计算，多次运行会叠加。
结果始终按课程任务顺序装配。讲解批次发生技术失败后停止派发后续批次，
已开始任务完成并保留诊断，未执行任务记录为 skipped。SQLite 和向量客户端分别加锁，网络搜索可并发；讲解与练习共享同一教材检索服务。
并发减少等待，不承诺减少模型调用或费用；按服务限流选择上限。

### 长教材蓝图

真实模型路径先建立教材结构树，再按来源范围和输入预算生成局部 Blueprint，逐层协调章节顺序、
前置关系和显式合并；不增加独立知识清单。协调 Agent 可以搜索/读取知识单元及按偏移回读原文。
所有未显式合并的单元由程序完整保留，失败不会静默当成跳过。课程架构师也按预算分批生成任务。

`BLUEPRINT_INPUT_BUDGET_TOKENS` 默认 16000，输入与检索包统一按中英文混合字符估算 token，并预留 schema/输出空间；
`BLUEPRINT_MAX_ATTEMPTS` 控制每次局部生成/协调的重试，`BLUEPRINT_COORDINATION_MAX_STEPS` 限制工具回路，
`BLUEPRINT_MAX_CALLS` 默认 1000，限制本次分层蓝图的实际模型请求数（provider 内部传输重试另计）。
成功局部结果默认缓存于 `storage/cache/blueprint`，按内容、提示、模型/配置及预算隔离；
`BLUEPRINT_CACHE_ENABLED=false` 可关闭复用。改变模型行为参数时应同时更新 `MODEL_CONFIG_VERSION`。

结构树、分窗偏移、跳过理由、调用/工具审计及缓存命中保存在 `blueprint-artifact-v3`；API 的
`blueprint_loop` 返回分层摘要。短教材单窗口沿用 A-001；预加载蓝图不调用模型，显式 offline profile 使用明确标记的
六单元离线 fixture。首次扫描仍读取全文，不承诺首次总 token 降低；结构覆盖也不代表语义零遗漏。

后端：

```powershell
uv sync --extra dev --link-mode copy
uv run uvicorn annotation.main:app --reload --app-dir src
```

前端：

```powershell
cd web
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。后端 API 为 `http://127.0.0.1:8000`，交互文档为 `/docs`。

### 向量检索与效果评估

```powershell
uv sync --extra dev --extra rag --extra rag-local-embeddings --link-mode copy
```

在本地 `.env` 中设置 `RAG_ENABLED=true`。`RAG_EMBEDDING_PROVIDER=auto` 优先使用独立的
`RAG_EMBEDDING_API_KEY`：默认端点为硅基流动 `https://api.siliconflow.cn/v1`，模型为
`BAAI/bge-m3`。2026-10-21 官方价格页将该模型列为免费，实际可用性与限流以服务账号为准。
没有 embedding 密钥或初始化连接失败时，明确选择本地 `BAAI/bge-small-zh-v1.5`（CPU），首次需下载。
`local` 和 `api` 可强制选择；不会借用内容生成的模型密钥，也不会在查询中途切换模型。
本地模型路径、API 模型和请求参数见 [.env.example](.env.example)。Chroma 在 `storage/chroma` 持久化，
同一教材和编码配置复用已有向量；切换编码配置会使用独立索引。

用冻结的 25 页、1,206 个原始教材块及 48 道问题对比现有 FTS5、纯向量和交替去重混合检索：

```powershell
.venv/Scripts/python.exe scripts/evaluate_retrieval.py --embedding-provider local --output-dir results/retrieval/local
# 已缓存本地模型后可追加 --offline。
.venv/Scripts/python.exe scripts/evaluate_retrieval.py --embedding-provider api --api-model BAAI/bge-m3 --api-base-url https://api.siliconflow.cn/v1 --output-dir results/retrieval/siliconflow-api --persist-directory storage/chroma/retrieval-siliconflow-api --repeats 3
```

输出 `report.md`、`summary.json`、`queries.jsonl` 和独立进程的 `reuse-probe.json`。
支持 `--corpus`、`--questions`、`--methods`、`--persist-directory`、`--repeats`；报告记录实际模型版本及复现命令。
测量首次建索引时，为 `--persist-directory` 指定新的空目录；已有目录用于测量复用。
数据及标签在 [tests/fixtures/retrieval/math_ch1](tests/fixtures/retrieval/math_ch1/README.md)，
标签未经独立人工复核，适合作为开发基线。无答案误召回单独报告；首版未设置相似度拒答阈值。
事实核查仍维持现有 FTS5，学习者页面不增加检索控件。

2026-10-21 已完成真实 CPU 与硅基流动 API 评估、独立进程索引复用及 API 自动选择验证。
本机对比见 [检索结果对比](results/retrieval/comparison.md)，API 逐题报告见
[BGE-M3 评估](results/retrieval/siliconflow-api/report.md)。当前章节上 API 纯向量 Recall@5 为
30.79%，本地为 33.76%；API 没有在这份开发基线上整体胜出，无答案误召回仍存在。
这些运行产物保留在本机，不随源码提交。

### 文档库与本地持久化

页面首次打开只读取本地文档库，不会自动调用模型。教材和每次生成运行的关系
保存在 `storage/annotation.sqlite3`；完整的来源、蓝图、内容、Document IR、审核
报告和运行清单仍以 JSON artifact 保存在 `storage/artifacts/`。同一 PDF 按 SHA-256
去重，因此再次上传同一文件只会复用已有教材记录。

常用接口：

```text
GET  /api/library                 读取教材和已保存文档
POST /api/books                   上传 PDF（multipart 字段 file）
POST /api/runs                    对选中的 book_id 生成一次新版本
GET  /api/documents/{document_id} 读取当前已保存的 Document IR
GET  /api/runs/{run_id}            读取运行状态和 artifact 索引
```

`POST /api/runs` 是同步的、显式的生成动作；刷新页面、选择教材或打开已保存文档
都不会再次付费调用 DeepSeek。失败的运行仍保留在数据库中，便于排查和重试。

审核状态的含义如下：

- `at_risk`：自动检查发现需要人工核查的警告，但没有阻断性问题。文档仍可阅读，
  页面只显示中性的“建议核实”提示；它不等于内容已经被证明正确。
- `blocked`：发现来源缺失、结构/公式无效或其他阻断性问题。页面不呈现正文，
  只显示“暂不可用”和审核提示；原始 artifact、问题和失败记录仍保留在 API/存储中。

运行使用 LangGraph 原生 SQLite 检查点与节点中断，显式续跑继续使用原 `run_id`。
已完成节点无需重跑；中断或失败节点可能重做，已完成模型调用不承诺恰好执行一次。
检查点的 channel payload 按版本单独保存，未变化的来源块等大对象复用已有文件。
旧 pickle 快照不自动迁移；进程重启只标记 interrupted，续跑仍由用户明确触发。

当前代码以现行数据契约为准，不自动迁移旧字段、旧来源数据库或重建早期运行。旧 artifact 原文件保留供审计；过时数据需要重新生成。

已存文档若只有某节或某个节点损坏，页面在原位置标明不可用，其余章节继续可读；审核记录损坏会显示核查提示，不会伪装为审核通过。

默认离线运行：

```powershell
$env:MODEL_PROFILE = "offline"
uv run pytest -q
```

运行一次工作流：

```powershell
$library = Invoke-RestMethod http://127.0.0.1:8000/api/library
$body = @{ book_id = $library.books[0].book_id } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/runs -ContentType "application/json" -Body $body
```

### 使用 DeepSeek 做真实试跑

DeepSeek 通过 OpenAI-compatible HTTPS API 接入，项目不要求 SOCKS 代理。运行前
只需在当前 PowerShell 会话设置 provider、模型和 API key；`MODEL_BASE_URL` 可省略，
工厂会使用 `https://api.deepseek.com/v1`。

```powershell
$env:MODEL_PROFILE = "live"
$env:MODEL_PROVIDER = "deepseek"
$env:MODEL_NAME = "deepseek-chat"
$env:MODEL_API_KEY = "<your-deepseek-api-key>"
$env:MODEL_THINKING = "disabled"
$library = Invoke-RestMethod http://127.0.0.1:8000/api/library
$body = @{ book_id = $library.books[0].book_id; run_id = "deepseek-smoke-001" } | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/runs -ContentType "application/json" -Body $body
```

如果本机继承了不可用的 SOCKS 环境代理，可在这次试跑中清除代理变量，或按网络环境
配置可用的代理；这不是 Annotation 或 DeepSeek provider 的必需依赖。

工作流返回的 blueprint、content artifacts、
review report 和模型调用记录都保存在 `storage/`。

当前 P0 题目闭环按知识单元生成独立 `QuizArtifact`：题量由 Agent 根据学习目标和
ContextPack 证据自主决定，可以为 0；题目带唯一答案、解析、目标和教材来源。覆盖矩阵
记录 Agent 选择的题量，失败响应保存在 `content.json`、独立的 `quiz-v1.json` 以及
SQLite artifact 索引中。题目通过结构、答案和来源检查后才进入 `QuizNode`；失败题目保留诊断并从文档中剔除，不阻塞通过正文检查的文档；显式零题不调用题目模型。`at_risk` 仅表示可读的 `accepted` 预览，只有审核 `passed`
才会投影为 `published`。

## 文档入口

- [单章复评（2026-10-22）](docs/p0-reevaluation-2026-10-22.md)：已有真实运行的结构核对、内容初评、运行状态冲突与当前 PDF 分窗预检；未新增付费调用。
- [复评问题修复（2026-10-22）](docs/p0-fixes-2026-10-22.md)：状态优先级、零命中证据占位、事实补查、覆盖检查及数学修订候选；包含续跑快照纠正和历史数据库备份记录。
- [TODO.md](TODO.md)：下一窗口只读这里，按清单推进。
- [tasks/backlog.md](tasks/backlog.md)：当前任务边界和完成定义。
- [docs/requirements.md](docs/requirements.md)：当前需求与 P0 范围。
- [docs/architecture.md](docs/architecture.md)：当前数据流和模块边界。
- [docs/decisions.md](docs/decisions.md)：仍然有效的架构决策。
- [MOCK_STRATEGY.md](MOCK_STRATEGY.md)：mock、离线 fixture、测试替身与真实 provider 的边界及迁移顺序。
- [documents/wed_design.md](documents/wed_design.md)：学习者页面的视觉与交互规范。
- [documents/ai_textbook_project_docs/README.md](documents/ai_textbook_project_docs/README.md)：教材 Agent 设计索引。
- [docs/archive/2026-09-11/](docs/archive/2026-09-11/)：已完成工作的原始记录、旧计划和详细评估。

`books/`、`storage/` 和模型调用日志只用于本地开发，不提交教材、密钥或运行产物。

模型输入估算默认使用 `TOKEN_ESTIMATE_SCALE=1.7`，是安全预算估算而非精确 tokenizer。可运行 `.venv/Scripts/python.exe scripts/calibrate_token_estimation.py storage/model-calls.jsonl`，按 provider/model 查看实际 `prompt_tokens` 相对基础估算的分位数，再显式调整该比例；脚本不输出教材提示词，也不自动改配置。

新的审计 artifact 将 512 字符以上的提示词替换为 `PromptReference`：`storage=gzip-sha256-v1`、`sha256`、相对 `path`、`characters`。原文按哈希去重压缩在同目录 `prompts/`；复制 artifact 时需一并复制该目录。`annotation.prompt_archive.read_artifact_json(Path(...))` 可校验并还原提示词，API 读取保持完整提示词，旧的内联字符串仍可读取；运行状态/检查点不受这项存储优化影响。
# Fact-check retrieval budget

Fact-check retrieval executes all distinct nonempty queries in each decision (up to three textbook queries and two web queries), merging duplicate evidence. `FactCheckPolicy.web_query_limit` caps total web search calls per knowledge unit across initial retrieval, supplemental searches and correction rounds. Failed calls count toward this budget; zero disables web searches. Exhaustion retains existing evidence and records `web:budget_exhausted` in the trace.
