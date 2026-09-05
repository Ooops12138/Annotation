# Annotation 第一版 Demo TODO

这是下一次开发窗口的接续入口。当前目标不是一次完成完整 P0，而是先建立一条可运行、可验证的最小垂直链路。

## 当前目标

跑通：

```text
PDF / fixture
  → SourceBlock
  → Learning Blueprint
  → JSON Document IR
  → Vue Document Renderer
  → 在线学习文档
```

第一版 Demo 必须能够展示：

- 一个章节或示例文档；
- 章节/知识单元结构；
- 至少一段讲解；
- 至少一个公式或结构化内容块；
- 至少一个来源引用；
- 至少一个测验或练习；
- 基础审核状态或风险标注。

## 已确定技术路线

- 前端：Vue 3 + TypeScript + Vite；
- UI：`shadcn-vue` + Tailwind CSS + `lucide-vue-next`；
- 前端状态：Pinia；
- 后端：Python 3.12 + FastAPI；
- 数据模型：Pydantic v2；
- Agent 编排：LangGraph；
- 模型接入：`ModelProvider`；支持 OpenAI API、Ollama 和 vLLM；
- 输入：P0 先支持文本型 PDF；
- PDF 解析：PyMuPDF；
- 检索：SQLite FTS5 + `source_refs` 精确定位；
- 持久化：SQLite + SQLAlchemy 2 + Alembic；
- 内容规范输出：JSON Document IR；
- 公式：KaTeX；
- Python 包管理：uv；
- 前端包管理：npm；
- P0 不使用 VitePress 作为主前端，不引入向量数据库、Celery 或 Redis。

详细说明见：

- [`docs/technology-selection.md`](docs/technology-selection.md)
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/requirements.md`](docs/requirements.md)
- [`docs/decisions.md`](docs/decisions.md)
- [`tasks/backlog.md`](tasks/backlog.md)

## 下一窗口第一步

### T-000：建立技术骨架与本地开发约定

创建最小项目骨架：

```text
src/annotation/
├── api/
├── domain/
├── workflow/
├── providers/
├── ingestion/
├── retrieval/
├── persistence/
└── rendering/

web/
├── src/components/ui/
├── src/components/domain/
├── src/features/
├── src/stores/
└── src/lib/

tests/
├── contract/
├── backend/
└── frontend/
```

第一步只建立骨架和最小契约，不实现完整业务逻辑。

### T-000 完成标准

- 后端可以启动 FastAPI；
- `GET /health` 返回成功；
- 后端可以加载并校验一个最小 Pydantic artifact；
- 前端可以启动 Vite；
- 前端可以访问后端健康检查；
- 前端包含一个基础布局和一个占位文档页面；
- `shadcn-vue` 和 Tailwind CSS 已完成基础配置；
- 前后端的本地启动命令和环境变量示例已记录；
- 不接真实模型、不上传真实教材、不实现完整 Agent 流程。

## 实施顺序

### 1. 建立工程骨架

- 创建 Python 项目和 uv 配置；
- 创建 Vue/Vite 项目和 npm 配置；
- 配置 TypeScript、Tailwind CSS、shadcn-vue；
- 创建 FastAPI app 和健康检查；
- 创建前端基础 layout；
- 添加 `.env.example`；
- 明确本地存储目录和 Git 忽略规则。

### 2. 定义最小 artifact 模型

先定义 Pydantic 模型：

- `SourceDocument`；
- `SourceBlock`；
- `KnowledgeUnit`；
- `LearningBlueprint`；
- `ContentArtifact`；
- `ReviewIssue`；
- `LearningDocument` / `DocumentNode`；
- `RunMetadata`。

所有核心 artifact 至少保留：

```text
artifact_id
run_id
version
status
source_refs
created_by
issues
```

### 3. 创建 fixture

在没有确定真实教材前，使用小型 fixture：

- 一个最小 PDF 或测试文本；
- 一个 Learning Blueprint JSON；
- 一个 Learning Document IR JSON；
- 一个 Review Report JSON。

fixture 必须能够让前端先渲染出完整页面。

### 4. 实现 Document IR Renderer

前端先支持以下节点：

- `section`；
- `markdown`；
- `formula`；
- `callout`；
- `source_ref`；
- `quiz`；
- `review_issue`。

建议领域组件：

```text
LearningDocumentRenderer
ChapterOutline
SourcePopover
ReviewIssueBadge
FormulaBlock
QuizCard
CalloutBlock
```

安全约束：

- 不渲染模型生成的任意 Vue 模板；
- 不执行模型生成的 JavaScript；
- 不允许未知节点类型静默通过；
- Markdown 内容必须经过明确的渲染边界和安全处理。

### 5. 实现 PDF 解析

- 使用 PyMuPDF 读取页和文本块；
- 生成页码/块级 `SourceBlock`；
- 保留文本 hash、页码、块序号和解析器版本；
- 用 SQLite FTS5 建立基础全文检索；
- 为关键片段返回 `source_refs`。

如果 POC 教材是扫描 PDF，不要直接扩大范围；先记录 OCR 需求，再单独创建任务。

### 6. 实现 ModelProvider

定义统一接口：

```python
class ModelProvider(Protocol):
    def generate(...): ...
    def generate_structured(...): ...
    def stream(...): ...
