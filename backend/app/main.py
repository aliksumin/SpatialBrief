from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import upload

app = FastAPI(title="OMRT Vector Engine API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"message": "OMRT Vector Engine API Running"}
