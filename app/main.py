"""Scholar Pulse: FastAPI Application Entrypoint.

Initializes FastAPI app, configures CORS middleware, registers REST API routes,
and mounts static frontend assets at root path '/'.
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import router as api_router
from app.store import store

app = FastAPI(
    title="Scholar Pulse",
    description="Proactive intelligence layer for Scholar AI detecting student struggle and pace drift.",
    version="1.0.0",
)

# 1. CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Register REST API Routes
app.include_router(api_router)

# 3. Mount Static Frontend Assets
static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static_assets")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


@app.on_event("startup")
def on_startup() -> None:
    """Ensure state store is initialized on application startup."""
    if hasattr(store, "reset"):
        store.reset()
