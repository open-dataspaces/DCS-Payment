"""FastAPI Application Entry Point"""
from app.core.logging import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.v1.router import api_router
from app.api.v1.endpoints import health
from app.api.v1.response_headers import COMMON_RESPONSE_HEADERS, GET_RESPONSE_HEADERS
from app.core.config import settings
from app.core.events import create_start_app_handler, create_stop_app_handler
from app.middleware import (
    TrackingMiddleware,
    ContentTypeMiddleware,
    CommonSecurityHeadersMiddleware,
    add_exception_handlers,
)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="""
## 変更履歴
### version 1.0.0  2026/02
""",
    version=settings.VERSION,
    log_level="info",
    access_log=True,
    openapi_tags=[
        {
            "name": "精算決済",
            "description": "利用料モデル登録、更新、削除、取得、データ交換完了通知、請求、支払情報取得を行うAPI群です。",
        }
    ]

)


# ========================================
# Middleware（登録順序：後から登録したものが先に実行）
# ========================================
# CORS設定: allow_credentials=Trueの場合、allow_origins=["*"]は使用不可
# セキュリティ上、具体的なオリジンを指定すること
cors_origins = settings.ALLOWED_HOSTS if settings.ALLOWED_HOSTS else []
if "*" in cors_origins and settings.IS_PRODUCTION:
    logger.warning("CORS: Wildcard origin '*' is not recommended in production")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True if cors_origins and "*" not in cors_origins else False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(CommonSecurityHeadersMiddleware)
app.add_middleware(ContentTypeMiddleware)
app.add_middleware(TrackingMiddleware)

# ========================================
# Exception Handlers
# ========================================
add_exception_handlers(app)

# ========================================
# Events
# ========================================
app.add_event_handler("startup", create_start_app_handler(app))
app.add_event_handler("shutdown", create_stop_app_handler(app))

# ========================================
# Routers
# ========================================
app.include_router(health.router)  # Health check endpoints (no prefix, not in OpenAPI)
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


# ========================================
# OpenAPI Schema Customization
# ========================================
from fastapi.openapi.utils import get_openapi


def custom_openapi():
    """OpenAPIスキーマにすべてのレスポンスへ共通ヘッダを追加"""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    paths = openapi_schema.get("paths", {})

    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if method in ["get", "post", "put", "delete", "patch"]:
                responses = operation.get("responses", {})
                for status_code, response in responses.items():
                    # 既存のheadersを取得、なければ空dictを作成
                    headers = response.get("headers", {})
                    # 共通ヘッダをマージ（既存のヘッダは上書きしない）
                    for header_name, header_def in COMMON_RESPONSE_HEADERS.items():
                        if header_name not in headers:
                            headers[header_name] = header_def
                    # GETメソッドのみETag/Last-Modifiedを追加
                    if method == "get":
                        for header_name, header_def in GET_RESPONSE_HEADERS.items():
                            if header_name not in headers:
                                headers[header_name] = header_def
                    response["headers"] = headers

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        server_header=False
    )