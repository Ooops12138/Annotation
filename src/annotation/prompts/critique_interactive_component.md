# A-004 交互组件语义核查

只评估下方受限规格是否与教材证据、已接受讲解和学习目标一致，并评估控件行为是否真的帮助学习。
输出 `InteractiveComponentCritiqueDraft` schema 中受限的问题代码和简短修订建议；不要输出代码、规格补丁、
新来源、URL 或任意执行指令。

`objective_mismatch`、`source_mismatch`、`mathematical_mismatch`、`interaction_mismatch` 应只在确有阻断性
不一致时使用；`accessibility` 用于不改变数学事实的可访问性 warning。不要替代已有的来源、表达式、安全或
浏览器渲染硬检查。

## 知识单元

{{KNOWLEDGE_UNIT_CONTEXT}}

## 已接受讲解

{{ACCEPTED_CONTENT}}

## 受限 ContextPack

{{CONTEXT_PACK}}

## 组件任务

{{COMPONENT_TASK}}

## 组件规格

{{COMPONENT_SPEC}}

## 浏览器 sandbox

{{SANDBOX_REPORT}}
