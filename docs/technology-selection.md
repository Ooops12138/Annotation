# Annotation 技术选型

## 1. 选型结论

本项目第一阶段采用“Vue 应用 + Python API + LangGraph 工作流 + 强类型 artifact + 本地单机存储”的路线：

| 层次 | 最终选择 | 第一阶段边界 |
|---|---|---|
| 前端框架 | Vue 3 + TypeScript + Vite | 单页应用；不使用 VitePress 作为主运行时 |
| UI 组件 | `shadcn-vue` + Tailwind CSS + `lucide-vue-next` | 使用源码组件和设计 token；不让 Agent 生成整套 CSS |
| 前端状态 | Pinia（界面/运行状态）+ 原生 `fetch` 封装 | 暂不引入复杂状态服务层 |
| 文档渲染 | 自定义 `LearningDocumentRenderer`，输入 JSON Document IR | 根据节点类型映射到受控 Vue 组件 |
| 后端 API | Python 3.12 + FastAPI | REST + SSE/轮询查询运行状态 |
| 后端模型 | Pydantic v2 | Blueprint、Content Artifact、Review Report、Document IR 的契约 |
| Agent 编排 | LangGraph | 负责有状态节点、checkpoint、审核中断、回退和重生成 |
| 模型接入 | `ModelProvider` 抽象 + OpenAI Provider + OpenAI-compatible Provider | 同时支持 OpenAI API、Ollama、vLLM |
| 输入 | PDF-only | P0 处理文本型 PDF；扫描 OCR、EPUB、HTML 放到 P1 |
| PDF 处理 | PyMuPDF（必要时使用 `pymupdf4llm` 辅助转换） | 保留页码、块序号、坐标和原文引用 |
| 检索 | SQLite FTS5 + source_refs 精确定位 | P0 不引入向量数据库和 embedding pipeline |
| 持久化 | SQLite + SQLAlchemy 2 + Alembic | 元数据、运行、版本、审核结果；artifact 内容存 JSON 文件 |
| 任务执行 | FastAPI API + 本地 worker/CLI | P0 不使用 Celery、Redis 或消息队列 |
| 内容格式 | JSON Document IR | Markdown 作为正文节点/导出格式，不作为唯一协议 |
| 数学公式 | KaTeX | 通过专用 formula 节点渲染 |
| 测试 | pytest + Vitest | 加入 artifact 契约和端到端回归样本 |
| 包管理 | uv（Python）+ npm（前端） | 分别使用 `uv.lock` 与 `package-lock.json` 锁定依赖版本 |

## 2. JSON Document IR 是什么

JSON Document IR（Intermediate Representation，文档中间表示）是“学习文档的语义数据结构”，不是网页源码，也不是模型随意输出的一段 Markdown。

它描述文档中有哪些章节、知识单元、讲解、公式、例题、提示、来源、测验和风险标注。前端 renderer 再把这些节点映射成 Vue 组件。

简化示例：

```json
{
  "document_id": "doc-001",
  "blueprint_version": "bp-003",
  "title": "极限与连续",
  "sections": [
    {
      "type": "section",
      "id": "sec-limit",
      "title": "函数极限",
      "children": [
        {
          "type": "explanation",
          "id": "ex-001",
          "knowledge_unit_ids": ["ku-limit-definition"],
          "blocks": [
            {
              "type": "markdown",
              "content": "函数极限描述的是……",
              "source_refs": ["src-book-p12-b04"]
            },
            {
              "type": "formula",
              "latex": "\\lim_{x \\to a} f(x)=L",
              "source_refs": ["src-book-p13-b02"]
            }
          ]
        },
        {
          "type": "quiz",
          "id": "quiz-001",
          "knowledge_unit_ids": ["ku-limit-definition"],
          "items": []
        }
      ]
    }
  ],
  "source_refs": ["src-book-p12-b04", "src-book-p13-b02"],
  "issues": []
}
```

