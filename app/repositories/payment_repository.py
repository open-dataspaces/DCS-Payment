"""
Repository Layer - データベースアクセス層

データベース操作のみを担当し、ビジネスロジックは含まない
SQLAlchemyの例外を独自の例外に変換して上位層に伝播
"""

from datetime import date, datetime, timezone
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy.exc import IntegrityError as SQLIntegrityError
from sqlalchemy.orm import Session

from app.core.custom_exceptions import (
    DuplicateRecordError,
    RecordNotFoundError,
    wrap_database_error,
)
from app.core.logging import get_logger
from app.models.payment_model import (
    FeeModel,
    FeeModelHistory,
    PaymentService,
    PaymentServiceUserRegistration,
    Transaction,
)

logger = get_logger(__name__)


class FeeModelRepository:
    """利用料モデルのリポジトリ"""

    def __init__(self, db: Session):
        self.db = db
        logger.debug("FeeModelRepository initialized")

    @wrap_database_error
    def create(self, fee_model: FeeModel) -> FeeModel:
        """利用料モデルを作成"""
        logger.debug(
            "Creating fee model",
            provider_id=fee_model.provider_id,
            consumer_id=fee_model.consumer_id,
            data_id=fee_model.data_id,
            price=str(fee_model.price),
            is_active=fee_model.is_active
        )
        
        try:
            self.db.add(fee_model)
            self.db.flush()
            
            logger.info(
                "Fee model created successfully",
                fee_model_id=str(fee_model.fee_model_id),
                fee_model_name=fee_model.fee_model_name,
                provider_id=fee_model.provider_id,
                consumer_id=fee_model.consumer_id
            )
            
            return fee_model
            
        except SQLIntegrityError as e:
            logger.warning(
                "Integrity constraint violated during fee model creation",
                provider_id=fee_model.provider_id,
                consumer_id=fee_model.consumer_id,
                data_id=fee_model.data_id,
                error=str(e.orig)
            )
            
            if "uq_fee_model_active" in str(e.orig):
                raise DuplicateRecordError(
                    entity="Fee Model",
                    message="Active fee model already exists for this identifier"
                )
            raise

    @wrap_database_error
    def get_by_id(self, fee_model_id: UUID) -> Optional[FeeModel]:
        """IDで利用料モデルを取得"""
        logger.debug("Fetching fee model by ID", fee_model_id=str(fee_model_id))
        
        fee_model = (
            self.db.query(FeeModel)
            .filter(FeeModel.fee_model_id == fee_model_id)
            .first()
        )
        
        if fee_model:
            logger.debug(
                "Fee model found",
                fee_model_id=str(fee_model_id),
                fee_model_name=fee_model.fee_model_name,
                is_active=fee_model.is_active
            )
        else:
            logger.debug("Fee model not found", fee_model_id=str(fee_model_id))
            
        return fee_model

    def get_by_id_or_raise(self, fee_model_id: UUID) -> FeeModel:
        """IDで利用料モデルを取得（見つからない場合は例外）"""
        logger.debug("Fetching fee model by ID (or raise)", fee_model_id=str(fee_model_id))
        
        fee_model = self.get_by_id(fee_model_id)
        if not fee_model:
            logger.warning("Fee model not found, raising exception", fee_model_id=str(fee_model_id))
            raise RecordNotFoundError(
                entity="Fee model",
                identifier=fee_model_id
            )
        return fee_model

    @wrap_database_error
    def get_active_by_identifiers(
        self,
        provider_id: str,
        consumer_id: str,
        data_id: str
    ) -> Optional[FeeModel]:
        """識別子でアクティブな利用料モデルを取得"""
        logger.debug(
            "Fetching active fee model by identifiers",
            provider_id=provider_id,
            consumer_id=consumer_id,
            data_id=data_id
        )
        
        fee_model = (
            self.db.query(FeeModel)
            .filter(
                FeeModel.provider_id == provider_id,
                FeeModel.consumer_id == consumer_id,
                FeeModel.data_id == data_id,
                FeeModel.is_active.is_(True)
            )
            .first()
        )
        
        if fee_model:
            logger.debug(
                "Active fee model found",
                fee_model_id=str(fee_model.fee_model_id),
                fee_model_name=fee_model.fee_model_name
            )
        else:
            logger.debug("No active fee model found for given identifiers")
            
        return fee_model

    @wrap_database_error
    def list_all(
        self,
        skip: int = 0,
        limit: int = 100,
        provider_id: Optional[str] = None,
        consumer_id: Optional[str] = None,
        is_active: Optional[bool] = None
    ) -> tuple[List[FeeModel], int]:
        """利用料モデル一覧を取得"""
        logger.debug(
            "Listing fee models",
            skip=skip,
            limit=limit,
            provider_id=provider_id,
            consumer_id=consumer_id,
            is_active=is_active
        )
        
        query = self.db.query(FeeModel)

        # フィルタリング
        if provider_id:
            query = query.filter(FeeModel.provider_id == provider_id)
        if consumer_id:
            query = query.filter(FeeModel.consumer_id == consumer_id)
        if is_active is not None:
            query = query.filter(FeeModel.is_active == is_active)

        total = query.count()
        models = query.offset(skip).limit(limit).all()
        
        logger.info(
            "Fee models retrieved",
            total_count=total,
            returned_count=len(models),
            skip=skip,
            limit=limit
        )

        return models, total

    @wrap_database_error
    def update(self, fee_model: FeeModel) -> FeeModel:
        """利用料モデルを更新"""
        logger.debug(
            "Updating fee model",
            fee_model_id=str(fee_model.fee_model_id),
            version=fee_model.version
        )
        
        self.db.flush()
        
        logger.info(
            "Fee model updated successfully",
            fee_model_id=str(fee_model.fee_model_id),
            fee_model_name=fee_model.fee_model_name,
            version=fee_model.version
        )
        
        return fee_model

    @wrap_database_error
    def delete(self, fee_model: FeeModel) -> None:
        """利用料モデルを削除"""
        logger.info(
            "Deleting fee model",
            fee_model_id=str(fee_model.fee_model_id),
            fee_model_name=fee_model.fee_model_name
        )
        
        self.db.delete(fee_model)
        self.db.flush()
        
        logger.info(
            "Fee model deleted successfully",
            fee_model_id=str(fee_model.fee_model_id)
        )

    @wrap_database_error
    def check_active_exists(
        self,
        provider_id: str,
        consumer_id: str,
        data_id: str,
        exclude_id: Optional[UUID] = None
    ) -> bool:
        """同じ識別子でアクティブなモデルが存在するかチェック"""
        logger.debug(
            "Checking if active fee model exists",
            provider_id=provider_id,
            consumer_id=consumer_id,
            data_id=data_id,
            exclude_id=str(exclude_id) if exclude_id else None
        )
        
        query = self.db.query(FeeModel).filter(
            FeeModel.provider_id == provider_id,
            FeeModel.consumer_id == consumer_id,
            FeeModel.data_id == data_id,
            FeeModel.is_active.is_(True)
        )

        if exclude_id:
            query = query.filter(FeeModel.fee_model_id != exclude_id)

        exists = query.count() > 0
        
        logger.debug(
            "Active fee model existence check completed",
            exists=exists
        )
        
        return exists


