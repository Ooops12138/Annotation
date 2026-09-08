# Annotation 实施 Backlog

本清单按第一阶段闭环的依赖排序。默认技术路线见 `docs/technology-selection.md`；任务可以验证选型是否满足 POC，但不能在没有 ADR 的情况下悄悄替换技术边界。完成任务时应同步更新受影响的设计文档和决策记录。

## P0：跑通单章节闭环

### T-000 建立技术骨架与本地开发约定

- **目的**：创建 Python/FastAPI、Vue/Vite、LangGraph、SQLite 和 artifact 目录边界。
- **依赖**：无。
- **产出**：`src/annotation/`、`web/`、`tests/` 的空骨架、uv/npm 配置、环境变量示例和本地启动说明。
- **完成定义**：前端能访问健康检查 API；后端能加载一个最小 Pydantic artifact；不包含业务生成逻辑。

**状态：已完成（2026-09-05）**。已建立 `src/annotation/`、`web/`、`tests/`、`storage/` 目录边界，FastAPI `/health` 与 fixture 文档 API，Pydantic artifact 最小契约，Vue/Vite + Tailwind/shadcn-vue 风格基础组件，以及 README 本地启动说明。T-000 阶段本身不接真实教材或模型；后续 T-005A/T-005B 已补上可替换 Provider 和离线最小 LangGraph 验证流程。

### T-001 选择并冻结 POC 教材与章节

- **目的**：确定一门可验证性强的学科、一个章节和合法输入材料。
- **依赖**：无。
- **产出**：教材版本、章节范围、授权说明、学习对象和学习目标。
- **完成定义**：评审者能复现同一输入；章节边界和关键目标已记录。

**状态：已完成（2026-09-07）**。POC 输入冻结为 `books/数学分析第1章.pdf` 的第 1 章《实数系与复数系》（25 页）；学习对象、目标、授权前提和已知抽取风险记录于 [`docs/poc-scope.md`](../docs/poc-scope.md)。

### T-002 建立教材引用与解析适配边界

- **目的**：把教材转化为可定位片段，保留章节/页码或等价引用。
- **依赖**：T-001。
- **产出**：Source Material / Source Reference 契约、解析样本和失败记录。
- **完成定义**：关键段落可从派生片段回到原文位置；解析失败可见。

**状态：已完成（解析骨架）**。`parse_pdf` 使用 PyMuPDF 生成页/块级 `SourceBlock`，保留页码、块序号、bbox、文本 hash、解析器版本和 `source_ref`；文本不做静默修正，缺失 PDF 会显式返回错误。

补充验收：`extraction_warnings` 对替换字符和零文本块产生可见 warning；`/api/source-preview` 返回样例片段和索引数量。

### T-002A 验证 PDF 解析和 SQLite FTS5 检索

- **目的**：验证 PyMuPDF 的页/块级定位和 SQLite FTS5 是否足以支持 POC。
- **依赖**：T-001、T-002。
- **产出**：SourceBlock 样本、检索查询样本、召回问题清单。
- **完成定义**：评估样本中的关键定义、公式和例题可以被定位；不足时记录是否需要 OCR/embedding。

**状态：已完成（2026-09-07）**。新增本地 SQLite FTS5 索引、幂等 upsert 和 `/api/source-search` 查询接口；结果返回 `source_ref`、页码、块号、原文和 BM25 rank。固定查询和召回风险记录于 [`docs/poc-scope.md`](../docs/poc-scope.md)。扫描 PDF/OCR 与语义召回仍不属于 P0。

### T-003 定义并验证 Learning Blueprint 最小 Schema

- **目的**：落实知识单元、关系、教学材料、来源和状态的语义契约。
- **依赖**：T-001、T-002。
- **产出**：版本化蓝图示例、校验规则、人工检查清单。
- **完成定义**：至少覆盖一个完整小节，并能表达概念、公式/定理、例题、前置关系和来源。

