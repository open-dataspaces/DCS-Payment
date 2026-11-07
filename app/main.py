"""FastAPI Application Entry Point"""
import os

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
    UnhandledExceptionMiddleware,
    add_exception_handlers,
)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="""
## 変更履歴
### version 1.2.0  2026/10
- 利用料モデル取得 API（GET /fee-model/{fee_model_id}）を追加。ETag / Last-Modified による条件付きリクエストに対応
- データ交換状態登録 API・取引金額確定 API（利用料モデルなし）で、取引可否確認 API の事前登録を、当事者・データ ID（利用料モデルなしは決済サービス・金額・税率・税区分）が一致するものから 1 件ずつ引き当てるよう変更（同じデータの取引可否確認を複数回呼んだ場合に、別の与信の取引がまとめて紐付く不具合を修正）。本文の `tracking_id` にはデータ取得の `X-TrackingId` を指定する。取引可否確認の `X-TrackingId` を指定した場合は、事前登録が別のデータ交換に引き当てられた後も 409
- 取引可否確認 API で、事前登録がデータ交換に引き当てられた後に同じ `X-TrackingId` で再送した場合は 409（NP に二重に取引登録しない）。取引可否確認の `X-TrackingId` を取引テーブルの `eligibility_tracking_id` に保存する（マイグレーション 005）
- 与信が必要な決済サービスの取引では、取引可否確認（与信 OK）を経ていないデータ交換状態登録を拒否するよう変更
- 取引金額確定 API（利用料モデルなし）の本文に `tracking_id`（必須。データ取得の `X-TrackingId`）を追加し、同じ `tracking_id` での再送を冪等にした。取引可否確認時の決済サービス・金額と照合するよう変更
- 支払予定額・請求予定額 API の期間判定にデータ交換完了日時（`completed_at`）を使用。L2 ログ未確定・失敗・キャンセル済みの取引を既定で含めないよう変更（`L2_HTTP_STATUS_FILTER_ENABLED` の既定値を true に変更）
- 取引の状態登録・更新・金額確定に行ロックによる排他制御を追加。ロック待ちが `DB_LOCK_TIMEOUT` 秒を超えた場合は 503（`Retry-After` 付き）
- 認可確認を L3 の認可判定 API 経由に変更（設定項目を `L3_BASE_URL`・`L3_API_KEY`・`AUTHZ_STORE_ID` に変更）
- API キーのヘッダ名表記を `X-Payment-API-Key` に統一。`X_PAYMENT_API_KEY` 未設定時はすべてのリクエストを拒否
- CORS の許可オリジンの設定名を `ALLOWED_HOSTS` から `CORS_ALLOWED_ORIGINS` に変更（旧名も読み込む）
- 税込金額の丸めを一元化し、DB の上限を超える金額を拒否（リクエストの検証で 422。利用料モデルの更新では 400）
- データ交換状態更新 API で取引当事者以外のオペレータを 400 で拒否
- 各テーブルの主キーに DB 側の既定値 `gen_random_uuid()` を設定（マイグレーション 005）。SQL で主キーを省略して登録できる
### version 1.0.0  2026/02
- 初版
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
cors_origins = settings.CORS_ALLOWED_ORIGINS if settings.CORS_ALLOWED_ORIGINS else []
if os.environ.get("ALLOWED_HOSTS") and not os.environ.get("CORS_ALLOWED_ORIGINS"):
    logger.warning("ALLOWED_HOSTS is deprecated; use CORS_ALLOWED_ORIGINS for the CORS allowed origins")
if "*" in cors_origins and settings.IS_PRODUCTION:
    logger.warning("CORS: Wildcard origin '*' is not recommended in production")

# 想定外の例外を最も内側で 500 応答に変換し、外側のミドルウェアでヘッダを付ける
app.add_middleware(UnhandledExceptionMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True if cors_origins and "*" not in cors_origins else False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
# ContentTypeMiddleware の 400 応答にもセキュリティヘッダ・X-TrackingId が付くよう、
# CommonSecurityHeadersMiddleware・TrackingMiddleware より内側に登録する
app.add_middleware(ContentTypeMiddleware)
app.add_middleware(CommonSecurityHeadersMiddleware)
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