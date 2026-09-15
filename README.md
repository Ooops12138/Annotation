# Annotation（书注）

Annotation 是一个教材驱动的 AI 学习文档生成项目。

输入一本教材的一章，系统完成：

```text
教材 → 来源解析 → Learning Blueprint → 学习内容 → 基础审核 → 在线学习文档
```

最终页面面向零基础或初学者，重点是循序渐进的讲解、例题、公式、练习、学习路径、教材出处和必要的中性风险/观点提示。项目不是通用教育平台，也不是静态文档导出器。

当前项目有两个并行目标：P0 负责验证一个章节的教材到学习文档闭环；A-* Agent 工程线
用于练习可观测的状态、反馈和条件路由，不改变 P0 发布门槛。现阶段主链路仍是线性工作流，
第一个条件回边由 A-001 Blueprint 修订 loop 引入：

```text
固定工作流：教材 → Blueprint → 内容 → Document IR → 审核 → 发布

A-001 子图：generate → check → route
                         ├─ accept
                         ├─ revise → generate
                         ├─ block
                         └─ fail
```

Loop 的完整 prompt、原始模型输出、检查反馈、路由和停止原因只保存在可审计 artifact；
学习者页面不显示 attempt、模型信息或内部 ID。设计输入见
[Agent 策划书](<documents/ai_textbook_learning_project_proposal (1).md>)，其中尚未实现的
能力以 proposal 形式保留，不代表当前已交付。

## 当前技术路线

- 前端：VitePress + Vue 3 + TypeScript + Vite
- 样式：Tailwind CSS、shadcn-vue 风格源码组件、编辑出版物视觉规范
- 后端：Python 3.12、FastAPI、Pydantic v2
- Agent 编排：LangGraph
- 教材解析：PyMuPDF + Annotation Hybrid 局部 OCR/公式候选
- 检索：SQLite FTS5 + 页/块级 `source_ref`
- 内容契约：JSON Document IR
- 公式：KaTeX
- 测试：pytest + Vitest

VitePress 是完整项目的前端外壳。当前只优先完成一个学习者页面；审核报告、运行状态和原始来源信息通过 API、日志或 Console 调试，不另外建设工作台。

## 本地运行

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

现有的 DeepSeek 试跑可以离线导入（不会发起新请求）：

```powershell
$env:PYTHONPATH = "src"
uv run python scripts/import_legacy_run.py deepseek-full-context-live-001
```

该命令只需在尚未导入该运行的数据库上执行一次；导入后的文档可直接从文档库打开。

默认离线运行：

```powershell
$env:MODEL_PROVIDER = "mock"
uv run pytest -q
```

运行一次工作流：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/workflow/run
```

### 使用 DeepSeek 做真实试跑

DeepSeek 通过 OpenAI-compatible HTTPS API 接入，项目不要求 SOCKS 代理。运行前
只需在当前 PowerShell 会话设置 provider、模型和 API key；`MODEL_BASE_URL` 可省略，
工厂会使用 `https://api.deepseek.com/v1`。

```powershell
$env:MODEL_PROVIDER = "deepseek"
$env:MODEL_NAME = "deepseek-chat"
$env:MODEL_API_KEY = "<your-deepseek-api-key>"
$env:MODEL_THINKING = "disabled"
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/workflow/run?run_id=deepseek-smoke-001"
```

如果本机继承了不可用的 SOCKS 环境代理，可在这次试跑中清除代理变量，或按网络环境
配置可用的代理；这不是 Annotation 或 DeepSeek provider 的必需依赖。

蓝图阶段现在把 PDF 解析出的全部非空 `SourceBlock` 传给模型，而不是只传前 12 个块。
对应实现位置是 [`src/annotation/workflow/graph.py`](src/annotation/workflow/graph.py) 的
`_source_context` 和 `load_or_create_blueprint`。内容生成仍按每个知识单元使用受限
`ContextPack`，以控制单次请求大小；工作流返回的 blueprint、content artifacts、
review report 和模型调用记录都保存在 `storage/`。该命令是一次真实集成试跑，仍需
根据 [docs/evaluation.md](docs/evaluation.md) 做人工抽样，不能仅凭 `published` 状态
认定教材内容已经完成整章质量验收。

当前 P0 题目闭环按知识单元生成独立 `QuizArtifact`：题量由 Agent 根据学习目标和
ContextPack 证据自主决定，可以为 0；题目带唯一答案、解析、目标和教材来源。覆盖矩阵
记录 Agent 选择的题量，失败响应保存在 `content.json`、独立的 `quiz-v1.json` 以及
SQLite artifact 索引中。题目通过结构、答案和来源检查后才进入 `QuizNode`；题目审核
阻塞不会生成可发布文档版本。`at_risk` 仅表示可读的 `accepted` 预览，只有审核 `passed`
才会投影为 `published`。

## 文档入口

- [TODO.md](TODO.md)：下一窗口只读这里，按清单推进。
- [tasks/backlog.md](tasks/backlog.md)：当前任务边界和完成定义。
- [docs/requirements.md](docs/requirements.md)：当前需求与 P0 范围。
- [docs/architecture.md](docs/architecture.md)：当前数据流和模块边界。
- [docs/decisions.md](docs/decisions.md)：仍然有效的架构决策。
- [documents/wed_design.md](documents/wed_design.md)：学习者页面的视觉与交互规范。
- [documents/ai_textbook_project_docs/README.md](documents/ai_textbook_project_docs/README.md)：教材 Agent 设计索引。
- [docs/archive/2026-09-11/](docs/archive/2026-09-11/)：已完成工作的原始记录、旧计划和详细评估。

`books/`、`storage/` 和模型调用日志只用于本地开发，不提交教材、密钥或运行产物。