### 2.1 为什么不把 Markdown 或 HTML 作为规范输出

- Markdown 不天然表达知识单元、来源、审核问题、测验和交互组件的结构关系。
- HTML 把内容和呈现耦合在一起，局部重生成、版本比较和安全校验更困难。
- JSON IR 可以经过 Pydantic 校验，也可以渲染成 Vue 页面、Markdown、PDF 或其他输出。
- 前端可拒绝未知节点，避免执行模型生成的任意 HTML、CSS 或 JavaScript。

Markdown 仍然有用：它可以作为 `markdown` 文本块的内容格式，也可以作为接受后的导出目标；但 Agent 之间传递的规范 artifact 是 JSON IR。

## 3. JSON Document IR 与 VitePress 的关系

两者不是竞争关系，而是不同层次的技术：

| 对比 | JSON Document IR | VitePress |
|---|---|---|
| 类型 | 内容/领域数据契约 | 基于 Markdown 的静态站点生成器 |
| 主要输入 | 结构化 artifact | Markdown、frontmatter、静态资源 |
| 生成时机 | Agent 运行时和审核过程中 | 构建时 |
| 是否适合运行状态 | 适合 draft、review、blocked、published | 主要面向已确定的静态内容 |
| 来源/审核/版本 | 可以作为字段和节点表达 | 需要额外约定和插件承载 |
| 交互组件 | 由 Vue renderer 按节点类型加载 | 可通过 Vue in Markdown 扩展，但不是核心契约 |
| 局部重生成 | 天然支持按 artifact/节点处理 | 通常需要重新生成 Markdown 并重新构建 |
| 本项目角色 | P0 的规范输出 | P1 可选的静态发布适配器 |

VitePress 是 Markdown 驱动的静态站点生成器，适合发布已经审核完成的教材章节快照。但 Annotation 的主界面还需要运行状态、审核标注、来源展开、版本比较和局部重生成，因此 P0 采用 Vue SPA 作为主前端。

后续可以增加：

```text
Accepted LearningDocument IR
        ↓
IR → Markdown + frontmatter exporter
        ↓
VitePress static snapshot
```

这不会改变内部 artifact 契约。

## 4. 前端路线：shadcn-vue + Tailwind CSS

Vue 项目不直接使用 React 版 `shadcn/ui`，而使用 `shadcn-vue`。它遵循 shadcn/ui 的核心方式：组件源码进入项目，可直接修改和组合，而不是把所有视觉决策锁在黑盒 npm 组件库中。

前端约定：

- Vue 3 Composition API + TypeScript；
- Vite 作为开发和构建工具；
- `shadcn-vue` 提供 Button、Card、Tabs、Dialog、Sheet、Dropdown、Table、Toast、Progress、Sidebar 等基础组件；
- Tailwind CSS 负责布局、间距、响应式、主题和状态样式；
- `lucide-vue-next` 提供图标；
- `LearningDocumentRenderer` 只组合已存在的组件和少量领域组件，不由 Agent 生成 CSS；
- 领域组件包括 `ChapterOutline`、`SourcePopover`、`ReviewIssueBadge`、`FormulaBlock`、`QuizCard`、`InteractiveBlock`；
- 所有来自模型的内容通过 IR 节点白名单渲染，不允许模型输出任意 Vue 模板或 JavaScript。

这条路线能让 AI 辅助开发聚焦于数据契约、组件组合和业务逻辑，而不是反复手搓基础 CSS、弹窗、表格和表单。

## 5. Agent 与模型适配

### 5.1 Agent 编排：LangGraph

LangGraph 用于定义教材理解、蓝图检查、内容生成、审核、修订和发布之间的有状态流程。业务节点仍然是普通 Python 函数，核心 artifact 使用 Pydantic 模型；不要求每个职责都部署为独立 Agent。

建议的 P0 图结构：

