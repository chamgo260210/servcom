# File: /backend/main.py
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app import models
from app.config import get_settings
from app.deps import engine, initialize_database
from app.routers import admin, auth, data_management, history, notices, requests, schedule, serials, system, users, visitors

settings = get_settings()
if settings.APP_ENV.lower() in {"local", "development", "dev"}:
    models.Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.PROJECT_NAME, root_path=settings.API_ROOT_PATH)

if not settings.TRUST_ALL_HOSTS and settings.TRUSTED_HOSTS:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)

if settings.CORS_ALLOW_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOW_ORIGINS,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def add_fallback_cors(request, call_next):
    """예외 상황에서도 상태 코드는 유지한 채 최소 CORS 헤더만 부여한다."""
    origin = request.headers.get("origin")
    allow_all = settings.CORS_ALLOW_ORIGINS == ["*"]
    allow_origin = "*"
    if settings.CORS_ALLOW_ORIGINS and not allow_all:
        if origin and origin in settings.CORS_ALLOW_ORIGINS:
            allow_origin = origin
        else:
            allow_origin = settings.CORS_ALLOW_ORIGINS[0]

    try:
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
    except Exception:
        response = JSONResponse({"detail": "internal_server_error"}, status_code=500)

    if settings.CORS_ALLOW_ORIGINS:
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
app.include_router(data_management.router)


@app.on_event("startup")
def warmup_database() -> None:
    initialize_database()


@app.get("/")
def root():
    return {"message": "Dasan Shift Manager API"}


@app.get("/cors-test")
def cors_test():
    return {"cors": "ok", "env": os.getenv("APP_ENV", "production")}
