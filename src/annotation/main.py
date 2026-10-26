from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from annotation.api.routes import router

@asynccontextmanager
async def lifespan(app):
    from annotation.api.persistence_service import repository
    repo = repository()
    try:
        for run in repo.list_runs():
            if run["status"] in {"running", "stopping"}:
                metadata = dict(run.get("metadata") or {})
                progress = dict(metadata.get("progress") or {})
                progress["status"] = "interrupted"
                repo.update_run(run["run_id"], status="interrupted", error="execution_process_exited",
                                metadata={**metadata, "progress": progress})
    finally:
        repo.close()
    yield

app = FastAPI(lifespan=lifespan, title="Annotation API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("annotation.main:app", host="127.0.0.1", port=8000, reload=True)
