"""Content-Type検証ミドルウェア"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from typing import Callable, Awaitable


class ContentTypeMiddleware(BaseHTTPMiddleware):
    """Content-Typeがapplication/json以外なら400エラー"""

    # 許可するContent-Typeのプレフィックス
    ALLOWED_CONTENT_TYPES = ("application/json",)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in ("POST", "PUT", "PATCH"):
            content_type = request.headers.get("content-type", "")
            # Content-Typeが存在し、かつapplication/jsonで始まらない場合のみエラー
            # charset指定（例: application/json; charset=utf-8）も許可
            if content_type and not content_type.startswith(self.ALLOWED_CONTENT_TYPES):
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Content-Type must be application/json"}
                )
        return await call_next(request)
