# A-004 交互组件语义核查

评估受限规格是否与教材证据、已接受讲解和学习目标一致，以及控件行为是否帮助学习。
返回 `InteractiveComponentCritiqueDraft` 的问题代码和简短修订建议。
不新增来源。
不得输出 schema 外内容。

输出必须严格是以下形状，顶层只能有 `issues`：

```json
{"issues":[{"code":"accessibility","message":"简短问题","suggested_action":"简短建议"}]}
```

每个 issue 包含 `code`、`message`、`suggested_action`；没有问题时返回 `{"issues":[]}`。

共享渲染器的控件边界是验收契约：`interval_line` 只支持可选的
`show-supremum` toggle（它只显示上确界标记），不支持端点开闭、区间范围或最大元切换；
`complex_plane` 与 `function_graph` 当前不支持控件。不要因为规格没有端点切换而提出
`interaction_mismatch`；只有实际行为与规格自述或教材事实矛盾时，才使用该代码。

`objective_mismatch`、`source_mismatch`、`mathematical_mismatch`、`interaction_mismatch` 只用于
阻断性不一致；`accessibility` 用于不改变数学事实的可访问性问题。
不要替代已有的来源、表达式、安全或浏览器渲染硬检查。

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
