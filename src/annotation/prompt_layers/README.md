# 可选学科增强

`../prompts/` 提供独立完整的基础流程。这里仅存放可选的学科规则与教学方法，当前内置 mathematics（数学）。未选择学科时，提示词加载不读取此目录的配置或规则。

## 使用

- 页面：生成前在“学科增强”中选择“通用 · 不指定学科”或已注册学科。
- API：GET /api/prompt-profiles 列出本地学科；POST /api/runs 可传 `"prompt_profile": "mathematics"`，省略或传 null 为通用模式。
- Python：`run_minimal_workflow(..., prompt_profile="mathematics")`；单个提示词使用 `load_prompt(..., profile="mathematics")`。
- 选择按运行保存，续跑读取原生检查点中的原选择，不受页面当前选择影响。

## 添加自定义学科

新建目录，例如 physics/，放入 domain_rules.md 和/或 teaching_guidance.md。在 manifest.json 的 profiles 中注册如下条目：

```json
{
  "physics": {
    "label": "物理",
    "slots": {
      "DOMAIN_RULES": "physics/domain_rules.md",
      "TEACHING_GUIDANCE": "physics/teaching_guidance.md"
    }
  }
}
```

以上是添加到 profiles 中的条目；完整 manifest 保留 `"version": 2`。刷新页面后新学科会出现在选择列表，无需修改工作流代码。

两个槽位均可省略。只有模板声明的槽位会注入；正文教学 Critic 仅接收教学指导，继续只评教学质量。缺少槽位沿用通用提示词；声明的文件丢失、非法槽位、未知学科或路径越界则报错。规则按原文注入，`{{...}}` 不再次展开。

DOMAIN_RULES 描述本学科跨阶段适用的约束，必要时写明“生成时”“核查时”等适用条件；TEACHING_GUIDANCE 描述本学科特有的讲解、规划和教学评价方法。通用深度档位、来源边界、检索语言、输出 schema、工具/渲染协议及审核路由仍归 prompts/ 和程序所有。自定义规则不能改变程序校验和发布门。

一次运行最多选择一个包，不支持代码执行、继承、多包叠加或在线安装。效果需另行通过教材对照评估。
