# Annotation（书注）

Annotation 是一个教材驱动的 AI 学习文档生成项目。

输入一本教材的一章，系统完成：

```text
教材 → 来源解析 → Learning Blueprint → 学习内容 → 基础审核 → 在线学习文档
```

最终页面面向零基础或初学者，重点是循序渐进的讲解、例题、公式、练习、学习路径、教材出处和必要的中性风险/观点提示。项目不是通用教育平台，也不是静态文档导出器。

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

默认离线运行：

```powershell
$env:MODEL_PROVIDER = "mock"
uv run pytest -q
```

运行一次工作流：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/workflow/run
```

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
