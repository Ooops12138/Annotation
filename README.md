# Annotation

第一版 Demo 的 T-000 技术骨架：FastAPI + Vue 3/Vite + Tailwind CSS（shadcn-vue 风格源码组件）+ Pydantic artifact。

## 本地启动

### 后端（Python 3.12）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn annotation.main:app --reload --app-dir src
```

API：`http://127.0.0.1:8000/health`，交互文档：`http://127.0.0.1:8000/docs`。

### 前端（Node 20 + pnpm）

```powershell
cd web
pnpm install
pnpm dev
```

打开 `http://127.0.0.1:5173`。前端通过 `VITE_API_BASE_URL` 访问后端，默认值为 `http://127.0.0.1:8000`。

当前仅使用 fixture 文档，不接入真实教材、真实模型或完整 Agent 流程。
