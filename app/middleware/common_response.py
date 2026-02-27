"""共通セキュリティヘッダーミドルウェア"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from typing import Callable, Awaitable


class CommonSecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    すべてのレスポンスに共通のセキュリティおよびキャッシュ制御ヘッダーを付与するミドルウェア
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:

        # リクエストの処理を続行し、レスポンスを取得
        response = await call_next(request)

        # 1. Server ヘッダーを削除（存在する場合）
        if "server" in response.headers:
            del response.headers["server"]

        # 2. CSP（コンテンツセキュリティポリシー）の設定
        csp_policy = [
            "default-src 'self'",            # 基本は自ドメインのリソースのみ許可
            "script-src 'self'",             # スクリプトは自ドメインのみ
            "style-src 'self' 'unsafe-inline'",  # スタイルは自ドメイン + インライン許可
            "img-src 'self' data:",          # 画像は自ドメイン + data URI
            "font-src 'self'",               # フォントは自ドメインのみ
            "object-src 'none'",             # <object> タグを禁止
            "frame-ancestors 'none'",        # クリックジャッキング対策
            "base-uri 'self'",               # base タグの制限
            "form-action 'self'",            # フォーム送信先の制限
            "upgrade-insecure-requests",     # HTTPリクエストをHTTPSにアップグレード
        ]

        # 3. ヘッダーの追加/更新
        # 注意: X-TrackingId は TrackingMiddleware で設定されるため、ここでは設定しない
        response.headers.update({
            # キャッシュ制御
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",

            # セキュリティヘッダー
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",

            # CSP
            "Content-Security-Policy": "; ".join(csp_policy),
        })

        return response
