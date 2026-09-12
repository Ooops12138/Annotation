# 下一步 TODO

当前只推进 T-009A：把已有 JSON Document IR 呈现为一个适合零基础/初学者的在线学习页面。

## 开始前

- [ ] 阅读 `AGENTS.md`、`README.md`、`docs/requirements.md`、`docs/architecture.md`、`docs/decisions.md`、`documents/wed_design.md` 和 `tasks/backlog.md`。
- [ ] 保持项目范围：一个学科的一章、一个学习页面、一个可验证闭环。
- [ ] 不新增维护者工作台、权限系统、版本管理页面或其他平台能力。

## T-009A 页面工作

- [ ] 检查 `web/src/components/domain/LearningDocumentPage.vue` 和 `LearningDocumentRenderer.vue` 的当前输出。
- [ ] 删除页面中的 API 健康状态、`run_id`、`artifact_id`、蓝图版本、审核计数和内部审核面板。
- [ ] 保留并优化章节导航、讲解、公式、例题、提示、测验和折叠交互。
- [ ] 将来源改成学习者可理解的“教材依据”：章节、页码或短摘录；不直接显示原始 `source_ref`。
- [ ] 将事实风险和学术分歧转成中性的“建议核实”“多角度观点”等学习提示。
- [ ] 未知节点继续安全拒绝；页面只显示“此部分暂不可用”，详细原因写入 Console 或内部日志。
- [ ] 后端返回的审核报告、运行状态和错误信息保留在 API/Console，暂不增加新的工作台 UI。

## 验证

- [ ] 使用 Mock Provider 启动 FastAPI 和 VitePress。
- [ ] 用真实浏览器验证：打开页面 → 阅读章节 → 展开教材依据 → 完成练习。
- [ ] 确认学习页面没有 API 状态、审核面板、原始 ID 或重生成按钮。
- [ ] 运行前端测试、`npm run build` 和后端回归测试。
- [ ] 根据 `docs/evaluation.md` 做一次 POC 人工抽样，并把结果写入归档。
- [ ] 完成后更新 `tasks/backlog.md`，再决定是否进入 T-010 评估。

## 新窗口启动提示

```text
请读取 AGENTS.md、README.md、TODO.md、docs/requirements.md、docs/architecture.md、docs/decisions.md、documents/wed_design.md 和 tasks/backlog.md。

当前只做 T-009A 的学习者页面收敛。项目目标是把教材一章转换成面向零基础/初学者的在线交互式学习文档。请先检查 LearningDocumentPage.vue 和 LearningDocumentRenderer.vue，删除 API 状态、运行/版本 ID、审核面板和原始 source_ref；保留讲解、例题、公式、练习、章节导航、教学化教材依据和必要的中性风险/观点提示。审核报告、来源检索和重生成只通过 API、Console 或日志调试，不新建维护者工作台。完成后运行测试、VitePress build 和真实浏览器核验。
```
