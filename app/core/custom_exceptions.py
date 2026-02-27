"""
Custom Exceptions - カスタム例外定義

アプリケーション固有の例外を定義し、外部ライブラリの例外を隠蔽
"""

from typing import Optional, Any

# ========================================
# ベース例外
# ========================================

class ApplicationError(Exception):
    """アプリケーションのベース例外"""
    
    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[dict] = None
    ):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)


# ========================================
# Repository層の例外
# ========================================

class RepositoryError(ApplicationError):
    """Repository層のベース例外"""
    pass


class DatabaseError(RepositoryError):
    """データベース接続・操作エラー"""
    
    def __init__(
        self,
        message: str = "Database error occurred",
        original_error: Optional[Exception] = None,
        **kwargs
    ):
        super().__init__(message, error_code="DATABASE_ERROR", **kwargs)
        self.original_error = original_error


class RecordNotFoundError(RepositoryError):
    """レコードが見つからない"""
    
    def __init__(
        self,
        entity: str,
        identifier: Any,
        **kwargs
    ):
        message = f"{entity} not found: {identifier}"
        super().__init__(message, error_code="NOT_FOUND", **kwargs)
        self.entity = entity
        self.identifier = identifier


class DuplicateRecordError(RepositoryError):
    """重複レコードエラー"""
    
    def __init__(
        self,
        entity: str,
        message: Optional[str] = None,
        **kwargs
    ):
        message = message or f"{entity} already exists"
        super().__init__(message, error_code="DUPLICATE_RECORD", **kwargs)
        self.entity = entity


class IntegrityError(RepositoryError):
    """データ整合性エラー"""
    
    def __init__(
        self,
        message: str = "Data integrity error occurred",
        constraint: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, error_code="INTEGRITY_ERROR", **kwargs)
        self.constraint = constraint


# ========================================
# Service層の例外
# ========================================

class ServiceError(ApplicationError):
    """Service層のベース例外"""
    pass


class BusinessRuleViolationError(ServiceError):
    """ビジネスルール違反"""
    
    def __init__(
        self,
        message: str,
        rule: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, error_code="BUSINESS_RULE_VIOLATION", **kwargs)
        self.rule = rule


class ValidationError(ServiceError):
    """検証エラー"""
    
    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, error_code="VALIDATION_ERROR", **kwargs)
        self.field = field


class OptimisticLockError(ServiceError):
    """楽観的ロックエラー（バージョン不一致）"""
    
    def __init__(
        self,
        entity: str,
        expected_version: int,
        actual_version: int,
        **kwargs
    ):
        message = (
            f"Version mismatch for {entity}. "
            f"Expected: {expected_version}, Actual: {actual_version}"
        )
        super().__init__(message, error_code="OPTIMISTIC_LOCK_ERROR", **kwargs)
        self.entity = entity
        self.expected_version = expected_version
        self.actual_version = actual_version


class ResourceConflictError(ServiceError):
    """リソース競合エラー"""
    
    def __init__(
        self,
        message: str,
        resource: Optional[str] = None,
        **kwargs
    ):
        super().__init__(message, error_code="RESOURCE_CONFLICT", **kwargs)
        self.resource = resource


# ========================================
# API層の例外（HTTPエラーへのマッピング用）
# ========================================

class APIError(ApplicationError):
    """API層のベース例外"""
    
    def __init__(
        self,
        message: str,
        status_code: int = 500,
        **kwargs
    ):
        super().__init__(message, **kwargs)
        self.status_code = status_code


class BadRequestError(APIError):
    """400 Bad Request"""
    
    def __init__(self, message: str = "Invalid request", **kwargs):
        super().__init__(message, status_code=400, error_code="BAD_REQUEST", **kwargs)


class UnauthorizedError(APIError):
    """401 Unauthorized"""
    
    def __init__(self, message: str = "Authentication required", **kwargs):
        super().__init__(message, status_code=401, error_code="UNAUTHORIZED", **kwargs)


class ForbiddenError(APIError):
    """403 Forbidden"""
    
    def __init__(self, message: str = "Access denied", **kwargs):
        super().__init__(message, status_code=403, error_code="FORBIDDEN", **kwargs)


class NotFoundError(APIError):
    """404 Not Found"""
    
    def __init__(self, message: str = "Resource not found", **kwargs):
        super().__init__(message, status_code=404, error_code="NOT_FOUND", **kwargs)


class ConflictError(APIError):
    """409 Conflict"""

    def __init__(self, message: str = "Resource conflict", **kwargs):
        super().__init__(message, status_code=409, error_code="CONFLICT_ERROR", **kwargs)


class InternalServerError(APIError):
    """500 Internal Server Error"""
    
    def __init__(self, message: str = "Internal server error occurred", **kwargs):
        super().__init__(message, status_code=500, error_code="INTERNAL_SERVER_ERROR", **kwargs)


# ========================================
# 例外マッピング
# ========================================

def map_exception_to_http_status(exc: Exception) -> tuple[int, str, str]:
    """
    例外をHTTPステータスコードとエラー情報にマッピング
    
    Args:
        exc: 発生した例外
        
    Returns:
        tuple: (status_code, error_code, message)
    """
    # Repository層の例外
    if isinstance(exc, RecordNotFoundError):
        return (404, "NOT_FOUND", str(exc))
    
    if isinstance(exc, DuplicateRecordError):
        return (409, "DUPLICATE_RECORD", str(exc))
    
    if isinstance(exc, IntegrityError):
        return (409, "INTEGRITY_ERROR", str(exc))
    
    if isinstance(exc, DatabaseError):
        return (500, "DATABASE_ERROR", str(exc))
    
    # Service層の例外
    if isinstance(exc, OptimisticLockError):
        return (409, "OPTIMISTIC_LOCK_ERROR", str(exc))
    
    if isinstance(exc, ResourceConflictError):
        return (409, "RESOURCE_CONFLICT", str(exc))
    
    if isinstance(exc, BusinessRuleViolationError):
        return (400, "BUSINESS_RULE_VIOLATION", str(exc))
    
    if isinstance(exc, ValidationError):
        return (400, "VALIDATION_ERROR", str(exc))
    
    # API層の例外
    if isinstance(exc, APIError):
        return (exc.status_code, exc.error_code or "API_ERROR", str(exc))
    
    # その他の例外
    return (500, "INTERNAL_ERROR", "An unexpected error occurred")


# ========================================
# ユーティリティ関数
# ========================================

def wrap_database_error(func):
    """
    データベース操作をラップし、SQLAlchemyErrorを独自例外に変換するデコレータ
    
    使用例:
        @wrap_database_error
        def create(self, entity):
            self.db.add(entity)
            self.db.flush()
    """
    from functools import wraps
    from sqlalchemy.exc import (
        SQLAlchemyError,
        IntegrityError as SQLIntegrityError,
        DataError,
        OperationalError
    )
    
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except SQLIntegrityError as e:
            # 一意制約違反など
            if "unique" in str(e.orig).lower():
                raise DuplicateRecordError(
                    entity="Record",
                    message=f"Record already exists: {str(e.orig)}"
                )
            else:
                raise IntegrityError(
                    message="Data integrity error",
                    constraint=str(e.orig)
                )
        except DataError:
            # データ型エラーなど
            raise ValidationError(
                message="Data type error"
            )
        except OperationalError as e:
            # データベース接続エラーなど
            raise DatabaseError(
                message="Database operation error",
                original_error=e
            )
        except SQLAlchemyError as e:
            # その他のSQLAlchemyエラー
            raise DatabaseError(
                message="Database error",
                original_error=e
            )
    
    return wrapper