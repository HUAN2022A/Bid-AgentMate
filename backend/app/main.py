"""FastAPI 入口：API 路由 + 前端静态文件托管（Q28：无 nginx，FastAPI 直托）。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, chapters, delivery, materials, outline as outline_api, projects
from app.core.config import settings
from app.core.database import Base, engine, SessionLocal
from app.core.security import hash_password
from app.models import (  # noqa: F401 注册表模型
    chapter,
    file_object,
    material,
    outline,
    project,
    scoring_item,
    tech_requirement,
    tender_file,
    user,
)
from app.models.user import User

app = FastAPI(title="Bid-AgentMate", version="0.1.0")

# 开发期 vite dev server 跨域；生产同源托管无影响
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(outline_api.router)
app.include_router(chapters.router)
app.include_router(delivery.router)
app.include_router(materials.router)


@app.on_event("startup")
def bootstrap():
    # 阶段 1 演示便利：自动建表（Alembic 迁移脚本随后补，生产部署走 Alembic）
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(
                User(
                    username=settings.bootstrap_admin_username,
                    display_name="管理员",
                    password_hash=hash_password(settings.bootstrap_admin_password),
                )
            )
            db.commit()
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok"}


# 前端构建产物托管（frontend/dist 存在时）
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    # 静态资源（js/css/图片）直接挂载
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    # SPA 回退：其余非 /api 的 GET 一律返回 index.html，前端路由接管
    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        candidate = _dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
