# 下一步 TODO

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
- [ ] 确认 embedding 模型并完成 Chroma 索引构建与真实运行验证；当前只提供可选适配器，不伪装为已启用。
