# 下一步 TODO

## 当前优先：单章真实复评问题闭环

复评与修复：[单章复评](docs/p0-reevaluation-2026-10-22.md)、[问题修复记录](docs/p0-fixes-2026-10-22.md)。状态和证据占位缺陷已修复；数学候选已另存，保持 P0 未验收。

逐步操作与填写入口：[人工审核清单](docs/manual-review-checklist.md)，现在先完成 M1–M4。

- [ ] 对照整章目录核查当前分层蓝图的真实语义覆盖，重点检查复数、有理数/无理数等遗漏；完整扫描不代表语义零遗漏。
- [ ] 复核 `results/p0-fixes/math-candidates/review.md` 的 4 份编辑候选，通过后显式登记新文档与审核版本；旧学习页面尚未被局部草稿替换。
- [ ] 用真实模型验证零向量、实数/复数作用域和正整数限制能被识别；检查补查后证据充分性及新增判定调用量。
- [ ] 确认新试跑的调用量、输入规模和费用后，再对当前完整版本生成、复评并完成人工签字。

## 内容 Agent 自主检索重构

- [x] 将内容生成改为由 Content Agent 自主决定检索问题并调用教材检索 Tool；允许在生成前按需进行多轮检索，而不是由规则在 Agent 运行前构造 ContextPack。
  技术决策已确定，待实现内容 Agent 的查询规划回路。
- [x] 将 ContextPack 改为本次 Tool 调用及其返回教材片段的审计快照；`source_refs` 只记录实际取用的证据，不能再作为 Blueprint 或 ContentTask 的来源白名单。

- 记录本次内容 Agent 实际调用了哪些检索 Tool、每次查了什么、返回哪些教材块、最终取用了哪些片段；
- 为后续内容校验、题目生成、事实核查和页面“教材依据”提供一个稳定、可回放的证据快照。
  也就是说流程变成：
  Content Agent → 检索 Tool 调用 → 选用教材片段 → 生成内容 → ContextPack/检索快照落盘
  而不是：
  规则构造 ContextPack → Content Agent 只能基于它生成

- [x] 保持 SQLite FTS5、向量检索及未来其他检索方式使用同一 Tool 输入/输出契约；由适配器实现具体检索策略，不把策略写入 Content Agent 或 Blueprint。
- [x] 统一教材与网络检索结果契约；网络查询默认关闭，内容 Agent 可显式选择。
- [x] 采用 LlamaIndex 作为可选 RAG 基础框架，Chroma 作为向量库，支持本地 embedding 路径和 OpenAI-compatible API。
- [x] 采用 SQLite FTS5 + 向量检索的混合召回，暂不加入重排。
- [x] 完成 BGE-small-zh-v1.5 CPU + Chroma 的真实运行、增量索引和重启复用验证；已配置本机内容 Agent 的可选混合检索。
- [x] 完成固定 25 页/1,206 块/48 题的 FTS5、向量、混合检索开发基线；多次运行质量指标一致，最终报告位于 `results/retrieval/local-final/report.md`，首轮/复跑记录位于 `results/retrieval/local/` 与 `results/retrieval/local-repeat/`。
- [x] 完成硅基流动 BGE-M3 真实 API smoke、同一 48 题三轮评估、独立进程复用全部 1,206 块及 auto 初始化验证；结果见 `results/retrieval/siliconflow-api/`，与 CPU 的对比见 `results/retrieval/comparison.md`。API Recall@5 未超过本地基线，排名波动和无答案误召回已记录。
