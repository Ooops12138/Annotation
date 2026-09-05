from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from annotation.api.routes import router

app = FastAPI(title="Annotation API", version="0.1.0")
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
