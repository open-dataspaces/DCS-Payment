"""Services Package"""

from sqlalchemy.orm import Session

from app.core.logging import get_logger

logger = get_logger(__name__)


class BaseService:
    """
    すべてのサービスの基底クラス

    トランザクション管理の共通機能を提供
    """

    def __init__(self, db: Session):
        """
        Args:
            db: SQLAlchemyのデータベースセッション
        """
        self.db = db

    def commit(self) -> None:
        """トランザクションをコミット"""
        try:
            self.db.commit()
            logger.debug("Transaction committed")
        except Exception:
            logger.error("Failed to commit transaction", exc_info=True)
            raise

    def rollback(self) -> None:
        """トランザクションをロールバック"""
        try:
            self.db.rollback()
            logger.debug("Transaction rolled back")
        except Exception:
            logger.error("Failed to rollback transaction", exc_info=True)
            raise


# サービスクラスのエクスポート
from app.services.fee_model_service import FeeModelService  # noqa: E402
from app.services.payment_billing_service import PaymentBillingService  # noqa: E402
from app.services.transaction_service import TransactionService  # noqa: E402

__all__ = [
    "BaseService",
    "FeeModelService",
    "TransactionService",
    "PaymentBillingService",
]
