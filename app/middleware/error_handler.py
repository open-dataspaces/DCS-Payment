"""Error Handler Middleware"""
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger
from app.core.custom_exceptions import (
    ApplicationError,
    map_exception_to_http_status,
)

logger = get_logger(__name__)


def _serialize_value(value: Any) -> Any:
    """非JSON直列化可能な値を文字列に変換"""
    if isinstance(value, Decimal):
        return str(value)
    elif isinstance(value, Exception):
        return str(value)
    elif isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    return value


def _sanitize_errors(errors: list) -> list:
    """バリデーションエラーをJSON直列化可能な形式に変換"""
    sanitized = []
    for error in errors:
        sanitized_error = {}
        for key, value in error.items():
            if key == 'url':
                # urlフィールドは削除
                continue
            sanitized_error[key] = _serialize_value(value)
        sanitized.append(sanitized_error)
    return sanitized


def add_exception_handlers(app: FastAPI):
    """Add exception handlers to FastAPI app"""

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """バリデーションエラーのハンドリング"""
        # エラーをJSON直列化可能な形式に変換
        errors = _sanitize_errors(exc.errors())

        logger.error("Validation error", extra={"errors": errors})

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "type": "validation_error",
                "title": "Request Validation Error",
                "detail": errors,
                "status": 422,
                "instance": request.url.path
            }
        )
    
    @app.exception_handler(ApplicationError)
    async def application_exception_handler(request: Request, exc: ApplicationError):
        """アプリケーション固有のエラーのハンドリング"""
        status_code, error_code, message = map_exception_to_http_status(exc)
        
        logger.error(
            "Application error",
            extra={
                "error_code": error_code,
                "message": message,
                "exception_class": exc.__class__.__name__,
                "status_code": status_code,
                "path": request.url.path,
                "details": exc.details
            }
        )
        
        return JSONResponse(
            status_code=status_code,
            content={
                "type": error_code.lower(),
                "title": "Application Error",
                "detail": message,
                "status": status_code,
                "instance": request.url.path
            }
        )
    
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """HTTPExceptionのハンドリング（統一形式）"""
        
        error_type_map = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "unprocessable_entity",
            500: "internal_server_error"
        }
        
        title_map = {
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            409: "Conflict",
            422: "Unprocessable Entity",
            500: "Internal Server Error"
        }
        
        error_type = error_type_map.get(exc.status_code, "error")
        title = title_map.get(exc.status_code, "Error")
        
        logger.warning(
            "HTTP exception",
            extra={"status_code": exc.status_code, "detail": exc.detail}
        )
        
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": error_type,
                "title": title,
                "detail": exc.detail,
                "status": exc.status_code,
                "instance": request.url.path
            }
        )
    
    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
        """データベースエラーのハンドリング"""
        logger.error(
            "Database error",
            extra={
                "error": str(exc),
                "method": request.method,
                "path": request.url.path,
                "tracking_id": request.headers.get("X-TrackingId", ""),
            },
            exc_info=True,
        )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "type": "database_error",
                "title": "Database Error",
                "detail": "Database error occurred",
                "status": 500,
                "instance": request.url.path
            }
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """一般的なエラーのハンドリング"""
        logger.error(
            "Unexpected error",
            extra={
                "error": str(exc),
                "error_type": type(exc).__name__,
                "method": request.method,
                "path": request.url.path,
                "tracking_id": request.headers.get("X-TrackingId", ""),
            },
            exc_info=True,
        )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "type": "internal_error",
                "title": "Internal Server Error",
                "detail": "",  # セキュリティのため詳細は非公開
                "status": 500,
                "instance": request.url.path
            }
        )