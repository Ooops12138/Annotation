# A-004 受控交互组件修订

根据下列硬检查、sandbox 和语义反馈，修订一个 `InteractiveComponentDraft`。
输出必须只包含调用方 schema 所允许的结构化数据。只有 `generated_html` 的 `libraries`、
`html`、`css`、`javascript` 字段可包含组件实现；不得输出 URL 或任意表达式 API。
只返回符合该 schema 的有效 JSON 对象；JSON 对象之外不要输出任何说明文字。
顶层 JSON 只能使用 `task_id`、`knowledge_unit_id`、`context_pack_id`、`spec`、
`not_needed_reason`；绝不能添加 `type`、`plan_id`、`run_id`、
`accepted_content_artifact_id` 或顶层 `source_refs`。当 `spec` 为 null 时必须给出具体
`not_needed_reason`；当 `spec` 非 null 时必须完全省略 `not_needed_reason`，不能写为 null。

非 null 的 `spec` 可以是受限的 `interval_line`、`complex_plane`、`function_graph` 或 `generated_html`。
所有类型都只能使用这些共同字段：`component_type`、`component_id`、`title`、
`learning_objective`、`source_refs`、`accessibility`（仅 `aria_label`、`description`、
`observation`）、`controls`、`test_actions`、`annotations`。`controls` 仅允许 `toggle`
或 `range` 的标准字段；`test_actions` 仅允许 `toggle` 或 `set_range` 及其
`control_id`、`value`、`expected_text`。类型专属字段仅为：`interval_line` 的
`interval_start`、`interval_end`、`left_endpoint`、`right_endpoint`、`supremum`、
`maximum`；`complex_plane` 的 `points`（每项仅 `point_id`、`real`、`imaginary`、
`label`）；`function_graph` 的 `formula`、`domain`、`sample_points`、
`excluded_points`、`sample_count`；`generated_html` 的 `libraries`、`html`、`css`、`javascript`。生成式代码是
片段，不能包含 `<script>`、`<style>`、`<iframe>`、URL 或网络请求，并且每个 control 必须在 html 中使用
`data-component-control` 标识。不得发明 `coordinate_axes`、`objects`、
`readouts`、`interactive_strategy` 或其他字段。

共享渲染器不是通用状态机。`complex_plane` 和 `function_graph` 必须使用空的 `controls` 与
`test_actions`；`interval_line` 的安全默认值也同样是空数组。唯一可用的非空控件对只适用于
右端点为 `open` 且 `maximum` 为 null 的 `interval_line`：

```json
"controls": [{"kind": "toggle", "control_id": "show-supremum", "label": "显示上确界", "default_value": false}],
"test_actions": [{"action": "toggle", "control_id": "show-supremum", "value": true, "expected_text": "上确界"}]
```

该开关只显示上确界标记，不能改变端点、区间或最大元。该限制只适用于 `interval_line`；
`generated_html` 可以使用 schema 声明的 toggle 或 range 控件，并在其 html 中标记对应的
`data-component-control`。如果上一候选试图改变端点开闭，应改成静态规格、上述固定开关，或合规的
`generated_html`。

如果反馈表明当前证据不足以修成这个精确契约，请改为有效的 `spec=null` 五键决定，而不是
输出近似 schema 或补写教材外事实。

## 上一轮生成错误（如有）

{{GENERATION_ERROR}}
必须保持任务、知识单元、ContextPack 和教材来源绑定；如果无法给出安全且有教学价值的组件，可返回
`spec=null` 和明确 `not_needed_reason`。

函数图像只能使用变量 `x`、数字、基本运算、括号和 `abs/cos/exp/log/sin/sqrt/tan`，并需修正定义域、端点、
采样点或排除点，直到不会在无定义位置绘制或连线。

## 知识单元

{{KNOWLEDGE_UNIT_CONTEXT}}

## 已接受讲解

{{ACCEPTED_CONTENT}}

## 受限 ContextPack

{{CONTEXT_PACK}}

## 组件任务

{{COMPONENT_TASK}}

## 上一候选

{{CANDIDATE_ARTIFACT}}

## 硬检查

{{HARD_CHECK_RESULT}}

## 浏览器 sandbox

{{SANDBOX_REPORT}}

## 语义 Critic

{{CRITIQUE_RESULT}}