**状态：已完成（2026-09-07）**。Pydantic artifact 已支持知识单元的类型、学习目标、前置关系、相关单元和教学材料；新增 `BlueprintCheckResult` 及确定性 `validate_blueprint` 质量门，检查最少单元数、来源有效性、前置关系和教学材料覆盖。

### T-004 实现教材理解与蓝图检查流程

- **目的**：从教材生成蓝图草案，并发现遗漏、错误合并、依赖问题和来源缺失。
- **依赖**：T-002、T-003。
- **产出**：蓝图草案、检查报告、需要回退的案例。
- **完成定义**：对 POC 章节能解释主要结构；问题可回到抽取步骤修正。

**状态：已完成（2026-09-07，POC 最小实现）**。LangGraph 蓝图节点使用结构化 Provider 输出后执行来源/覆盖/关系检查；检查失败会进入 fallback 并写入 warning，原始问题不会静默丢弃。完整的多轮修订 Agent loop 仍属于后续 T-008/T-010 范围。

### T-005 选择最小编排与 artifact 生命周期

- **目的**：定义任务计划、状态、版本、重试、人工介入和失败恢复。
- **依赖**：T-003、T-004。
- **产出**：一次运行的状态图、artifact 元数据示例、失败/重生成策略。
- **完成定义**：不依赖隐式上下文即可说明每个阶段输入、输出和停止条件。

**状态：已完成（2026-09-07，POC 最小实现）**。状态图、artifact 元数据、provider metadata、warning/error 暴露和发布门已在 `src/annotation/workflow/graph.py` 落地；失败生成使用显式 fallback，旧输出不会在本地 fixture 中被静默覆盖。持久化 checkpoint/重生成版本比较仍列入后续增强。

### T-005A 实现 ModelProvider capability contract

- **目的**：让 OpenAI API、Ollama 和 vLLM 在同一领域接口下可替换。
- **依赖**：T-003、T-005。
- **产出**：Provider 协议、OpenAIProvider、OpenAICompatibleProvider、能力探测和失败分类。
- **完成定义**：至少能对三类 endpoint 执行一次结构化输出测试；不支持的能力会提前报告。

**状态：已完成（2026-09-05）**。已实现 `ModelProvider` 协议、请求/响应与运行元数据契约、`MockProvider`、`OpenAIProvider`、`OpenAICompatibleProvider`（Ollama/vLLM）、能力声明、结构化输出校验、流式事件和统一错误分类。默认环境配置使用 Mock，不需要网络即可运行。

### T-005B 接入最小 LangGraph 流程

- **目的**：验证显式 artifact 状态可以在 LangGraph 节点之间传递，并保留 provider 运行元数据。
- **依赖**：T-005A、T-003。
- **产出**：`ingest → load_or_create_blueprint → generate_document_ir → validate_document_ir → review → assemble` 图、离线 fixture 运行入口和 API 触发接口。
- **完成定义**：默认 Mock Provider 可完成一次流程；蓝图、文档、来源、审核状态和 provider 元数据可从运行结果读取；失败通过 `errors` 暴露。

**状态：已完成（2026-09-05）**。实现于 `src/annotation/workflow/graph.py`，API 入口为 `POST /api/workflow/run`；当前已接入 `books/` PDF、结构化 Blueprint/Document IR 生成、来源校验和前端结果展示。

运行验证：已使用根目录 `.env` 中的 `deepseek-v4-flash` 完成一次真实运行（1206 个 SourceBlock、3 个 Blueprint 单元、4 个 Document IR 节点、状态 `published`）。解析器会在实际出现替换字符时保留审核 warning；不宣称 OCR 或教材内容质量已通过最终验收。

补充运行记录（2026-09-07）：DeepSeek 已支持通过 `MODEL_THINKING=disabled` 关闭思考模式；真实 provider 调用会把每个 agent 的输入、原始输出、解析结果和错误追加到 `storage/model-calls.jsonl`。当前最小流程仍只向 agent 提供教材开头 12 个 SourceBlock，主要验证首节，不代表整章生成。

