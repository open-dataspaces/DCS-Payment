"""Repositories Package"""

from sqlalchemy.orm import Session

from app.core.logging import get_logger
logger = get_logger(__name__)

# ============================================================================
# ベースリポジトリクラス
# ============================================================================

class BaseRepository:
    """
    すべてのリポジトリの基底クラス
    
    共通のCRUD操作とユーティリティメソッドを提供
    
    重要: Repository層ではcommit/rollbackを行いません
          トランザクション管理はService層で行います
    """
    
    def __init__(self, db: Session):
        """
        Args:
            db: SQLAlchemyのデータベースセッション
        """
        self.db = db
    
    def _log_error(self, error: Exception, operation: str) -> None:
        """
        エラーのログ出力

        Args:
            error: 発生した例外
            operation: 実行していた操作名
        """
        logger.error(
            "Database error",
            extra={
                'operation': operation,
                'error_type': type(error).__name__,
                'error_message': str(error)
            },
            exc_info=True
        )