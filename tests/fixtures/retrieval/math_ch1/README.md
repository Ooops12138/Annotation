# 数学分析第 1 章检索开发基线

`corpus.jsonl` 冻结当前已解析的 25 页、1,206 个原始 SourceBlock（document_id 为
`srcdoc-acb412de0faf0abc`），保留 text_hash、页/块索引，并另存实际文本的 SHA256。
三种检索使用完全相同的语料，不调整解析器、中文分词或块粒度。

`questions.json` 包含 40 个有答案问题：keyword、paraphrase、conditions、comparison 各 10 个；
另有 8 个 unanswerable 问题。每个 qrel 保存来源的实际文本 SHA256，加载时同时检查整份语料的 SHA256。
标签在观察真实检索排名之前依据教材整理，未经独立人工复核，不是正式人工金标。

- 2：直接支持解答，或是完整解答所需的公式/定义碎片；关键词题也包括直接定位主题的段落。
- 1：部分支持或补充上下文。
- 0：无关；未标注的块也按 0 计，因此遗漏相关块会影响绝对分数。

Recall/Hit/MRR 将 grade>0 视为相关；nDCG 使用 2^grade-1。每题按 source_ref 去重。
跨块题按各相关块计算普通检索指标，不额外声称已经召回完整证明或完整答案。
无答案问题用于观察误召回，首版不校准相似度阈值。

从仓库根目录运行：

```powershell
.venv/Scripts/python.exe scripts/evaluate_retrieval.py --embedding-provider local --output-dir results/retrieval/local
```

已缓存模型可使用 `--offline`。程序输出逐题结果与汇总，另启动新 Python 进程验证所有向量均可复用。
报告包含实际模型 revision、依赖版本和完整复现命令。错误轮次与降级分别计数，不以故障冒充无答案拒答。
更改语料或标注时需明确创建新的基线版本，并重新生成报告，不在观察检索效果后悄悄修改本版标签。