```text
ingest
  → extract_blueprint
  → validate_blueprint
       ├── needs_revision → extract_blueprint
       └── accepted
             → plan_content
             → generate_content
             → review_content
                  ├── needs_revision → generate_content
                  ├── human_review → interrupt / resume
                  └── accepted → assemble_document
```

### 5.2 统一模型适配器

领域层只依赖一个能力接口，不直接依赖 OpenAI、Ollama 或 vLLM 的 SDK：

```python
class ModelProvider(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResponse: ...
    def generate_structured(
        self, request: StructuredGenerationRequest
    ) -> StructuredGenerationResponse: ...
    def stream(self, request: GenerationRequest) -> Iterator[ModelEvent]: ...
```

实现分为两类：

1. **OpenAIProvider**：连接 OpenAI API。
2. **OpenAICompatibleProvider**：连接实现 OpenAI-compatible API 的服务，包括 Ollama 和 vLLM。

配置示例：

```text
provider=openai
base_url=https://api.openai.com/v1
model=<cloud-model>

provider=openai-compatible
base_url=http://localhost:11434/v1
model=<ollama-model>

provider=openai-compatible
base_url=http://localhost:8000/v1
model=<vllm-served-model>
```

适配器必须暴露能力声明，而不是假设所有后端行为完全相同：`supports_structured_output`、`supports_tools`、`supports_streaming`、`supports_vision`、`supports_json_schema`，以及上下文窗口和最大输出限制。

P0 要求：结构化输出、文本生成、错误重试和可记录的模型元数据。工具调用、视觉输入和 embedding 不是 P0 的必要能力。Ollama/vLLM 的 OpenAI-compatible 接口不代表所有模型都支持相同的结构化输出、工具调用或多模态特性，因此运行前要做 capability check。

当前实现提供 `src/annotation/providers/` 下的统一协议、Mock Provider、OpenAI Provider 和 OpenAI-compatible Provider。结构化输出通过可移植的 JSON object 请求并由 Pydantic schema 二次校验；严格 provider-specific JSON Schema 不作为兼容接口的默认能力。`MODEL_PROVIDER=mock` 是离线开发默认值。仓库中的离线 fixture 已覆盖多节讲解、公式、例题、测验和风险标注；真实 DeepSeek 运行通过同一 `OpenAICompatibleProvider` 接口接入，不把 API 输出或密钥作为测试前提。DeepSeek 可通过 `MODEL_THINKING=disabled|enabled` 控制思考模式；默认关闭以降低结构化 JSON 的延迟和截断风险。真实 provider 的请求、原始响应、解析结果和错误追加记录到本地 JSONL 日志，API key 不进入日志。

### 5.3 为什么不在业务层直接使用供应商 SDK

- 方便在云端模型和本地模型之间切换；
- 让 LangGraph 节点只关心任务和 artifact，不关心传输协议；
- 可以统一记录 `provider`、`model`、`base_url`、配置版本和 token/耗时统计；
- 能在能力不足时提前失败，而不是生成半结构化结果后才发现不兼容。

## 6. 输入处理路线

P0 只支持文本型 PDF，流程为：

```text
上传 PDF
  → 计算文件 hash
  → 保存原始文件元数据
  → PyMuPDF 读取页、文本块和坐标
  → 规范化 SourceBlock
  → SQLite FTS5 建立全文索引
  → 教材理解 Agent 使用定位片段
```

`SourceBlock` 至少包含：文档 ID、页码、块序号、原文、页面坐标（若可得）、章节推断、字符范围、文本 hash 和解析器版本。

扫描 PDF OCR、复杂表格、图片内公式、EPUB、HTML 和多教材对齐属于 P1；如果 POC 教材是扫描件，应单独增加 OCR 适配任务，而不是隐式扩大 P0。

## 7. 输出处理路线

所有 Agent 输出先进入版本化 artifact：

