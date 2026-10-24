# Mock 与离线运行边界

## 结论

Mock 在开源项目和已经交付的产品中都很常见，但通常属于测试、演示或开发环境边界，不属于生产业务逻辑本身。

Annotation 保留 mock，用于无 API key 的离线回归、确定性契约测试和本地演示。Mock 不应承担生产默认行为，workflow
也不应通过 `provider == "mock"` 直接决定内容生成策略。

## 按项目阶段划分

| 阶段 | 是否使用 mock | 允许的形式 | 验收含义 |
| --- | --- | --- | --- |
| 单元测试 | 是 | `SequenceProvider`、HTTP mock、固定 fixture | 验证边界、错误和路由，不证明模型效果 |
| 集成/回归测试 | 是 | `OfflineFixtureProvider` 或 offline profile | 验证完整数据链路、artifact 和页面契约 |
| 本地演示 | 可选 | 明确的 offline profile | 只能证明系统可运行，不证明教材质量 |
| 真实试跑 | 否 | DeepSeek/OpenAI-compatible provider | 验证真实模型接入、成本和内容质量 |
| 交付生产 | 默认否 | 通过配置显式选择真实 provider | 未配置真实 provider 时应尽早失败或明确提示 |

## 已解决的问题

旧 `MockProvider` 同时包含 provider 替身和六单元离线 fixture；旧 `SequenceProvider` 也继承了它。workflow 曾直接
判断 mock，并各自生成讲解、题目、课程计划或组件，使业务流程、模型适配和测试数据互相耦合。

旧工厂和运行时把 mock 作为默认值；现在默认使用 live 配置，缺少真实 provider 配置时明确报错，不会悄悄生成离线内容。

## 目标边界

```text
src/annotation/providers/   provider 协议、真实适配器、配置工厂
src/annotation/offline/     离线 fixture provider 和按 schema 组织的 fixture
tests/support/              SequenceProvider、HTTP/错误替身和 web fixture
src/annotation/workflow/    依赖 ModelProvider 和可选 WorkflowProviderCapabilities
```

离线输出必须继续经过现有 Pydantic schema、来源检查、审核和 artifact 持久化；fixture 不能绕过业务校验。

## 已执行

- `MODEL_PROFILE=offline|live` 已成为显式配置；offline 不需要 `MODEL_PROVIDER`，live 必须显式选择真实 provider。
- 删除了 `MockProvider` 和 `MODEL_PROVIDER=mock` 路径；离线实现改名为 `OfflineFixtureProvider`。
- `SequenceProvider` 已移入 `tests/support`，不再从生产 provider 包导出。
- workflow fixture 通过可选的 `WorkflowProviderCapabilities` 协议提供；普通模型 provider 仍使用统一的结构化请求。
- `SequenceProvider` 仅存在于测试支持目录，并显式安装测试 workflow fixture，不再继承生产 offline provider。
- 讲解、课程计划、题目、交互组件及 web search fixtures 已分别移入 `annotation/offline` 和 `tests/support`。

## 后续迁移

workflow 分支下沉到 provider capability 已完成。模块拆分属于独立结构任务，应在单独确认调用边界后进行。

## 完成定义

- 真实 provider 与 offline provider 经过同一 workflow、schema、审核和 artifact 路径；
- workflow 通过显式 capability 选择离线 fixture 行为，不依赖 profile 或 provider 名称；
- 测试显式选择 sequence/fixture，不依赖全局默认 mock；
- 缺少真实模型配置时不会静默声称完成真实生成；
- README、`.env.example` 和测试说明对 offline/live 一致；
- 无网络、无 API key 时离线回归仍稳定通过。

