# A-004 交互组件语义核查

只评估下方受限规格是否与教材证据、已接受讲解和学习目标一致，并评估控件行为是否真的帮助学习。
输出 `InteractiveComponentCritiqueDraft` schema 中受限的问题代码和简短修订建议；不要输出代码、规格补丁、
新来源、URL 或任意执行指令。
只返回符合该 schema 的有效 JSON 对象；JSON 对象之外不要输出任何说明文字。

输出必须严格是以下形状，顶层只能有 `issues`：

```json
{"issues":[{"code":"accessibility","message":"简短问题","suggested_action":"简短建议"}]}
```

每个 issue 只能有 `code`、`message`、`suggested_action` 三个字段；不得输出
`issue_id`、`category`、`severity`、`target_id`、`layer`、`source_refs`、`schema_version`、
`summary` 或任何其他字段。没有问题时只输出 `{"issues":[]}`。

共享渲染器的控件边界是验收契约的一部分：`interval_line` 只支持可选的
`show-supremum` toggle（它只显示上确界标记），不支持端点开闭、区间范围或最大元切换；
`complex_plane` 与 `function_graph` 当前不支持控件。不要因为规格没有端点切换而提出
`interaction_mismatch`；只有实际行为与规格自述或教材事实矛盾时，才使用该代码。

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