```text
Pydantic model
  → schema validation
  → source/blueprint relationship validation
  → persist JSON + metadata
  → review
  → LearningDocument IR
  → Vue renderer
```

输出处理规则：

- 讲解、公式、例题、测验和风险标注是不同节点类型；
- 关键事实、公式和例题必须带 `source_refs` 或明确的补充来源；
- artifact 不通过 Schema 校验时不得进入发布状态；
- 未知节点类型由 renderer 拒绝并记录问题；
- 模型不能输出任意 HTML、Vue SFC、CSS 或 JavaScript 作为可执行内容；
- 最终文档可以导出为 Markdown，但导出是单向呈现适配，不反向替代 IR。

## 8. 后端与任务执行

后端分为两个逻辑进程：

```text
Vue SPA
  ↕ REST / SSE
FastAPI API
  → SQLite runs/artifacts metadata
  → local worker / LangGraph runner
       → ModelProvider
       → PDF/parser/retrieval adapters
```

P0 的 worker 可以是本地 Python 进程或 CLI；不引入 Celery、Redis、Kafka 或云任务队列。API 创建 `run` 后返回 `run_id`，前端通过查询或 SSE 获取阶段状态、问题和产物版本。

当并发、可靠性或多用户需求超过单机 POC 时，再把 worker 替换为队列系统；API、artifact 和 ModelProvider 契约不应因此改变。

## 9. 存储与目录建议

```text
src/annotation/
├── api/                 # FastAPI routes and DTOs
├── domain/              # Pydantic artifact models and policies
├── workflow/            # LangGraph graph and nodes
├── providers/           # OpenAI / OpenAI-compatible adapters
├── ingestion/           # PDF parsing and SourceBlock creation
├── retrieval/           # SQLite FTS5 and source lookup
├── persistence/         # SQLAlchemy repositories and migrations
└── rendering/           # IR export adapters

web/
├── src/components/ui/   # shadcn-vue generated/owned components
├── src/components/domain/# learning-specific Vue components
├── src/features/        # chapters, runs, review, documents
├── src/stores/          # Pinia UI state
└── src/lib/             # API client, IR types, utilities

storage/
├── sources/             # original PDFs (local development only)
├── artifacts/           # versioned JSON artifacts
└── sqlite/              # local database files
```

生产环境的文件存储、密钥、权限和备份不在 P0 解决；本地存储目录不得提交教材、API key 或运行产物到 Git。

## 10. 不选的方案与原因

### 不把 VitePress 作为主前端

VitePress 更适合 Markdown 驱动、构建时确定的静态内容；它不是审核工作台、运行监控和版本化 artifact 的替代品。保留 IR → Markdown → VitePress 的 P1 导出路径。

### P0 不使用向量数据库

单章节的首要问题是来源定位和 Blueprint 结构，不是大规模语义召回。先用页/块引用和 SQLite FTS5，只有在评估证明关键词和结构查询不足时才加入 embedding 与向量库。

### P0 不使用 Celery/Redis

当前没有高并发或跨机器执行需求。过早引入消息基础设施会增加运维变量，影响对内容质量问题的定位。

### 不让模型生成前端代码

模型生成的是受 Schema 约束的内容 artifact；视觉系统和 Vue 组件由代码维护。这样能保持可访问性、样式一致性、安全边界和可测试性。

## 11. 官方参考

- [OpenAI API Documentation](https://developers.openai.com/api/docs/overview)
- [Ollama OpenAI compatibility](https://docs.ollama.com/openai)
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Vue introduction](https://vuejs.org/guide/introduction.html)
- [VitePress: What is VitePress?](https://vitepress.dev/guide/what-is-vitepress)
- [shadcn-vue introduction](https://www.shadcn-vue.com/docs/introduction)
- [Tailwind CSS with Vite](https://tailwindcss.com/docs/installation/using-vite)
- [PyMuPDF documentation](https://pymupdf.readthedocs.io/en/latest/)
