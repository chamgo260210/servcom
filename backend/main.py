# File: main.py  (work_time_back 레포 루트에 있는 그 main.py 기준)

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app import models
from app.config import get_settings
from app.deps import engine, initialize_database
from app.routers import admin, auth, requests, schedule, users, history, notices, visitors, serials
from app.routers import system

settings = get_settings()
if os.getenv("APP_ENV", "").lower() in {"local", "development", "dev"}:
    models.Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.PROJECT_NAME)

# ✅ CORS: 기본적으로 렌더 배포 + GitHub Pages 등 프런트 도메인을 허용
app.add_middleware(
    CORSMiddleware,
    # allow_origins=settings.CORS_ALLOW_ORIGINS,  # 기존 설정 (환경변수 의존)
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://zolt46.github.io",
        "https://work-time-front.onrender.com",
        "*" # 개발 편의를 위해 당분간 전체 허용 (보안상 추후 제거 필요)
    ],
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_fallback_cors(request, call_next):
    """
    예외 상황에도 CORS 헤더를 덧붙이되, HTTP 상태 코드는 그대로 유지한다.
    """
    origin = request.headers.get("origin")
    allow_all = "*" in settings.CORS_ALLOW_ORIGINS
    allow_origin = "*"
    if not allow_all and settings.CORS_ALLOW_ORIGINS:
        if origin and origin in settings.CORS_ALLOW_ORIGINS:
            allow_origin = origin
        else:
            allow_origin = settings.CORS_ALLOW_ORIGINS[0]
    try:
        response = await call_next(request)
    except HTTPException as exc:  # 그대로 전달하되 헤더만 보강
        response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
    except Exception as exc:  # pragma: no cover - 런타임 방어
        response = JSONResponse({"detail": "internal_server_error"}, status_code=500)
    response.headers.setdefault("Access-Control-Allow-Origin", allow_origin if origin or allow_all else "*")
    response.headers.setdefault("Access-Control-Allow-Headers", "*")
    response.headers.setdefault("Access-Control-Allow-Methods", "*")
    if settings.CORS_ALLOW_CREDENTIALS:
        response.headers.setdefault("Access-Control-Allow-Credentials", "true")
    return response

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(schedule.router)
app.include_router(requests.router)
app.include_router(admin.router)
app.include_router(system.router)
app.include_router(history.router)
app.include_router(notices.router)
app.include_router(visitors.router)
app.include_router(serials.router)


@app.on_event("startup")
def warmup_database() -> None:
    initialize_database()


@app.get("/")
def root():
    return {"message": "Dasan Shift Manager API"}

@app.get("/cors-test")
def cors_test():
    return {"cors": "ok"}
