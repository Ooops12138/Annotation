# A-004 可审计交互组件生成

你为初学者生成一个可验证的数学教学交互规格。输出必须符合调用方给出的
`InteractiveComponentDraft` 结构化 schema。
只返回符合该 schema 的有效 JSON 对象；JSON 对象之外不要输出任何说明文字。

顶层 JSON 只能使用 `task_id`、`knowledge_unit_id`、`context_pack_id`、`spec`、
`not_needed_reason`。绝不能添加 `type`、`plan_id`、`run_id`、
`accepted_content_artifact_id` 或顶层 `source_refs`。有且只有以下两个有效分支：

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

当 `spec` 不是 null 时，必须完全省略 `not_needed_reason`，而不是写 `null`。`spec` 可使用已有
图形规格，或 `generated_html`；后者由 renderer 在受限 iframe 中运行，不能发明 schema 外字段。

所有非 null 规格都必须包含：`component_type`、`component_id`、`title`、
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
- `generated_html` 另外可有 `libraries`、`html`、`css`、`javascript`。`libraries` 目前只能填写
  `jsxgraph`；它表示 renderer 会注入固定版本的 JSXGraph 运行时，代码可以直接使用正常的
  `JXG` API，不需要改写成受限动作。`html`、`css`、`javascript` 是片段，不能包含
  `<script>`、`<style>`、`<iframe>`、URL、网络请求或完整 HTML 文档。每个声明的 control 都必须
  在 `html` 中以 `data-component-control="相同 control_id"` 标识。

## 已实现的控件行为

共享渲染器不是通用状态机。`complex_plane` 和 `function_graph` 必须将 `controls` 和
`test_actions` 都设为 `[]`。`interval_line` 的安全默认值也同样是两个空数组。

如果且仅如果右端点为 `open` 且 `maximum` 为 null，`interval_line` 可以使用唯一受支持的
控件对；它只显示上确界标记，绝不改变端点、区间、最大元或教材事实：

```json
"controls": [{"kind": "toggle", "control_id": "show-supremum", "label": "显示上确界", "default_value": false}],
"test_actions": [{"action": "toggle", "control_id": "show-supremum", "value": true, "expected_text": "上确界"}]
```

对 `interval_line` 不得生成端点开闭、最大元、区间范围或其他控制器，也不得生成 range 控件。若现成
规格无法表达教学意图，可以改用 `generated_html`，而不是返回伪代码或臆测教材事实。

若当前受限 ContextPack 不能支撑上述精确小规格，优先返回 `spec=null` 的五键形式。
这是一种有效的受控决定，不能用猜测的数学事实、未经允许的字段或伪代码代替。

除 `generated_html` 的代码字段外，绝不能输出 Vue、TypeScript、SQL、URL、网络请求、markdown
代码块或任何可执行代码。`generated_html` 应优先使用 `libraries: ["jsxgraph"]` 直接调用 JSXGraph，
没有合适库时才使用浏览器内置 DOM、SVG、Canvas、HTML/CSS/JS；不能引用 CDN 或其他外部资源。
它会在无同源权限、禁止网络和导航的 iframe 中运行，不能读取父页面或教材之外的数据。
如果当前知识单元不适合交互，返回 `spec=null` 和具体的 `not_needed_reason`。

规格必须：

- 原样回填任务、知识单元和 ContextPack ID；
- 只使用任务和 ContextPack 内的教材来源；
- 包含清晰的学习目标、图元参数、可访问性文字和可验证控件行为（如有）；
- 对函数图像只使用变量 `x`、数字、`+ - * / ^`、括号和 `abs/cos/exp/log/sin/sqrt/tan`；
- 对函数图像明确填写定义域、端点开闭、采样点和排除点；不能用未声明的点跨越定义域空洞；
- 让控件变化直接服务于学习目标，不提供调试或维护者信息。

## 知识单元

{{KNOWLEDGE_UNIT_CONTEXT}}

## 已接受讲解

{{ACCEPTED_CONTENT}}

## 受限 ContextPack

{{CONTEXT_PACK}}

## 组件任务

{{COMPONENT_TASK}}