class FeeModelHistoryRepository:
    """利用料モデル履歴のリポジトリ"""

    def __init__(self, db: Session):
        self.db = db
        logger.debug("FeeModelHistoryRepository initialized")

    @wrap_database_error
    def create(self, history: FeeModelHistory) -> FeeModelHistory:
        """履歴を作成"""
        logger.debug(
            "Creating fee model history",
            fee_model_id=str(history.fee_model_id),
            change_type=history.change_type,
            version=history.version
        )
        
        self.db.add(history)
        self.db.flush()
        
        logger.info(
            "Fee model history created",
            history_id=str(history.fee_model_history_id),
            fee_model_id=str(history.fee_model_id),
            change_type=history.change_type
        )
        
        return history

    @wrap_database_error
    def get_latest_by_fee_model(
        self,
        fee_model_id: UUID
    ) -> Optional[FeeModelHistory]:
        """指定したモデルの最新履歴を取得"""
        logger.debug("Fetching latest history for fee model", fee_model_id=str(fee_model_id))
        
        history = (
            self.db.query(FeeModelHistory)
            .filter(FeeModelHistory.fee_model_id == fee_model_id)
            .order_by(FeeModelHistory.created_at.desc())
            .first()
        )
        
        if history:
            logger.debug(
                "Latest history found",
                history_id=str(history.fee_model_history_id),
                change_type=history.change_type
            )
        else:
            logger.debug("No history found for fee model")
            
        return history

    @wrap_database_error
    def list_by_fee_model(
        self,
        fee_model_id: UUID,
        limit: int = 10
    ) -> List[FeeModelHistory]:
        """指定したモデルの履歴一覧を取得"""
        logger.debug(
            "Listing history for fee model",
            fee_model_id=str(fee_model_id),
            limit=limit
        )
        
        histories = (
            self.db.query(FeeModelHistory)
            .filter(FeeModelHistory.fee_model_id == fee_model_id)
            .order_by(FeeModelHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        
        logger.debug(
            "Histories retrieved",
            fee_model_id=str(fee_model_id),
            count=len(histories)
        )
        
        return histories

    @wrap_database_error
    def create_snapshot(
        self,
        fee_model: FeeModel,
        change_type: str = "snapshot",
        change_reason: Optional[str] = None
    ) -> FeeModelHistory:
        """スナップショット履歴を作成"""
        logger.debug(
            "Creating snapshot for fee model",
            fee_model_id=str(fee_model.fee_model_id),
            change_type=change_type,
            change_reason=change_reason
        )
        
        history = FeeModelHistory(
            fee_model_id=fee_model.fee_model_id,
            fee_model_name=fee_model.fee_model_name,
            price=fee_model.price,
            tax_classification=fee_model.tax_classification,
            tax_rate=fee_model.tax_rate,
            provider_id=fee_model.provider_id,
            consumer_id=fee_model.consumer_id,
            data_id=fee_model.data_id,
            payment_service_id=fee_model.payment_service_id,
            storage_type=fee_model.storage_type,
            storage_key=fee_model.storage_key,
            valid_from=fee_model.valid_from,
            valid_to=fee_model.valid_to or datetime.now(timezone.utc),
            change_type=change_type,
            change_reason=change_reason,
            version=fee_model.version
        )
        
        return self.create(history)


class TransactionRepository:
    """取引のリポジトリ"""

    def __init__(self, db: Session):
        self.db = db
        logger.debug("TransactionRepository initialized")

    @wrap_database_error
    def create(self, transaction: Transaction) -> Transaction:
        """取引を作成"""
        logger.debug(
            "Creating transaction",
            tracking_id=str(transaction.tracking_id),
            provider_id=transaction.provider_id,
            consumer_id=transaction.consumer_id,
            data_id=transaction.data_id,
            consumer_exchange_status=transaction.consumer_exchange_status,
            provider_exchange_status=transaction.provider_exchange_status
        )

        try:
            self.db.add(transaction)
            self.db.flush()

            logger.info(
                "Transaction created successfully",
                transaction_id=str(transaction.transaction_id),
                tracking_id=str(transaction.tracking_id),
                calculated_amount=str(transaction.calculated_amount),
                consumer_exchange_status=transaction.consumer_exchange_status,
                provider_exchange_status=transaction.provider_exchange_status
            )

            return transaction

        except SQLIntegrityError as e:
            logger.warning(
                "Integrity constraint violated during transaction creation",
                tracking_id=str(transaction.tracking_id),
                error=str(e.orig)
            )

            if "primary key" in str(e.orig).lower() or "transaction_id" in str(e.orig).lower():
                raise DuplicateRecordError(
                    entity="transaction",
                    message=f"Transaction ID already exists: {transaction.transaction_id}"
                )
            raise

    @wrap_database_error
    def get_by_tracking_id(self, tracking_id: UUID) -> Optional[Transaction]:
        """トラッキングIDで取引を取得"""
        logger.debug("Fetching transaction by tracking ID", tracking_id=str(tracking_id))
        
        transaction = (
            self.db.query(Transaction)
            .filter(Transaction.tracking_id == tracking_id)
            .first()
        )
        
        if transaction:
            logger.debug(
                "Transaction found",
                tracking_id=str(tracking_id),
                consumer_exchange_status=transaction.consumer_exchange_status,
                provider_exchange_status=transaction.provider_exchange_status
            )
        else:
            logger.debug("Transaction not found", tracking_id=str(tracking_id))
            
        return transaction

    @wrap_database_error
    def get_all_by_tracking_id(self, tracking_id: UUID) -> List[Transaction]:
        """トラッキングIDで全ての取引を取得"""
        logger.debug("Fetching all transactions by tracking ID", tracking_id=str(tracking_id))

        transactions = (
            self.db.query(Transaction)
            .filter(Transaction.tracking_id == tracking_id)
            .all()
        )

        logger.debug(
            "Transactions found",
            tracking_id=str(tracking_id),
            count=len(transactions)
        )
        return transactions

    @wrap_database_error
    def get_all_by_tracking_id_and_identifiers(
        self,
        tracking_id: UUID,
        consumer_id: str,
        provider_id: str
    ) -> List[Transaction]:
        """トラッキングID、コンシューマーID、プロバイダーIDで全ての取引を取得"""
        logger.debug(
            "Fetching all transactions by tracking ID and identifiers",
            tracking_id=str(tracking_id),
            consumer_id=consumer_id,
            provider_id=provider_id
        )

        transactions = (
            self.db.query(Transaction)
            .filter(
                Transaction.tracking_id == tracking_id,
                Transaction.consumer_id == consumer_id,
                Transaction.provider_id == provider_id
            )
            .all()
        )

        logger.debug(
            "Transactions found by identifiers",
            tracking_id=str(tracking_id),
            consumer_id=consumer_id,
            provider_id=provider_id,
            count=len(transactions)
        )
        return transactions

    def get_by_tracking_id_or_raise(self, tracking_id: UUID) -> Transaction:
        """トラッキングIDで取引を取得（見つからない場合は例外）"""
        logger.debug("Fetching transaction by tracking ID (or raise)", tracking_id=str(tracking_id))
        
        transaction = self.get_by_tracking_id(tracking_id)
        if not transaction:
            logger.warning("Transaction not found, raising exception", tracking_id=str(tracking_id))
            raise RecordNotFoundError(
                entity="transaction",
                identifier=tracking_id
            )
        return transaction

    @wrap_database_error
    def update(self, transaction: Transaction) -> Transaction:
        """取引を更新"""
        logger.debug(
            "Updating transaction",
            transaction_id=str(transaction.transaction_id),
            tracking_id=str(transaction.tracking_id),
            consumer_exchange_status=transaction.consumer_exchange_status,
            provider_exchange_status=transaction.provider_exchange_status,
            l2_http_status=transaction.l2_http_status
        )

        self.db.flush()

        logger.info(
            "Transaction updated successfully",
            transaction_id=str(transaction.transaction_id),
            tracking_id=str(transaction.tracking_id),
            consumer_exchange_status=transaction.consumer_exchange_status,
            provider_exchange_status=transaction.provider_exchange_status,
            l2_http_status=transaction.l2_http_status
        )

        return transaction

    @wrap_database_error
    def list_by_provider(
        self,
        provider_id: str,
        start_date: date,
        end_date: date,
        consumer_exchange_status: Optional[str] = None,
        provider_exchange_status: Optional[str] = None,
        l2_http_status: Optional[str] = None
    ) -> List[Transaction]:
        """プロバイダーIDと期間で取引一覧を取得"""
        logger.debug(
            "Listing transactions by provider",
            provider_id=provider_id,
            start_date=str(start_date),
            end_date=str(end_date)
        )

        query = (
            self.db.query(Transaction)
            .filter(
                Transaction.provider_id == provider_id,
                Transaction.created_at >= datetime.combine(start_date, datetime.min.time()),
                Transaction.created_at <= datetime.combine(end_date, datetime.max.time())
            )
        )

        if consumer_exchange_status:
            query = query.filter(Transaction.consumer_exchange_status == consumer_exchange_status)
        if provider_exchange_status:
            query = query.filter(Transaction.provider_exchange_status == provider_exchange_status)
        if l2_http_status:
            query = query.filter(Transaction.l2_http_status == l2_http_status)

        transactions = query.all()

        logger.info(
            "Transactions retrieved for provider",
            provider_id=provider_id,
            count=len(transactions),
            start_date=str(start_date),
            end_date=str(end_date)
        )

        return transactions

    @wrap_database_error
    def list_by_consumer(
        self,
        consumer_id: str,
        start_date: date,
        end_date: date,
        consumer_exchange_status: Optional[str] = None,
        provider_exchange_status: Optional[str] = None,
        l2_http_status: Optional[str] = None
    ) -> List[Transaction]:
        """コンシューマーIDと期間で取引一覧を取得"""
        logger.debug(
            "Listing transactions by consumer",
            consumer_id=consumer_id,
            start_date=str(start_date),
            end_date=str(end_date)
        )

        query = (
            self.db.query(Transaction)
            .filter(
                Transaction.consumer_id == consumer_id,
                Transaction.created_at >= datetime.combine(start_date, datetime.min.time()),
                Transaction.created_at <= datetime.combine(end_date, datetime.max.time())
            )
        )

        if consumer_exchange_status:
            query = query.filter(Transaction.consumer_exchange_status == consumer_exchange_status)
        if provider_exchange_status:
            query = query.filter(Transaction.provider_exchange_status == provider_exchange_status)
        if l2_http_status:
            query = query.filter(Transaction.l2_http_status == l2_http_status)

        transactions = query.all()

        logger.info(
            "Transactions retrieved for consumer",
            consumer_id=consumer_id,
            count=len(transactions),
            start_date=str(start_date),
            end_date=str(end_date)
        )

        return transactions

    @wrap_database_error
    def list_by_consumer_and_provider(
        self,
        consumer_id: str,
        provider_id: str,
        start_date: date,
        end_date: date,
        consumer_exchange_status: Optional[str] = None,
        provider_exchange_status: Optional[str] = None,
        l2_http_status: Optional[str] = None
    ) -> List[Transaction]:
        """コンシューマーIDとプロバイダーIDと期間で取引一覧を取得"""
        logger.debug(
            "Listing transactions by consumer and provider",
            consumer_id=consumer_id,
            provider_id=provider_id,
            start_date=str(start_date),
            end_date=str(end_date)
        )

        query = (
            self.db.query(Transaction)
            .filter(
                Transaction.consumer_id == consumer_id,
                Transaction.provider_id == provider_id,
                Transaction.created_at >= datetime.combine(start_date, datetime.min.time()),
                Transaction.created_at <= datetime.combine(end_date, datetime.max.time())
            )
        )

        if consumer_exchange_status:
            query = query.filter(Transaction.consumer_exchange_status == consumer_exchange_status)
        if provider_exchange_status:
            query = query.filter(Transaction.provider_exchange_status == provider_exchange_status)
        if l2_http_status:
            query = query.filter(Transaction.l2_http_status == l2_http_status)

        transactions = query.all()

        logger.info(
            "Transactions retrieved for consumer and provider",
            consumer_id=consumer_id,
            provider_id=provider_id,
            count=len(transactions),
            start_date=str(start_date),
            end_date=str(end_date)
        )

        return transactions

    @wrap_database_error
    def list_by_consumer_and_provider_with_settlement_status(
        self,
        consumer_id: str,
        provider_id: str,
        start_date: date,
        end_date: date,
        settlement_status: str,
    ) -> List[Transaction]:
        """コンシューマーID・プロバイダーID・期間・決済状態で取引一覧を取得"""
        logger.debug(
            "Listing transactions by consumer, provider and settlement_status",
            consumer_id=consumer_id,
            provider_id=provider_id,
            start_date=str(start_date),
            end_date=str(end_date),
            settlement_status=settlement_status,
        )

        transactions = (
            self.db.query(Transaction)
            .filter(
                Transaction.consumer_id == consumer_id,
                Transaction.provider_id == provider_id,
                Transaction.created_at >= datetime.combine(start_date, datetime.min.time()),
                Transaction.created_at <= datetime.combine(end_date, datetime.max.time()),
                Transaction.settlement_status == settlement_status,
            )
            .all()
        )

        logger.info(
            "Transactions retrieved by settlement_status",
            consumer_id=consumer_id,
            provider_id=provider_id,
            settlement_status=settlement_status,
            count=len(transactions),
            start_date=str(start_date),
            end_date=str(end_date),
        )

        return transactions

    @wrap_database_error
    def get_by_identifiers(
        self,
        provider_id: str,
        consumer_id: str,
        data_id_list: List[str]
    ) -> List[Transaction]:
        """識別子で取引を検索"""
        logger.debug(
            "Searching transactions by identifiers",
            provider_id=provider_id,
            consumer_id=consumer_id,
            data_id_count=len(data_id_list)
        )
        
        transactions = (
            self.db.query(Transaction)
            .filter(
                Transaction.provider_id == provider_id,
                Transaction.consumer_id == consumer_id,
                Transaction.data_id.in_(data_id_list)
            )
            .all()
        )
        
        logger.debug(
            "Transactions found by identifiers",
            count=len(transactions)
        )
        
        return transactions

    @wrap_database_error
    def get_by_identifiers_without_tracking_id(
        self,
        provider_id: str,
        consumer_id: str,
        data_id_list: List[Optional[str]],
    ) -> List[Transaction]:
        """provider_id + consumer_id + data_id + tracking_id IS NULL で
        事前登録されたTransactionレコードを検索する。

        Args:
            provider_id: データ提供者ID
            consumer_id: データ利用者ID
            data_id_list: データIDリスト（NULLの場合はdata_id IS NULLで検索）
        """
        logger.debug(
            "Searching pre-registered transactions (tracking_id=NULL)",
            provider_id=provider_id,
            consumer_id=consumer_id,
            data_id_list=data_id_list,
        )

        query = (
            self.db.query(Transaction)
            .filter(
                Transaction.provider_id == provider_id,
                Transaction.consumer_id == consumer_id,
                Transaction.tracking_id.is_(None),
            )
        )

        if data_id_list and data_id_list[0] is not None:
            query = query.filter(Transaction.data_id.in_(data_id_list))
        else:
            query = query.filter(Transaction.data_id.is_(None))

        transactions = query.all()

        logger.debug(
            "Pre-registered transactions found",
            count=len(transactions),
        )

        return transactions


class PaymentServiceRepository:
    """決済サービスのリポジトリ"""

    def __init__(self, db: Session):
        self.db = db
        logger.debug("PaymentServiceRepository initialized")

    @wrap_database_error
    def get_by_id(self, payment_service_id: UUID) -> Optional[PaymentService]:
        """IDで決済サービスを取得"""
        logger.debug("Fetching payment service by ID", payment_service_id=str(payment_service_id))
        
        service = (
            self.db.query(PaymentService)
            .filter(PaymentService.payment_service_id == payment_service_id)
            .first()
        )
        
        if service:
            logger.debug(
                "Payment service found",
                payment_service_id=str(payment_service_id),
                payment_service_name=service.payment_service_name
            )
        else:
            logger.debug("Payment service not found", payment_service_id=str(payment_service_id))
            
        return service

    def get_by_id_or_raise(self, payment_service_id: UUID) -> PaymentService:
        """IDで決済サービスを取得（見つからない場合は例外）"""
        logger.debug("Fetching payment service by ID (or raise)", payment_service_id=str(payment_service_id))
        
        service = self.get_by_id(payment_service_id)
        if not service:
            logger.warning("Payment service not found, raising exception", payment_service_id=str(payment_service_id))
            raise RecordNotFoundError(
                entity="payment_service",
                identifier=payment_service_id
            )
        return service

    @wrap_database_error
    def create(self, payment_service: PaymentService) -> PaymentService:
        """決済サービスを作成"""
        logger.info(
            "Creating payment service",
            payment_service_name=payment_service.payment_service_name
        )
        
        self.db.add(payment_service)
        self.db.flush()
        
        logger.info(
            "Payment service created successfully",
            payment_service_id=str(payment_service.payment_service_id),
            payment_service_name=payment_service.payment_service_name
        )
        
        return payment_service

    @wrap_database_error
    def list_all(self) -> List[PaymentService]:
        """すべての決済サービスを取得"""
        logger.debug("Listing all payment services")
        
        services = self.db.query(PaymentService).all()
        
        logger.debug("Payment services retrieved", count=len(services))
        
        return services

    @wrap_database_error
    def get_or_create(
        self,
        payment_service_id: UUID,
        payment_service_name: Optional[str] = None,
        payment_service_url: Optional[str] = None
    ) -> Tuple[PaymentService, bool]:
        """
        決済サービスを取得、存在しない場合は作成
        
        Args:
            payment_service_id: 決済サービスID
            payment_service_name: 決済サービス名（新規作成時に使用）
            payment_service_url: 決済サービスURL（新規作成時に使用）
        
        Returns:
            Tuple[PaymentService, bool]: (決済サービス, 新規作成されたかどうか)
        """
        logger.debug(
            "Get or create payment service",
            payment_service_id=str(payment_service_id)
        )
        
        payment_service = self.get_by_id(payment_service_id)
        
        if not payment_service:
            logger.info(
                "Payment service not found, creating new one",
                payment_service_id=str(payment_service_id)
            )
            
            payment_service = PaymentService(
                payment_service_id=payment_service_id,
                payment_service_name=payment_service_name or f"Auto-created Service ({payment_service_id})",
                payment_service_url=payment_service_url or "https://example.com/api/v1"
            )
            payment_service = self.create(payment_service)
            
            logger.info(
                "Payment service auto-created successfully",
                payment_service_id=str(payment_service_id),
                payment_service_name=payment_service.payment_service_name
            )
            
            return payment_service, True  # 新規作成
        
        logger.debug(
            "Payment service already exists",
            payment_service_id=str(payment_service_id)
        )
        
        return payment_service, False  # 既存


class PaymentServiceUserRegistrationRepository:
    """決済サービスユーザ登録のリポジトリ"""

    def __init__(self, db: Session):
        self.db = db
        logger.debug("PaymentServiceUserRegistrationRepository initialized")

    @wrap_database_error
    def get_by_id(
        self,
        payment_service_user_id: UUID
    ) -> Optional[PaymentServiceUserRegistration]:
        """IDで取得"""
        logger.debug("Fetching payment service user registration by ID", user_id=str(payment_service_user_id))
        
        registration = (
            self.db.query(PaymentServiceUserRegistration)
            .filter(
                PaymentServiceUserRegistration.payment_service_user_id == payment_service_user_id
            )
            .first()
        )
        
        if registration:
            logger.debug("User registration found", user_id=str(payment_service_user_id))
        else:
            logger.debug("User registration not found", user_id=str(payment_service_user_id))
            
        return registration

    @wrap_database_error
    def get_by_identifiers(
        self,
        payment_service_id: UUID,
        consumer_id: str,
        provider_id: str
    ) -> Optional[PaymentServiceUserRegistration]:
        """識別子で取得"""
        logger.debug(
            "Fetching user registration by identifiers",
            payment_service_id=str(payment_service_id),
            consumer_id=consumer_id,
            provider_id=provider_id
        )
        
        registration = (
            self.db.query(PaymentServiceUserRegistration)
            .filter(
                PaymentServiceUserRegistration.payment_service_id == payment_service_id,
                PaymentServiceUserRegistration.consumer_id == consumer_id,
                PaymentServiceUserRegistration.provider_id == provider_id
            )
            .first()
        )
        
        if registration:
            logger.debug("User registration found by identifiers", user_id=str(registration.payment_service_user_id))
        else:
            logger.debug("User registration not found by identifiers")
            
        return registration

    @wrap_database_error
    def create(
        self,
        registration: PaymentServiceUserRegistration
    ) -> PaymentServiceUserRegistration:
        """ユーザ登録を作成"""
        logger.debug(
            "Creating user registration",
            consumer_id=registration.consumer_id,
            provider_id=registration.provider_id
        )
        
        try:
            self.db.add(registration)
            self.db.flush()
            
            logger.info(
                "User registration created successfully",
                user_id=str(registration.payment_service_user_id),
                consumer_id=registration.consumer_id,
                provider_id=registration.provider_id
            )
            
            return registration
            
        except SQLIntegrityError as e:
            logger.warning(
                "Integrity constraint violated during user registration creation",
                consumer_id=registration.consumer_id,
                provider_id=registration.provider_id,
                error=str(e.orig)
            )
            
            if "uq_payment_user_registration" in str(e.orig):
                raise DuplicateRecordError(
                    entity="Payment Service User Registration",
                    message="This user registration already exists"
                )
            raise

    def get_or_create(
        self,
        payment_service_id: UUID,
        consumer_id: str,
        provider_id: str
    ) -> PaymentServiceUserRegistration:
        """取得または作成"""
        logger.debug(
            "Get or create user registration",
            payment_service_id=str(payment_service_id),
            consumer_id=consumer_id,
            provider_id=provider_id
        )
        
        registration = self.get_by_identifiers(
            payment_service_id,
            consumer_id,
            provider_id
        )

        if not registration:
            logger.info("User registration not found, creating new one")
            registration = PaymentServiceUserRegistration(
                payment_service_id=payment_service_id,
                consumer_id=consumer_id,
                provider_id=provider_id
            )
            registration = self.create(registration)
        else:
            logger.debug("User registration already exists", user_id=str(registration.payment_service_user_id))

        return registration