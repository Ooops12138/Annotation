# Annotation

第一版 Demo 的 T-000 技术骨架：FastAPI + Vue 3/Vite + Tailwind CSS（shadcn-vue 风格源码组件）+ Pydantic artifact。

## 本地启动

前置环境：Python 3.12、uv，以及 Node.js 20（自带 npm）。本项目固定使用 uv 管理 Python 环境和依赖，使用 npm 管理前端依赖。

### 后端（Python 3.12）

```powershell
# uv sync --extra dev
uv sync --extra dev --link-mode copy
uv run uvicorn annotation.main:app --reload --app-dir src
```

`uv sync --extra dev` 会根据 `pyproject.toml` 创建或同步项目的 `.venv`，并安装运行和开发依赖；`uv run` 会在该环境中执行命令，因此不需要手动激活虚拟环境。

API：`http://127.0.0.1:8000/health`，交互文档：`http://127.0.0.1:8000/docs`。

### 前端（Node 20 + npm）

```powershell
cd web
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。前端通过 `VITE_API_BASE_URL` 访问后端，默认值为 `http://127.0.0.1:8000`。

Demo 现在会读取 `books/` 中的第一个 PDF，执行教材导入、SourceBlock 生成、Learning Blueprint 生成、Document IR 生成和基础审核。默认生产运行读取根目录 `.env`；测试通过环境变量强制使用 Mock Provider。

`.env` 是本机真实配置（包含密钥，不提交 Git），`.env.example` 是可提交的配置模板。两者都需要保留：新环境可从 `.env.example` 复制出 `.env` 后填写密钥。

运行最小流程：

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/workflow/run
```

返回值包含教材、blueprint、Document IR、审核状态、source block 数量、provider/model/base_url/config_version/duration_ms、警告和错误列表。

注意：当前教材 PDF 的 PyMuPDF 文本提取包含替换字符，运行结果会把这个问题作为审核警告保留；这不等同于 OCR 已完成。若要用于正式内容验收，应先提供文本编码正常的 PDF 或增加 OCR 适配。

模型切换：`MODEL_PROVIDER=mock` 使用确定性的离线回归样例；`MODEL_PROVIDER=deepseek` 使用 OpenAI-compatible DeepSeek endpoint（还需要 `MODEL_NAME` 和 `MODEL_API_KEY`；`MODEL_BASE_URL` 可省略，默认是 `https://api.deepseek.com/v1`）。详见 [`docs/poc-scope.md`](docs/poc-scope.md)。

每次真实模型调用的 agent 名称、输入提示、原始输出、解析结果、耗时和错误会追加记录到 `storage/model-calls.jsonl`，不记录 API key。DeepSeek 默认使用 `MODEL_THINKING=disabled`；如需测试思考模式，可在本地 `.env` 中改为 `enabled`。

## PDF 解析预览

将文本型 PDF 放入仓库根目录的 `books/`（该目录已被 Git 忽略，教材不会上传），启动后端后访问 `http://127.0.0.1:8000/api/source-preview`。接口使用 PyMuPDF 生成页/块级 `SourceBlock`，保留页码、块序号、坐标、文本 hash、解析器版本和 `source_ref`。

解析器不会执行 OCR 或静默修正文案；如果 PDF 字体编码导致乱码，原始提取结果会被保留并可在审核阶段标记。
