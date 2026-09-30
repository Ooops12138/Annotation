# A-004 可审计交互组件生成

你为初学者生成一个可验证的数学教学交互规格，返回符合
`InteractiveComponentDraft` 的 JSON 对象。

顶层字段为 `task_id`、`knowledge_unit_id`、`context_pack_id`、`spec` 和按需提供的
`not_needed_reason`。有效分支如下：

运行时字段如 `plan_id`、`run_id`、`accepted_content_artifact_id` 和顶层
`source_refs` 由调用方绑定，不属于草稿输出。

```json
{
  "task_id": "exact task id",
  "knowledge_unit_id": "exact knowledge unit id",
  "context_pack_id": "exact ContextPack id",
  "spec": null,
  "not_needed_reason": "specific reason when spec is null"
}
```

或：

```json
{
  "task_id": "exact task id",
  "knowledge_unit_id": "exact knowledge unit id",
  "context_pack_id": "exact ContextPack id",
  "spec": {
    "component_type": "complex_plane",
    "component_id": "plane-1",
    "title": "short learner title",
    "learning_objective": "one supported objective",
    "source_refs": ["exact source ref"],
    "accessibility": {
      "aria_label": "short label",
      "description": "what is rendered",
      "observation": "what to observe"
    },
    "controls": [],
    "test_actions": [],
    "annotations": [],
    "points": [{"point_id": "z", "real": 1, "imaginary": 0, "label": "z"}]
  }
}
```

`spec` 非 null 时必须完全省略 `not_needed_reason`。`spec` 可使用已有图形规格或在受限 iframe
中运行的 `generated_html`。

所有非 null 规格包含：`component_type`、`component_id`、`title`、
`learning_objective`、`source_refs`、`accessibility`、`controls`、`test_actions`、
`annotations`。其中 `accessibility` 只能有 `aria_label`、`description`、
`observation`；每个 `controls` 项只能是 `{ "kind": "toggle", "control_id", "label", "default_value" }`
或 `{ "kind": "range", "control_id", "label", "minimum", "maximum", "step", "default_value" }`；
每个 `test_actions` 项只能有 `action`（`toggle` 或 `set_range`）、`control_id`、
`value`、`expected_text`；每个 `annotations` 项只能有 `annotation_id`、`x`、`y`、`text`。

- `interval_line` 另外只能有 `interval_start`、`interval_end`、`left_endpoint`、
  `right_endpoint`、`supremum`、`maximum`。`supremum` 必须等于 `interval_end`，右端点开放时 `maximum` 必须为 null。
- `complex_plane` 另外只能有 `points`，每个点只能有 `point_id`、`real`、`imaginary`、`label`。
- `function_graph` 另外只能有 `formula`、`domain`、`sample_points`、`excluded_points`、
  `sample_count`；`domain` 只能有 `start`、`end`、`start_endpoint`、`end_endpoint`。
- `generated_html` 另外可有 `libraries`、`html`、`css`、`javascript`。`libraries` 仅支持
  `jsxgraph`；renderer 会注入其运行时。代码字段是片段，每个声明的 control 都须在 `html`
  中以 `data-component-control="相同 control_id"` 标识，并且不能依赖外部资源或父页面。

## 已实现的控件行为

共享渲染器不是通用状态机。`complex_plane` 和 `function_graph` 必须将 `controls` 和
`test_actions` 都设为 `[]`。`interval_line` 的安全默认值也同样是两个空数组。

如果且仅如果右端点为 `open` 且 `maximum` 为 null，`interval_line` 可以使用唯一受支持的
控件对；它只显示上确界标记，绝不改变端点、区间、最大元或教材事实：

```json
"controls": [{"kind": "toggle", "control_id": "show-supremum", "label": "显示上确界", "default_value": false}],
"test_actions": [{"action": "toggle", "control_id": "show-supremum", "value": true, "expected_text": "上确界"}]
```

`interval_line` 不使用其他控件或 range 控件。现成规格无法表达教学意图时，可改用
`generated_html`。

ContextPack 不足以支撑精确规格时，返回 `spec=null` 与具体原因。

`generated_html` 优先使用 `libraries: ["jsxgraph"]`；没有合适库时可使用浏览器内置
DOM、SVG 或 Canvas。它在禁止网络、导航和父页面访问的 iframe 中运行。
如果当前知识单元不适合交互，返回 `spec=null` 和具体的 `not_needed_reason`。

规格必须：

- 原样回填任务、知识单元和 ContextPack ID；
- 只使用任务和 ContextPack 内的教材来源；
- 包含清晰的学习目标、图元参数、可访问性文字和可验证控件行为（如有）；
- 对函数图像只使用变量 `x`、数字、`+ - * / ^`、括号和 `abs/cos/exp/log/sin/sqrt/tan`；
- 对函数图像明确填写定义域、端点开闭、采样点和排除点；不能用未声明的点跨越定义域空洞；
- 让控件变化直接服务于学习目标。

## 知识单元

{{KNOWLEDGE_UNIT_CONTEXT}}

## 已接受讲解

{{ACCEPTED_CONTENT}}

## 受限 ContextPack

{{CONTEXT_PACK}}

## 组件任务

{{COMPONENT_TASK}}
