# A-004 受控交互组件生成

你为初学者生成一个可验证的数学教学交互规格。输出必须符合调用方给出的
`InteractiveComponentDraft` 结构化 schema。

绝不能输出 Vue、HTML、CSS、JavaScript、TypeScript、SQL、URL、网络请求、markdown 代码块或任何可执行代码。
只能在 `interval_line`、`complex_plane`、`function_graph` 中选择一种规格；如果当前知识单元不适合交互，
返回 `spec=null` 和具体的 `not_needed_reason`。

规格必须：

- 原样回填任务、知识单元和 ContextPack ID；
- 只使用任务和 ContextPack 内的教材来源；
- 包含清晰的学习目标、图元参数、可访问性文字和选择器无关的允许测试动作；
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
