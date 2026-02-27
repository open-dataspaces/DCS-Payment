"""
PaymentBillingService - 支払い・請求サービス

支払予定額・請求予定額の取得を担当
"""

from decimal import Decimal
from typing import List
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.payment_model import Transaction
from app.schemas.fee_model_schema import (
    PaymentScheduleRequest,
    PaymentAmountListResponse,
    PaymentAmountItem,
    BillingScheduleRequest,
    BillingAmountListResponse,
    BillingAmountItem,
    ConsumerTransactionRequest,
    ConsumerTransactionListResponse,
    ConsumerTransactionItem,
)
from app.core.config import settings
from app.repositories.payment_repository import TransactionRepository

logger = get_logger(__name__)


class PaymentBillingService:
    """支払い・請求サービス"""

    def __init__(self, db: Session):
        self.db = db
        self.transaction_repo = TransactionRepository(db)
        logger.debug("PaymentBillingService initialized")

    def get_payment_schedule(
        self,
        request: PaymentScheduleRequest,
        consumer_id: str
    ) -> PaymentAmountListResponse:
        """支払予定額を取得（データ利用者向け）"""
        logger.info(
            "Getting payment schedule",
            consumer_id=consumer_id,
            provider_id=request.provider_id,
            start_date=str(request.start_date),
            end_date=str(request.end_date)
        )

        # 指定期間のトランザクションを取得（consumer_idとprovider_idで絞り込み）
        # 3条件: consumer=completed AND provider=completed AND l2_http_status='200'
        transactions = self.transaction_repo.list_by_consumer_and_provider(
            consumer_id=consumer_id,
            provider_id=request.provider_id,
            start_date=request.start_date,
            end_date=request.end_date,
            consumer_exchange_status="completed",
            provider_exchange_status="completed",
            l2_http_status="200" if settings.L2_HTTP_STATUS_FILTER_ENABLED else None
        )

        logger.debug(
            "Retrieved transactions for payment schedule",
            consumer_id=consumer_id,
            provider_id=request.provider_id,
            transaction_count=len(transactions)
        )

        # トラッキングIDごとにグループ化
        grouped_transactions = self._group_transactions_by_tracking_id(transactions)

        payment_items = []
        total_amount = Decimal(0)

        for tracking_id, trans_list in grouped_transactions.items():
            # 最初のトランザクションから共通情報を取得
            first_trans = trans_list[0]

            # データIDリストを作成（NULLを除外）
            data_id_list = [t.data_id for t in trans_list if t.data_id is not None]

            # 合計金額を計算
            amount = sum(t.calculated_amount for t in trans_list)

            payment_item = PaymentAmountItem(
                tracking_id=str(tracking_id),
                fee_model_id=str(first_trans.fee_model_history.fee_model_id) if first_trans.fee_model_history else None,
                payment_service_id=str(first_trans.payment_user.payment_service_id) if first_trans.payment_user else None,
                provider_id=first_trans.provider_id,
                consumer_id=first_trans.consumer_id,
                data_id_list=data_id_list,
                completed_at=first_trans.created_at,
                amount=float(amount),
                tax_rate=float(first_trans.snapshot_tax_rate)
            )

            payment_items.append(payment_item)
            total_amount += amount

        logger.info(
            "Payment schedule retrieved successfully",
            consumer_id=consumer_id,
            provider_id=request.provider_id,
            payment_item_count=len(payment_items),
            total_amount=str(total_amount)
        )

        return PaymentAmountListResponse(
            payment_details=payment_items,
            total_amount=float(total_amount)
        )

    def get_billing_schedule(
        self,
        request: BillingScheduleRequest,
        provider_id: str
    ) -> BillingAmountListResponse:
        """請求予定額を取得（データ提供者向け）"""
        logger.info(
            "Getting billing schedule",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            start_date=str(request.start_date),
            end_date=str(request.end_date)
        )

        # 指定期間のトランザクションを取得（provider_idとconsumer_idで絞り込み）
        # 3条件: consumer=completed AND provider=completed AND l2_http_status='200'
        transactions = self.transaction_repo.list_by_consumer_and_provider(
            consumer_id=request.consumer_id,
            provider_id=provider_id,
            start_date=request.start_date,
            end_date=request.end_date,
            consumer_exchange_status="completed",
            provider_exchange_status="completed",
            l2_http_status="200" if settings.L2_HTTP_STATUS_FILTER_ENABLED else None
        )

        logger.debug(
            "Retrieved transactions for billing schedule",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            transaction_count=len(transactions)
        )

        # トラッキングIDごとにグループ化
        grouped_transactions = self._group_transactions_by_tracking_id(transactions)

        billing_items = []
        total_amount = Decimal(0)

        for tracking_id, trans_list in grouped_transactions.items():
            # 最初のトランザクションから共通情報を取得
            first_trans = trans_list[0]

            # データIDリストを作成（NULLを除外）
            data_id_list = [t.data_id for t in trans_list if t.data_id is not None]

            # 合計金額を計算
            amount = sum(t.calculated_amount for t in trans_list)

            billing_item = BillingAmountItem(
                tracking_id=str(tracking_id),
                fee_model_id=str(first_trans.fee_model_history.fee_model_id) if first_trans.fee_model_history else None,
                payment_service_id=str(first_trans.payment_user.payment_service_id) if first_trans.payment_user else None,
                provider_id=first_trans.provider_id,
                consumer_id=first_trans.consumer_id,
                data_id_list=data_id_list,
                completed_at=first_trans.created_at,
                amount=float(amount),
                tax_rate=float(first_trans.snapshot_tax_rate)
            )

            billing_items.append(billing_item)
            total_amount += amount

        logger.info(
            "Billing schedule retrieved successfully",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            billing_item_count=len(billing_items),
            total_amount=str(total_amount)
        )

        return BillingAmountListResponse(
            billing_details=billing_items,
            total_amount=float(total_amount)
        )

    def get_consumer_transactions(
        self,
        request: ConsumerTransactionRequest,
        provider_id: str,
    ) -> ConsumerTransactionListResponse:
        """利用者トランザクションを取得（データ提供者向け）"""
        logger.info(
            "Getting consumer transactions",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            start_date=str(request.start_date),
            end_date=str(request.end_date),
            settlement_status=request.settlement_status.value,
        )

        transactions = self.transaction_repo.list_by_consumer_and_provider_with_settlement_status(
            consumer_id=request.consumer_id,
            provider_id=provider_id,
            start_date=request.start_date,
            end_date=request.end_date,
            settlement_status=request.settlement_status.value,
        )

        logger.debug(
            "Retrieved transactions for consumer",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            transaction_count=len(transactions),
        )

        items = [
            ConsumerTransactionItem(
                transaction_id=str(t.transaction_id),
                tracking_id=str(t.tracking_id) if t.tracking_id else None,
                external_transaction_id=t.external_transaction_id,
                provider_id=t.provider_id,
                consumer_id=t.consumer_id,
                data_id=t.data_id,
                snapshot_price=t.snapshot_price,
                snapshot_tax_rate=t.snapshot_tax_rate,
                snapshot_tax_classification=t.snapshot_tax_classification,
                calculated_amount=t.calculated_amount,
                consumer_exchange_status=t.consumer_exchange_status,
                provider_exchange_status=t.provider_exchange_status,
                l2_http_status=t.l2_http_status,
                order_details=t.order_details,
                request_date=t.request_date,
                payment_deadline=t.payment_deadline,
                paid_at=t.paid_at,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
            for t in transactions
        ]

        logger.info(
            "Consumer transactions retrieved successfully",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            total_count=len(items),
        )

        return ConsumerTransactionListResponse(
            transactions=items,
            total_count=len(items),
        )

    def _group_transactions_by_tracking_id(
        self,
        transactions: List[Transaction]
    ) -> dict[UUID, List[Transaction]]:
        """トランザクションをトラッキングIDでグループ化"""
        grouped = {}
        for trans in transactions:
            if trans.tracking_id not in grouped:
                grouped[trans.tracking_id] = []
            grouped[trans.tracking_id].append(trans)

        logger.debug(
            "Grouped transactions by tracking ID",
            total_transactions=len(transactions),
            unique_tracking_ids=len(grouped)
        )

        return grouped
