# P0 POC 输入与验收基线

## 冻结输入

- 学科：数学分析
- 教材文件：`books/数学分析第1章.pdf`
- 章节范围：第 1 章《实数系与复数系》，共 25 页
- 输入类型：文本型 PDF；页面渲染可读，PyMuPDF 可提取页/块文本；运行时仍保留替换字符检测
- 学习对象：具备高中代数基础、正在学习数学分析的本科初学者
- P0 学习目标：掌握实数的域/序结构、区间和整数基础；理解有理数/无理数、上界/上确界与完全性；能用复平面解释复数及其绝对值
- 授权说明：该 PDF 由项目维护者放入本地 `books/`，目录被 Git 忽略；提交前须确认本人拥有处理和展示权限

## 可复现命令

```powershell
$env:MODEL_PROVIDER = "mock"
python -m pytest -q
Invoke-RestMethod http://127.0.0.1:8000/api/source-preview
Invoke-RestMethod "http://127.0.0.1:8000/api/source-search?query=上确界"
```

真实模型运行需要在本地环境设置 `MODEL_PROVIDER=deepseek`、`MODEL_NAME` 和 `MODEL_API_KEY`；`MODEL_BASE_URL` 可省略（工厂默认 `https://api.deepseek.com/v1`），密钥不写入仓库。

样例生成脚本：`python scripts/generate_deepseek_document_draft.py`。它读取 PDF 的页/块文本，要求 DeepSeek 返回 `DocumentDraft` JSON，并把成功结果写入本地 `storage/`；网络/API 失败不会影响 Mock 离线回归。

## 当前已知风险

1. PDF 页面视觉内容可读；若实际运行出现 `�` 替换字符，系统保留原始抽取结果并在审核中生成 warning，不把它伪装成 OCR 已完成。
2. SQLite FTS5 是关键词检索，不承诺语义召回；关键定义、公式和例题要用固定查询做人工抽样。
3. 离线 Mock 样例是回归基线，不代表教材事实已自动核验；文档仍保留人工复核 warning。
4. 当前最小 LangGraph 流程只把教材开头的 12 个非空 SourceBlock 送入蓝图和文档生成 agent；因此真实运行主要验证首节（当前为 1.1 引言），不应解读为已覆盖整章。
5. DeepSeek 的 `MODEL_THINKING=disabled` 已作为当前 POC 默认配置，用于减少结构化 JSON 的延迟和截断风险；可通过本地 `.env` 改为 `enabled` 做对照实验。

## 模型调用日志

真实 OpenAI-compatible provider 的每次调用都会追加到 `storage/model-calls.jsonl`。每行包含 agent 名称、输入提示、原始输出、解析结果、思考模式、耗时、token 用量、完成原因和错误；API key 不写入日志。可通过 `MODEL_CALL_LOG_PATH` 指定本地日志路径。
