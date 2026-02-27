"""共通レスポンスヘッダ定義（OpenAPIドキュメント用）"""

# OpenAPIドキュメントに表示するレスポンスヘッダ定義
# 実際のヘッダ値はミドルウェア(CommonSecurityHeadersMiddleware, TrackingMiddleware)で設定
COMMON_RESPONSE_HEADERS = {
    "Cache-Control": {
        "description": "キャッシュ制御指示",
        "schema": {"type": "string", "example": "no-cache, no-store, must-revalidate"},
    },
    "X-TrackingId": {
        "description": "リクエストトラッキング用UUID",
        "schema": {"type": "string", "example": "550e8400-e29b-41d4-a716-446655440000"},
    },
    "Content-Security-Policy": {
        "description": "XSS対策",
        "schema": {"type": "string", "example": "default-src 'self'"},
    },
    "X-Content-Type-Options": {
        "description": "MIMEスニッフィング防止",
        "schema": {"type": "string", "example": "nosniff"},
    },
    "Strict-Transport-Security": {
        "description": "HTTPS強制",
        "schema": {"type": "string", "example": "max-age=63072000; includeSubDomains"},
    },
}

# GETエンドポイント用の追加ヘッダ（リソース取得時のキャッシュ制御）
GET_RESPONSE_HEADERS = {
    "ETag": {
        "description": "リソースのバージョン識別子",
        "schema": {"type": "string", "example": '"abc123etagvalue"'},
    },
    "Last-Modified": {
        "description": "リソースの最終更新日時",
        "schema": {"type": "string", "example": "Wed, 30 Jul 2025 01:00:00 GMT"},
    },
}
