"""Health Check Endpoints for Kubernetes Probes"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.session import get_db
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health", include_in_schema=False)
async def health_check():
    """
    Liveness Probe endpoint.

    Kubernetesのliveness probeで使用。
    アプリケーションが起動しているかを確認する軽量なエンドポイント。
    データベース接続は確認しない（readinessで確認）。

    Returns:
        200: アプリケーションが正常に動作中
    """
    return {"status": "ok"}


@router.get("/readiness", include_in_schema=False)
async def readiness_check(db: Session = Depends(get_db)):
    """
    Readiness Probe endpoint.

    Kubernetesのreadiness probeで使用。
    アプリケーションがトラフィックを受け入れる準備ができているかを確認。
    データベース接続を含む依存サービスの状態を確認する。

    Returns:
        200: アプリケーションがトラフィックを処理可能
        503: サービスが利用不可（データベース接続エラーなど）
    """
    checks = {
        "database": "unknown"
    }

    try:
        # データベース接続確認
        db.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as e:
        logger.error("Database health check failed", extra={"error": str(e)})
        checks["database"] = "unhealthy"
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "checks": checks
            }
        )

    return {
        "status": "ok",
        "checks": checks
    }