离线回归样例已升级为“实数系与复数系”多节文档，包含 3 个章节区段、公式、3 个例题、测验和风险标注；`MODEL_PROVIDER=mock` 时可重复生成，不需要网络。`MODEL_PROVIDER=deepseek` 时工厂会构造 OpenAI-compatible DeepSeek provider，需同时设置 `MODEL_BASE_URL`、模型名和 API key。

### T-006 生成章节讲解与教学材料

- **目的**：基于已接受或明确标记状态的蓝图生成学习路径、解释、例题/证明和必要衔接。
- **依赖**：T-004、T-005。
- **产出**：带来源的内容 artifact 和生成运行记录。
- **完成定义**：关键知识单元有对应内容；补充内容与蓝图关联并标记角色。

### T-007 生成可追溯的练习与测验

- **目的**：让测验覆盖学习目标和关键知识，而不是泛化问答。
- **依赖**：T-006、T-003。
- **产出**：题目、答案/解析、覆盖映射、风险标记。
- **完成定义**：每个核心目标至少有检查方式；错误题目可阻塞发布。

### T-008 建立基础审核与修订闭环

- **目的**：检查事实、逻辑、公式/定义、遗漏、衔接、来源和学术分歧。
- **依赖**：T-006、T-007。
- **产出**：审核报告、问题分类、修订/重生成版本、人工介入点。
- **完成定义**：审核问题可定位；旧版本不被静默覆盖；阻塞、带风险和通过状态可区分。

### T-009 整合为在线学习文档

- **目的**：按学习蓝图组织文本、例题、测验、来源和风险标注。
- **依赖**：T-008。
- **产出**：可打开的 POC 文档或等价呈现产物。
- **完成定义**：评审者能完成阅读、练习和回看来源；呈现不打乱主学习路径。

### T-009A 建立 JSON Document IR renderer

- **目的**：使用 Vue、shadcn-vue 和 Tailwind CSS 将受控 IR 渲染为学习文档。
- **依赖**：T-003、T-009。
- **产出**：IR 节点白名单、领域组件、来源面板、风险标注、公式和测验组件。
- **完成定义**：未知节点被拒绝并记录；模型不能注入任意 HTML/CSS/JavaScript；主要页面可由组件组合完成。

### T-010 执行第一阶段评估并做发布决策

- **目的**：用固定样本、自动检查和人工 rubric 判断是否通过 POC。
- **依赖**：T-004、T-008、T-009。
- **产出**：评估报告、问题清单、回归样本、通过/不通过决策。
- **完成定义**：满足 `docs/evaluation.md` 的硬门槛；失败项进入 backlog 而不是被隐藏。

## P1：在 POC 之后扩展

### T-010A 增加 IR 到 Markdown/VitePress 的静态导出

- **依赖**：T-010、T-009A。
- **重点**：只导出 accepted 版本，保留 frontmatter、来源和版本信息；不改变 IR 作为主契约的地位。

### T-011 增加多教材主次对齐

- **依赖**：T-010。
- **重点**：主教材骨架、补充映射、深度差异、冲突和来源展示。

### T-012 增加背景/应用/图表/交互内容适配器

- **依赖**：T-006、T-008、T-009。
- **重点**：按知识特点选择，不把多媒体数量当作质量指标。

### T-013 建立评估回归集与更细粒度指标

- **依赖**：T-010。
- **重点**：跨运行比较、问题趋势、评审一致性和用户学习反馈。

### T-014 评估可替换模型、检索、存储和呈现实现

- **依赖**：T-005、T-010。
- **重点**：用真实 POC 证据决定技术选型，并为每个选型补充 ADR。

## 暂不排期

- 账号、权限、协作和社区；
- 移动端；
- 大规模多租户部署和高可用；
- 通用知识库或课程市场；
- 在没有 POC 证据前预先建设复杂 Agent 框架、图数据库或自动评估平台。