```

实现：

- `OpenAIProvider`；
- `OpenAICompatibleProvider`，用于 Ollama 和 vLLM；
- capability check；
- 超时、重试和错误分类；
- 记录 provider、model、base_url、配置版本和耗时。

尚未确定具体默认模型时，先实现 Mock Provider 和配置接口。

### 7. 接入最小 LangGraph 流程

第一版可以先实现：

```text
ingest
  → load_or_create_blueprint
  → generate_document_ir
  → validate_document_ir
  → review
  → assemble
```

暂时可以使用 fixture 蓝图，先验证流程和状态；随后再接真实教材理解节点。

### 8. 加入真实模型运行

在 Mock Provider 和 fixture 路径稳定后，再选择一个默认模型运行真实生成：

- OpenAI API；或
- Ollama 本地模型；或
- vLLM 服务模型。

运行前必须记录：

- provider；
- model；
- base URL；
- prompt/config 版本；
- 输入 artifact 版本；
- 输出 artifact 版本；
- 审核结果。

## 第一版 Demo 不做

- 多教材融合；
- OCR；
- EPUB/HTML 输入；
- 向量数据库和 embedding pipeline；
- Celery、Redis、Kafka；
- 账号、权限和协作；
- 移动端；
- 生产级部署；
- 任意模型生成前端代码；
- VitePress 静态导出；
- 复杂图片、交互和多媒体生成。

## 需要用户在开发中确定的参数

这些参数不阻碍先搭建骨架，但接入真实数据前必须确定：

1. POC 使用的学科、教材和章节；
2. 教材是否为可复制文本型 PDF；
3. 默认模型 Provider；
4. 默认模型名称；
5. 是否使用本地 Ollama/vLLM 作为日常开发模型；
6. 何种审核问题必须阻塞发布；
7. 是否需要在 P0 结束后增加 VitePress 导出。

## 当前完成状态

- [x] 项目目标和第一阶段范围已确定；
- [x] 技术路线已确定；
- [x] JSON Document IR 与 VitePress 的职责边界已确定；
- [x] OpenAI/Ollama/vLLM 模型适配方向已确定；
- [x] Vue + shadcn-vue + Tailwind CSS 前端路线已确定；
- [x] 技术选型文档和 ADR 已更新；
- [x] T-000 工程骨架；
- [x] artifact 模型；
- [ ] fixture 和 renderer；
- [ ] PDF 解析；
- [x] PDF 解析基础骨架（PyMuPDF 页/块级 SourceBlock）；
- [x] ModelProvider（Mock、OpenAI、OpenAI-compatible、能力声明和错误分类）；
- [x] LangGraph 最小流程（PDF → SourceBlock → Blueprint → Document IR → 校验 → 审核 → assemble）；
- [x] 第一版 Demo 端到端运行（Mock/真实 Provider 可替换，当前 PDF 乱码风险会显式进入审核）；
- [ ] POC 教材与章节评估。

## 下一窗口启动提示

可以直接把下面这句话发给新的 Codex 窗口：

> 请读取 `TODO.md`、`AGENTS.md`、`docs/technology-selection.md`、`docs/architecture.md` 和 `tasks/backlog.md`，从 T-000 开始实现第一版 Demo 技术骨架。先不要接入真实教材和真实模型，先完成 FastAPI + Vue/Vite + shadcn-vue/Tailwind + 最小 Pydantic artifact + 健康检查 + 本地启动说明，并在完成后运行最小验证。
