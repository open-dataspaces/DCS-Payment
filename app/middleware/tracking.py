"""トラッキングID・リクエストロギングミドルウェア"""
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger, set_request_id

logger = get_logger(__name__)


class TrackingMiddleware(BaseHTTPMiddleware):
    """X-TrackingIdをレスポンスヘッダーに付与し、処理時間を記録"""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # X-TrackingIdを取得
        tracking_id = request.headers.get("X-TrackingId", "")
        
        # ロギング用にリクエストIDを設定
        if tracking_id:
            set_request_id(tracking_id)
        
        # リクエスト情報をログ
        logger.info(
            "Request received",
            extra={
                "method": request.method,
                "path": request.url.path,
                "client_host": request.client.host if request.client else "unknown",
                "tracking_id": tracking_id
            }
        )
        
        # 次の処理を実行
        response = await call_next(request)
        
        # 処理時間を計算
        process_time = time.time() - start_time
        
        # レスポンス情報をログ
        logger.info(
            "Response sent",
            extra={
                "status_code": response.status_code,
                "process_time_sec": round(process_time, 3)
            }
        )
        
        # レスポンスヘッダーに追加
        if tracking_id:
            response.headers["X-TrackingId"] = tracking_id
        
        return response


class DebugLoggingMiddleware(BaseHTTPMiddleware):
    """デバッグ用：リクエストヘッダをログに記録"""

    async def dispatch(self, request: Request, call_next):
        logger.debug(
            "Debug request info",
            extra={
                "path": request.url.path,
                "headers": dict(request.headers)
            }
        )
        response = await call_next(request)
        return response