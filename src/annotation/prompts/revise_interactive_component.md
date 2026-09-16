# A-004 受控交互组件修订

根据下列硬检查、sandbox 和语义反馈，修订一个 `InteractiveComponentDraft`。
输出必须只包含调用方 schema 所允许的结构化数据，不能输出或嵌入任何可执行前端代码、URL 或任意表达式 API。
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
