# 下一步 TODO

当前已完成 T-009A/T-009B、T-010 Codex 初评、A-001、A-002、A-003 离线实现和 T-007 题目闭环实现。下一步是对固定教材生成新版本，完成 6 道题 + 4 个重点知识单元的人工抽样并按 P0 rubric 复评；不自动降低发布门槛或新增维护者工作台。

## 开始前

- [x] 阅读 `AGENTS.md`、`README.md`、`docs/requirements.md`、`docs/architecture.md`、`docs/decisions.md`、`documents/wed_design.md` 和 `tasks/backlog.md`。
- [x] 保持项目范围：一个学科的一章、一个学习页面、一个可验证闭环。
- [x] 不新增维护者工作台、权限系统、版本管理页面或其他平台能力。
- [x] 增加单用户本地文档库：教材去重、运行记录、artifact 和文档版本持久化；页面不自动生成。

## T-009A 页面工作

- [x] 检查 `web/src/components/domain/LearningDocumentPage.vue` 和 `LearningDocumentRenderer.vue` 的当前输出。
- [x] 删除页面中的 API 健康状态、`run_id`、`artifact_id`、蓝图版本、审核计数和内部审核面板。
- [x] 保留并优化章节导航、讲解、公式、例题、提示、测验和折叠交互。
- [x] 将来源改成学习者可理解的“教材依据”：章节、页码或短摘录；不直接显示原始 `source_ref`。
- [x] 将事实风险和学术分歧转成中性的“建议核实”“多角度观点”等学习提示。
- [x] 未知节点继续安全拒绝；页面只显示“此部分暂不可用”，详细原因写入 Console 或内部日志。
- [x] 后端返回的审核报告、运行状态和错误信息保留在 API/Console，暂不增加新的工作台 UI。
- [x] 收敛 Document IR 为 `MarkdownNode`、`CalloutNode`、`QuizNode`：正文、公式、例题和证明均使用 Markdown；Callout 仅在内容 Agent 判断需要强调时提出，QuizNode 只承载结构化题目内容，旧 formula/example 文档在读取边界显式转换。

## 验证

- [x] 使用 Mock Provider 启动 FastAPI 和 VitePress。
- [x] 用真实浏览器验证：打开页面 → 阅读章节 → 展开教材依据 → 完成练习。
- [x] 确认学习页面没有 API 状态、审核面板、原始 ID 或重生成按钮。
- [x] 运行前端测试、`npm run build` 和后端回归测试。
- [x] 根据 `docs/evaluation.md` 完成 Codex POC 初评，并把结果写入归档。
- [x] 完成后更新 `tasks/backlog.md`，再决定是否进入 T-010 评估。

## T-010 → A-001 入口

- [x] 读取固定 POC 样本和已有 `deepseek-full-context-live-001` artifact，完成 8 个维度的 Codex 初评。
- [x] 将 T-010 报告写入 `docs/archive/2026-09-14/t010-poc-evaluation.md`，记录证据、问题和发布判断。
- [ ] 项目负责人确认数学内容与最终 P0 状态；T-010 初评仍暂不通过，保留 `at_risk` 预览。
- [x] A-001 已实现 Blueprint `generate → check → route` 回环，离线 Mock/Sequence Provider 覆盖通过、修订、阻塞和失败路径。
- [x] A-002 已实现每知识单元讲解反思子图：来源/Markdown 公式硬检查先于教学 Critic，最多 3 个候选，保留 trace、content-artifact-v3、SQLite/API 摘要和 blocked/fail-fast 路径；内容 Agent 自主组织 Markdown，Callout 仅按需生成，不硬检查“跟做材料”标题；不自动发起 DeepSeek。
- [x] A-003 已在通过 A-002/T-007 后执行：当前教材 FTS5 优先、网络默认关闭、讲解和题目事实分别定向纠错最多两轮；evidence trace、`fact_check` artifact/API 审计端点和 `at_risk` 降级均已覆盖离线测试。

## T-007 题目与 P0 完善

- [x] 收敛 `ContentTask.content_types` 为 `["explanation", "quiz"]`，并扩展 QuizQuestion/QuizArtifact 契约，保留 run、version、task、ContextPack、prompt 和 raw/error 元数据。
- [x] 由 Agent 根据学习目标和 ContextPack 证据自主决定题量（可为 0）；题目答案、解析、目标和 source_refs 通过结构化检查。
- [x] 生成覆盖矩阵并把缺题、坏题、无效来源设为 blocking；目标缺口保留 warning。
- [x] 组装 QuizNode、保留来源回看，并在未知/损坏节点处 fail closed。
- [x] 独立保存 `quiz-v1.json`，更新 API、run manifest 和 SQLite artifact 索引。
- [x] 实现 `HumanReviewRecord` 和 ReviewReport 版本合并，不覆盖旧报告。
- [ ] 用固定数学分析章节重新生成并执行真实运行成本确认（不默认发起 DeepSeek）。
- [ ] 完成 6 道题 + 4 个重点知识单元的人工复核，关闭严重事实/逻辑/公式问题。
- [ ] 按 8 维 rubric 复评，只有 `ReviewReport.status=passed` 才决定 `published`。

T-010 未完成并由人工确认前，不运行新的 DeepSeek 试跑；任何真实试跑都需先确认预计调用量、输入规模和费用。

## 新窗口启动提示

```text
请读取 AGENTS.md、README.md、TODO.md、docs/requirements.md、docs/architecture.md、docs/decisions.md、documents/wed_design.md 和 tasks/backlog.md。

当前 T-010 Codex 初评已归档，A-001 Blueprint loop、A-002 讲解反思 loop、A-003 事实核查 loop 和 T-007 题目闭环已完成。下一步生成固定教材新版本，完成 6 道题 + 4 个重点知识单元的人工抽样和 P0 复评；任何新的 DeepSeek 试跑都必须先确认输入规模、调用量和费用。
```
