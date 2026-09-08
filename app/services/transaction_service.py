"""
TransactionService - 取引サービス

データ交換の取引処理を担当
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.clients.payment_client import PaymentClient, PaymentClientError, TransactionInfo
from app.core.config import settings
from app.core.custom_exceptions import (
    RecordNotFoundError,
    ResourceConflictError,
    BusinessRuleViolationError,
)
from app.core.logging import get_logger
from app.models.payment_model import PaymentServiceUserRegistration, Transaction
from app.schemas.fee_model_schema import (
    DataExchangeRequest,
    DataExchangeStatusRequest,
    DataExchangeStatusResponse,
    DataExchangeNonFeeModelRequest,
    DataExchangeStatusNonFeeModelResponse,
    TransactionEligibilityRequest,
    TransactionEligibilityResponse,
    TransactionEligibilityNonFeeModelRequest,
    TransactionEligibilityNonFeeModelResponse,
    DataExchangeStatus,
    ProcessStatus,
    TransactionEligibility,
)
from app.repositories.payment_repository import (
    FeeModelRepository,
    FeeModelHistoryRepository,
    TransactionRepository,
    PaymentServiceUserRegistrationRepository,
)
from app.utils.money import calculate_amount_with_tax, to_decimal

logger = get_logger(__name__)


class TransactionService:
    """取引サービス"""

    def __init__(
        self,
        db: Session,
        payment_client: Optional[PaymentClient] = None,
    ):
        self.db = db
        self.transaction_repo = TransactionRepository(db)
        self.fee_model_repo = FeeModelRepository(db)
        self.history_repo = FeeModelHistoryRepository(db)
        self.payment_user_repo = PaymentServiceUserRegistrationRepository(db)
        self._payment_client = payment_client
        logger.debug("TransactionService initialized")

    @property
    def payment_client(self) -> PaymentClient:
        """PaymentClientを取得（遅延初期化）"""
        if self._payment_client is None:
            self._payment_client = PaymentClient()
        return self._payment_client

    async def close(self) -> None:
        """リソースをクリーンアップ"""
        if self._payment_client is not None:
            await self._payment_client.close()
            self._payment_client = None

    async def check_transaction_eligibility(
        self,
        request: TransactionEligibilityRequest,
        tracking_id: str,
    ) -> TransactionEligibilityResponse:
        """取引可否を確認

        1. 利用料モデルの存在確認
        2. 外部決済サービスの与信確認（決済サービス名に応じてAPIを呼び出し）
           - consumer_id を buyer_id として使用
        3. 与信OKの場合、tracking_id 付きで Transaction を事前登録する

        tracking_id（X-TrackingId）はこのデータ交換を識別するIDとして事前登録行に保存し、
        データ交換状態登録API は同じ tracking_id で事前登録行を特定する。
        同じ tracking_id で再度呼ばれた場合（再送）は、同一内容なら再登録せず ALLOWED を返す。
        """
        logger.info(
            "Checking transaction eligibility",
            provider_id=request.provider_id,
            consumer_id=request.consumer_id,
            data_id_count=len(request.data_id_list)
        )

        # 1. 各データIDについて利用料モデルの存在を確認
        missing_data_ids = []
        fee_models = []

        for data_id in request.data_id_list:
            fee_model = self.fee_model_repo.get_active_by_identifiers(
                provider_id=request.provider_id,
                consumer_id=request.consumer_id,
                data_id=data_id
            )
            if not fee_model:
                missing_data_ids.append(data_id)
            else:
                fee_models.append(fee_model)

        if missing_data_ids:
            logger.warning(
                "Transaction denied - fee models not found",
                provider_id=request.provider_id,
                consumer_id=request.consumer_id,
                missing_data_ids=missing_data_ids
            )
            return TransactionEligibilityResponse(
                status=TransactionEligibility.DENIED,
                detail=f"Fee models not found: {', '.join(missing_data_ids)}"
            )

        # 2. 外部決済サービスの与信確認
        # 外部決済サービス連携が無効の場合はスキップ
        if not settings.EXTERNAL_PAYMENT_ENABLED:
            logger.info(
                "External payment service disabled, skipping credit check",
                provider_id=request.provider_id,
                consumer_id=request.consumer_id,
                data_id_count=len(request.data_id_list),
            )
            return TransactionEligibilityResponse(
                status=TransactionEligibility.ALLOWED,
                detail="Eligible for transaction "
            )

        tracking_uuid = UUID(tracking_id)

        # 同じ tracking_id で既に事前登録済みの場合（再送）は冪等に扱う
        existing_records = self.transaction_repo.get_all_by_tracking_id(tracking_uuid)
        if existing_records:
            self._ensure_same_pre_registration(
                existing_records,
                tracking_id=tracking_id,
                provider_id=request.provider_id,
                consumer_id=request.consumer_id,
                data_ids=set(request.data_id_list),
            )
            logger.info(
                "Transaction already pre-registered for tracking_id, returning ALLOWED",
                tracking_id=tracking_id,
                record_count=len(existing_records),
            )
            return TransactionEligibilityResponse(
                status=TransactionEligibility.ALLOWED,
                detail="Eligible for transaction (already registered)",
                tracking_id=tracking_id,
            )

        # 利用料モデルごとに異なる決済サービスが設定されている可能性があるため、
        # 各決済サービスごとにNP掛け払い取引登録を行う
        checked_service_ids: set[str] = set()

        for fee_model in fee_models:
            # 決済サービスIDを取得
            payment_service_id = str(fee_model.payment_service_id)

            # 同じ決済サービスは1度だけチェック
            if payment_service_id in checked_service_ids:
                continue

            # 取引登録をサポートしているサービスのみチェック
            if not self.payment_client.supports_transaction_registration(payment_service_id):
                logger.debug(
                    "Transaction registration not supported for payment service",
                    payment_service_id=payment_service_id,
                )
                checked_service_ids.add(payment_service_id)
                continue

            # 利用料モデルから取引情報を構築
            transaction_infos = []
            for fm in fee_models:
                if str(fm.payment_service_id) == payment_service_id:
                    calculated_amount = self._calculate_amount_with_tax(
                        price=fm.price,
                        tax_rate=fm.tax_rate,
                        tax_classification=fm.tax_classification
                    )
                    transaction_infos.append(TransactionInfo(
                        data_id=fm.data_id,
                        amount=calculated_amount,
                        tax_rate=fm.tax_rate,
                        order_date=date.today(),
                    ))

            if not transaction_infos:
                checked_service_ids.add(payment_service_id)
                continue

            # DBから企業情報を取得してdeliveryInfoを構築
            payment_user = self.payment_user_repo.get_by_identifiers(
                payment_service_id=UUID(payment_service_id),
                consumer_id=request.consumer_id,
                provider_id=request.provider_id,
            )
            delivery_info = self._build_delivery_info(payment_user) if payment_user else None

            # external_buyer_idを取得
            external_buyer_id = payment_user.external_buyer_id if payment_user else None
            if not external_buyer_id:
                logger.warning(
                    "external_buyer_id not set for consumer",
                    payment_service_id=payment_service_id,
                    consumer_id=request.consumer_id,
                    provider_id=request.provider_id,
                )
                return TransactionEligibilityResponse(
                    status=TransactionEligibility.DENIED,
                    detail="external_buyer_id is not set for this consumer"
                )

            logger.info(
                "Registering transaction with NP Kakebarai for eligibility check",
                payment_service_id=payment_service_id,
                consumer_id=request.consumer_id,
                transaction_count=len(transaction_infos),
            )

            try:
                # NP掛け払い取引登録APIを呼び出し
                registration_result = await self.payment_client.register_transaction(
                    external_buyer_id=external_buyer_id,
                    payment_service_id=payment_service_id,
                    shop_transaction_id=tracking_id,
                    transactions=transaction_infos,
                    tracking_id=tracking_id,
                    delivery_info=delivery_info,
                )

                if not registration_result.is_success:
                    logger.warning(
                        "Transaction denied - NP Kakebarai authori NG",
                        provider_id=request.provider_id,
                        consumer_id=request.consumer_id,
                        payment_service_id=payment_service_id,
                        ng_count=len(registration_result.ng_items),
                        ok_count=len(registration_result.ok_items),
                        detail=registration_result.detail,
                    )
                    # 1件でも NG なら取引全体を不可とし、NP側で採番済みの取引は取り消す
                    await self._cancel_external_transactions_best_effort(
                        payment_service_id=payment_service_id,
                        external_transaction_ids=registration_result.registered_external_transaction_ids,
                        reason="authori NG on other item",
                    )
                    return TransactionEligibilityResponse(
                        status=TransactionEligibility.DENIED,
                        detail=f"Transaction authori failed: {registration_result.detail}"
                    )

                external_ids_by_data_id = registration_result.external_transaction_id_by_data_id()
                logger.info(
                    "NP Kakebarai transaction registration passed",
                    payment_service_id=payment_service_id,
                    consumer_id=request.consumer_id,
                    external_transaction_ids=external_ids_by_data_id,
                )

                try:
                    # NP取引登録成功後、各fee_modelに対してTransactionレコードを事前登録
                    # external_transaction_id は item（data_id）ごとに個別の値を保存する
                    for fm in fee_models:
                        if str(fm.payment_service_id) == payment_service_id:
                            external_transaction_id = external_ids_by_data_id.get(fm.data_id)
                            if not external_transaction_id:
                                raise BusinessRuleViolationError(
                                    message=f"external_transaction_id not returned for data_id={fm.data_id}",
                                    rule="external_transaction_id_required",
                                )
                            history = self.history_repo.create_snapshot(
                                fee_model=fm,
                                change_type="snapshot",
                                change_reason="Transaction pre-registered via eligibility check"
                            )

                            payment_user_for_txn = self.payment_user_repo.get_by_identifiers(
                                payment_service_id=fm.payment_service_id,
                                consumer_id=request.consumer_id,
                                provider_id=request.provider_id
                            )
                            if not payment_user_for_txn:
                                raise RecordNotFoundError(
                                    entity="Payment Service User Registration",
                                    identifier=f"payment_service={fm.payment_service_id}, consumer={request.consumer_id}, provider={request.provider_id}"
                                )

                            calculated_amount = self._calculate_amount_with_tax(
                                price=fm.price,
                                tax_rate=fm.tax_rate,
                                tax_classification=fm.tax_classification
                            )

                            transaction = Transaction(
                                tracking_id=tracking_uuid,
                                external_transaction_id=external_transaction_id,
                                fee_model_history_id=history.fee_model_history_id,
                                payment_service_user_id=payment_user_for_txn.payment_service_user_id,
                                provider_id=request.provider_id,
                                consumer_id=request.consumer_id,
                                data_id=fm.data_id,
                                snapshot_price=fm.price,
                                snapshot_tax_rate=fm.tax_rate,
                                snapshot_tax_classification=fm.tax_classification,
                                calculated_amount=calculated_amount,
                                consumer_exchange_status="pending",
                                provider_exchange_status="pending",
                                l2_http_status="pending",
                            )
                            self.transaction_repo.create(transaction)

                    self.db.commit()

                except Exception as e:
                    # NP側には取引が登録済みだがローカル保存に失敗した。
                    # 再試行で二重登録にならないよう NP側の取引を取り消す（補償処理）。
                    self.db.rollback()
                    logger.error(
                        "Local transaction save failed after external registration - cancelling external transactions",
                        payment_service_id=payment_service_id,
                        external_transaction_ids=registration_result.registered_external_transaction_ids,
                        error_type=type(e).__name__,
                        error=str(e),
                        exc_info=True,
                    )
                    await self._cancel_external_transactions_best_effort(
                        payment_service_id=payment_service_id,
                        external_transaction_ids=registration_result.registered_external_transaction_ids,
                        reason="local save failed after external registration",
                    )
                    raise

                logger.info(
                    "Transaction records pre-registered",
                    payment_service_id=payment_service_id,
                    consumer_id=request.consumer_id,
                    external_transaction_ids=external_ids_by_data_id,
                )

                checked_service_ids.add(payment_service_id)

            except PaymentClientError as e:
                logger.error(
                    "NP Kakebarai transaction registration API error",
                    provider_id=request.provider_id,
                    consumer_id=request.consumer_id,
                    payment_service_id=payment_service_id,
                    error=str(e),
                )
                return TransactionEligibilityResponse(
                    status=TransactionEligibility.DENIED,
                    detail=f"Transaction registration error: {str(e)}"
                )

        logger.info(
            "Transaction eligibility check passed",
            provider_id=request.provider_id,
            consumer_id=request.consumer_id,
            data_id_count=len(request.data_id_list),
            checked_service_ids=list(checked_service_ids),
        )

        return TransactionEligibilityResponse(
            status=TransactionEligibility.ALLOWED,
            detail="Eligible for transaction",
            tracking_id=tracking_id,
        )

    def _ensure_same_pre_registration(
        self,
        records: list[Transaction],
        tracking_id: str,
        provider_id: str,
        consumer_id: str,
        data_ids: set[Optional[str]],
    ) -> None:
        """同じ tracking_id の既存レコードが、今回のリクエストと同一の事前登録か検証する

        再送（同一内容・両ステータス pending・外部取引ID採番済み）なら何もしない。
        それ以外（別内容、既に交換完了/失敗）は ResourceConflictError。
        """
        same_parties = all(
            r.provider_id == provider_id and r.consumer_id == consumer_id for r in records
        )
        same_data = {r.data_id for r in records} == data_ids
        all_pending = all(
            r.consumer_exchange_status == "pending" and r.provider_exchange_status == "pending"
            for r in records
        )
        all_registered = all(r.external_transaction_id for r in records)
        if same_parties and same_data and all_pending and all_registered:
            return
        logger.warning(
            "tracking_id already used by a different or completed transaction",
            tracking_id=tracking_id,
            same_parties=same_parties,
            same_data=same_data,
            all_pending=all_pending,
            all_registered=all_registered,
        )
        raise ResourceConflictError(
            message=f"tracking_id already used: {tracking_id}",
            resource="Transaction",
        )

    async def check_transaction_eligibility_non_fee_model(
        self,
        request: TransactionEligibilityNonFeeModelRequest,
        tracking_id: str,
    ) -> TransactionEligibilityNonFeeModelResponse:
        """取引可否を確認（利用料モデル無し）

        利用料モデルを使用せず、リクエストで直接指定された決済サービスIDで与信確認を行う。
        consumer_id を buyer_id として使用。

        Args:
            request: 取引可否確認リクエスト（利用料モデル無し）
            tracking_id: トラッキングID（任意）

        Returns:
            TransactionEligibilityNonFeeModelResponse: 取引可否確認結果
        """
        logger.info(
            "Checking transaction eligibility (non fee model)",
            provider_id=request.provider_id,
            consumer_id=request.consumer_id,
            payment_service_id=str(request.payment_service_id),
            amount=request.amount,
        )

        payment_service_id = str(request.payment_service_id)

        # 外部決済サービス連携が無効の場合はスキップ
        if not settings.EXTERNAL_PAYMENT_ENABLED:
            logger.info(
                "External payment service disabled, skipping transaction registration",
                provider_id=request.provider_id,
                consumer_id=request.consumer_id,
                payment_service_id=payment_service_id,
            )
            return TransactionEligibilityNonFeeModelResponse(
                status=TransactionEligibility.ALLOWED,
                detail="Eligible for transaction "
            )

        # 取引登録をサポートしているサービスのみチェック
        if not self.payment_client.supports_transaction_registration(payment_service_id):
            logger.info(
                "Transaction registration not supported for payment service, allowing transaction",
                payment_service_id=payment_service_id,
            )
            return TransactionEligibilityNonFeeModelResponse(
                status=TransactionEligibility.ALLOWED,
                detail="Eligible for transaction (transaction registration not required)"
            )

        tracking_uuid = UUID(tracking_id)

        # 同じ tracking_id で既に事前登録済みの場合（再送）は冪等に扱う
        existing_records = self.transaction_repo.get_all_by_tracking_id(tracking_uuid)
        if existing_records:
            self._ensure_same_pre_registration(
                existing_records,
                tracking_id=tracking_id,
                provider_id=request.provider_id,
                consumer_id=request.consumer_id,
                data_ids={None},
            )
            logger.info(
                "Transaction already pre-registered for tracking_id (non fee model), returning ALLOWED",
                tracking_id=tracking_id,
            )
            return TransactionEligibilityNonFeeModelResponse(
                status=TransactionEligibility.ALLOWED,
                detail="Eligible for transaction (already registered)",
                tracking_id=tracking_id,
            )

        # リクエストから取引情報を構築
        price = to_decimal(request.amount)
        calculated_amount = self._calculate_amount_with_tax(
            price=price,
            tax_rate=request.tax_rate,
            tax_classification=request.tax_classification.value,
        )
        transaction_infos = [TransactionInfo(
            data_id=f"non-fee-model-{tracking_id}",
            amount=calculated_amount,
            tax_rate=request.tax_rate,
            order_date=request.completed_at.date() if request.completed_at else date.today(),
        )]

        # DBから企業情報を取得してdeliveryInfoを構築
        payment_user = self.payment_user_repo.get_by_identifiers(
            payment_service_id=request.payment_service_id,
            consumer_id=request.consumer_id,
            provider_id=request.provider_id,
        )
        delivery_info = self._build_delivery_info(payment_user) if payment_user else None

        # external_buyer_idを取得
        external_buyer_id = payment_user.external_buyer_id if payment_user else None
        if not external_buyer_id:
            logger.warning(
                "external_buyer_id not set for consumer (non fee model)",
                payment_service_id=payment_service_id,
                consumer_id=request.consumer_id,
                provider_id=request.provider_id,
            )
            return TransactionEligibilityNonFeeModelResponse(
                status=TransactionEligibility.DENIED,
                detail="external_buyer_id is not set for this consumer"
            )

        logger.info(
            "Registering transaction with NP Kakebarai for eligibility check (non fee model)",
            payment_service_id=payment_service_id,
            consumer_id=request.consumer_id,
            amount=request.amount,
        )

        try:
            # NP掛け払い取引登録APIを呼び出し
            registration_result = await self.payment_client.register_transaction(
                external_buyer_id=external_buyer_id,
                payment_service_id=payment_service_id,
                shop_transaction_id=tracking_id,
                transactions=transaction_infos,
                tracking_id=tracking_id,
                delivery_info=delivery_info,
            )

            if not registration_result.is_success:
                logger.warning(
                    "Transaction denied - NP Kakebarai authori NG (non fee model)",
                    provider_id=request.provider_id,
                    consumer_id=request.consumer_id,
                    payment_service_id=payment_service_id,
                    authori_result=registration_result.authori_result,
                    authori_ng_reason=registration_result.authori_ng_reason,
                    detail=registration_result.detail,
                )
                await self._cancel_external_transactions_best_effort(
                    payment_service_id=payment_service_id,
                    external_transaction_ids=registration_result.registered_external_transaction_ids,
                    reason="authori NG (non fee model)",
                )
                return TransactionEligibilityNonFeeModelResponse(
                    status=TransactionEligibility.DENIED,
                    detail=f"Transaction authori failed: {registration_result.detail}"
                )

            logger.info(
                "NP Kakebarai transaction registration passed (non fee model)",
                payment_service_id=payment_service_id,
                consumer_id=request.consumer_id,
                authori_result=registration_result.authori_result,
                external_transaction_id=registration_result.external_transaction_id,
            )

            try:
                # NP取引登録成功後、Transactionレコードを事前登録
                pre_reg_price = to_decimal(request.amount)
                pre_reg_amount = self._calculate_amount_with_tax(
                    price=pre_reg_price,
                    tax_rate=request.tax_rate,
                    tax_classification=request.tax_classification.value,
                )

                transaction = Transaction(
                    tracking_id=tracking_uuid,
                    external_transaction_id=registration_result.external_transaction_id,
                    fee_model_history_id=None,  # 利用料モデル無し
                    # 与信に使った決済サービスを確定時に照合できるよう保存する
                    payment_service_user_id=payment_user.payment_service_user_id,
                    provider_id=request.provider_id,
                    consumer_id=request.consumer_id,
                    data_id=None,  # 利用料モデル無し
                    snapshot_price=pre_reg_price,
                    snapshot_tax_rate=request.tax_rate,
                    snapshot_tax_classification=request.tax_classification.value,
                    calculated_amount=pre_reg_amount,
                    consumer_exchange_status="pending",
                    provider_exchange_status="pending",
                    l2_http_status="pending",
                )
                self.transaction_repo.create(transaction)
                self.db.commit()

            except Exception as e:
                # NP側には取引が登録済みだがローカル保存に失敗した。
                # 再試行で二重登録にならないよう NP側の取引を取り消す（補償処理）。
                self.db.rollback()
                logger.error(
                    "Local transaction save failed after external registration - cancelling external transactions",
                    payment_service_id=payment_service_id,
                    external_transaction_ids=registration_result.registered_external_transaction_ids,
                    error_type=type(e).__name__,
                    error=str(e),
                    exc_info=True,
                )
                await self._cancel_external_transactions_best_effort(
                    payment_service_id=payment_service_id,
                    external_transaction_ids=registration_result.registered_external_transaction_ids,
                    reason="local save failed after external registration (non fee model)",
                )
                raise

            logger.info(
                "Transaction record pre-registered (non fee model)",
                provider_id=request.provider_id,
                consumer_id=request.consumer_id,
                external_transaction_id=registration_result.external_transaction_id,
            )

        except PaymentClientError as e:
            logger.error(
                "NP Kakebarai transaction registration API error (non fee model)",
                provider_id=request.provider_id,
                consumer_id=request.consumer_id,
                payment_service_id=payment_service_id,
                error=str(e),
            )
            return TransactionEligibilityNonFeeModelResponse(
                status=TransactionEligibility.DENIED,
                detail=f"Transaction registration error: {str(e)}"
            )

        logger.info(
            "Transaction eligibility check passed (non fee model)",
            provider_id=request.provider_id,
            consumer_id=request.consumer_id,
            payment_service_id=payment_service_id,
        )

        return TransactionEligibilityNonFeeModelResponse(
            status=TransactionEligibility.ALLOWED,
            detail="Eligible for transaction",
            tracking_id=tracking_id,
        )

    async def register_data_exchange(
        self,
        request: DataExchangeRequest,
        tracking_id_header: Optional[str] = None,
        operator_id: Optional[str] = None,
    ) -> DataExchangeStatusResponse:
        """データ交換状態を登録

        トランザクション作成後、決済サービスが取引登録をサポートしている場合は
        外部決済サービスに取引登録を実行する。

        Args:
            request: データ交換リクエスト
            tracking_id_header: HTTPヘッダーのトラッキングID（外部API呼び出し用）
            operator_id: 登録者のID（トークンから取得したoperator_id）

        Returns:
            DataExchangeStatusResponse: 登録結果
        """
        logger.info(
            "Starting data exchange registration",
            tracking_id=request.tracking_id,
            provider_id=request.provider_id,
            consumer_id=request.consumer_id,
            data_id_count=len(request.data_id_list),
            status=request.status.value,
            operator_id=operator_id
        )

        try:
            tracking_id = UUID(request.tracking_id)
            existing_records = self.transaction_repo.get_all_by_tracking_id(tracking_id)

            # 既存レコードがある場合（取引可否APIによる事前登録、または2番目の登録者）
            if existing_records:
                existing = existing_records[0]

                # provider_id/consumer_idの整合性チェック
                if existing.provider_id != request.provider_id or existing.consumer_id != request.consumer_id:
                    logger.warning(
                        "Provider/Consumer ID mismatch for existing tracking_id",
                        tracking_id=str(tracking_id),
                        existing_provider_id=existing.provider_id,
                        existing_consumer_id=existing.consumer_id,
                        request_provider_id=request.provider_id,
                        request_consumer_id=request.consumer_id,
                    )
                    raise ResourceConflictError(
                        message=f"Provider/Consumer ID mismatch for existing tracking_id: {tracking_id}",
                        resource="Transaction"
                    )

                # operator_idに基づいて更新対象のステータスカラムを判定
                new_status = "completed" if request.status == DataExchangeStatus.COMPLETED else "failed"

                if operator_id == request.provider_id:
                    target_column = "provider_exchange_status"
                elif operator_id == request.consumer_id:
                    target_column = "consumer_exchange_status"
                else:
                    logger.warning(
                        "operator_id does not match provider_id or consumer_id",
                        tracking_id=str(tracking_id),
                        operator_id=operator_id,
                    )
                    raise BusinessRuleViolationError(
                        message="operator_id does not match provider_id or consumer_id",
                        rule="operator_id_match"
                    )

                # リクエストで指定された data_id のレコードのみ更新対象にする。
                # tracking_id 配下の全レコードを更新すると、報告されていないデータまで
                # completed になり請求対象になってしまう。
                requested_data_ids = set(request.data_id_list)
                stored_data_ids = {r.data_id for r in existing_records}
                unknown_data_ids = sorted(requested_data_ids - stored_data_ids)
                if unknown_data_ids:
                    logger.warning(
                        "Requested data_id not found under tracking_id",
                        tracking_id=str(tracking_id),
                        unknown_data_ids=unknown_data_ids,
                    )
                    raise RecordNotFoundError(
                        entity="transaction",
                        identifier=f"tracking_id={tracking_id}, data_id={', '.join(unknown_data_ids)}"
                    )
                target_records = [r for r in existing_records if r.data_id in requested_data_ids]

                # 対象レコードのステータスを更新
                for record in target_records:
                    current_status = getattr(record, target_column)
                    if current_status == "failed" and new_status == "completed":
                        logger.warning(
                            "Status mismatch: cannot change from failed to completed",
                            tracking_id=str(tracking_id),
                            column=target_column,
                            existing_status=current_status,
                            new_status=new_status,
                            operator_id=operator_id
                        )
                        raise ResourceConflictError(
                            message=f"Status mismatch: existing={current_status}, new={new_status}. Cannot change from failed to completed.",
                            resource="Transaction"
                        )
                    if current_status == "pending":
                        setattr(record, target_column, new_status)
                        self.transaction_repo.update(record)

                self.db.commit()

                logger.info(
                    "Data exchange registration completed (existing record updated)",
                    tracking_id=str(tracking_id),
                    operator_id=operator_id,
                    updated_column=target_column,
                    new_status=new_status,
                    updated_count=len(target_records),
                )

                return DataExchangeStatusResponse(
                    status=ProcessStatus.SUCCESS,
                    detail=f"Data exchange completed for tracking_id: {tracking_id}"
                )

            # 取引可否APIで事前登録されたレコードは tracking_id 付きで保存されるため、
            # 上の「既存レコードあり」分岐で処理される（tracking_id=NULL の曖昧検索は行わない）。

            # 既存レコードがない場合: 最初の登録者として新規作成
            # 各データIDについてトランザクションを作成
            transaction_count = 0

            for data_id in request.data_id_list:
                # アクティブな利用料モデルを取得
                fee_model = self.fee_model_repo.get_active_by_identifiers(
                    provider_id=request.provider_id,
                    consumer_id=request.consumer_id,
                    data_id=data_id
                )

                if not fee_model:
                    logger.warning(
                        "Fee model not found for data exchange",
                        provider_id=request.provider_id,
                        consumer_id=request.consumer_id,
                        data_id=data_id
                    )
                    raise RecordNotFoundError(
                        entity="fee_model",
                        identifier=f"provider={request.provider_id}, consumer={request.consumer_id}, data={data_id}"
                    )

                # 履歴としてスナップショットを作成
                history = self.history_repo.create_snapshot(
                    fee_model=fee_model,
                    change_type="snapshot",
                    change_reason=f"Transaction created: {tracking_id}"
                )

                # 決済サービスユーザ登録を取得（事前登録が必要）
                payment_user = self.payment_user_repo.get_by_identifiers(
                    payment_service_id=fee_model.payment_service_id,
                    consumer_id=request.consumer_id,
                    provider_id=request.provider_id
                )
                if not payment_user and settings.EXTERNAL_PAYMENT_ENABLED:
                    raise RecordNotFoundError(
                        entity="Payment Service User Registration",
                        identifier=f"payment_service={fee_model.payment_service_id}, consumer={request.consumer_id}, provider={request.provider_id}"
                    )

                # 金額を計算（税込）
                calculated_amount = self._calculate_amount_with_tax(
                    price=fee_model.price,
                    tax_rate=fee_model.tax_rate,
                    tax_classification=fee_model.tax_classification
                )

                # operator_idに基づいてステータスを決定
                exchange_status = "completed" if request.status == DataExchangeStatus.COMPLETED else "failed"
                if operator_id == request.provider_id:
                    consumer_status = "pending"
                    provider_status = exchange_status
                elif operator_id == request.consumer_id:
                    consumer_status = exchange_status
                    provider_status = "pending"
                else:
                    # operator_idがprovider/consumerどちらにも一致しない場合はデフォルト
                    consumer_status = exchange_status
                    provider_status = exchange_status

                # トランザクションを作成
                transaction = Transaction(
                    tracking_id=tracking_id,
                    fee_model_history_id=history.fee_model_history_id,
                    payment_service_user_id=payment_user.payment_service_user_id if payment_user else None,
                    provider_id=request.provider_id,
                    consumer_id=request.consumer_id,
                    data_id=data_id,
                    snapshot_price=fee_model.price,
                    snapshot_tax_rate=fee_model.tax_rate,
                    snapshot_tax_classification=fee_model.tax_classification,
                    calculated_amount=calculated_amount,
                    consumer_exchange_status=consumer_status,
                    provider_exchange_status=provider_status,
                    l2_http_status="pending",
                    created_at=request.completed_at
                )

                self.transaction_repo.create(transaction)
                transaction_count += 1

            self.db.commit()

            logger.info(
                "Data exchange registration completed successfully",
                tracking_id=str(tracking_id),
                transaction_count=transaction_count,
                status=request.status.value
            )

            return DataExchangeStatusResponse(
                status=ProcessStatus.SUCCESS,
                detail="Data exchange status registered"
            )

        except RecordNotFoundError as e:
            self.db.rollback()
            logger.error(
                "Data exchange registration failed due to validation error",
                tracking_id=request.tracking_id,
                error_type=type(e).__name__,
                error_message=str(e)
            )
            raise
        except PaymentClientError as e:
            # 外部決済サービスAPI呼び出しエラー（DBコミット後なのでロールバック不要）
            logger.error(
                "External payment transaction registration failed",
                tracking_id=request.tracking_id,
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True
            )
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(
                "Unexpected error during data exchange registration",
                tracking_id=request.tracking_id,
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True
            )
            raise

    def update_data_exchange_status(
        self,
        request: DataExchangeStatusRequest,
        operator_id: Optional[str] = None,
    ) -> DataExchangeStatusResponse:
        """データ交換状態を更新

        Args:
            request: ステータス更新リクエスト
            operator_id: 操作者のID（トークンから取得、provider_id or consumer_id）
        """
        logger.info(
            "Starting data exchange status update",
            tracking_id=request.tracking_id,
            new_status=request.status.value,
            operator_id=operator_id
        )

        try:
            tracking_id = UUID(request.tracking_id)
            transactions = self.transaction_repo.get_all_by_tracking_id(tracking_id)

            if not transactions:
                raise RecordNotFoundError(
                    entity="transaction",
                    identifier=tracking_id
                )

            first = transactions[0]
            new_status = "completed" if request.status == DataExchangeStatus.COMPLETED else "failed"

            # operator_idに基づいて更新対象を判定
            # 取引当事者（provider/consumer）以外のオペレータによる更新は拒否する
            if operator_id == first.provider_id:
                target_column = "provider_exchange_status"
            elif operator_id == first.consumer_id:
                target_column = "consumer_exchange_status"
            else:
                logger.warning(
                    "operator_id does not match provider_id or consumer_id",
                    tracking_id=str(tracking_id),
                    operator_id=operator_id,
                    provider_id=first.provider_id,
                    consumer_id=first.consumer_id,
                )
                raise BusinessRuleViolationError(
                    message="operator_id does not match provider_id or consumer_id",
                    rule="operator_id_match"
                )

            old_status = None
            for transaction in transactions:
                old_status = getattr(transaction, target_column)
                # failed -> completed への遷移は許可しない（POST側と同じガード）
                if old_status == "failed" and new_status == "completed":
                    logger.warning(
                        "Status mismatch: cannot change from failed to completed",
                        tracking_id=str(tracking_id),
                        column=target_column,
                        existing_status=old_status,
                        new_status=new_status,
                        operator_id=operator_id
                    )
                    raise ResourceConflictError(
                        message=f"Status mismatch: existing={old_status}, new={new_status}. Cannot change from failed to completed.",
                        resource="Transaction"
                    )
                setattr(transaction, target_column, new_status)
                self.transaction_repo.update(transaction)

            self.db.commit()

            logger.info(
                "Data exchange status updated successfully",
                tracking_id=str(tracking_id),
                column=target_column,
                old_status=old_status,
                new_status=new_status
            )

            return DataExchangeStatusResponse(
                status=ProcessStatus.SUCCESS,
                detail="Data exchange status updated"
            )

        except (RecordNotFoundError, BusinessRuleViolationError, ResourceConflictError) as e:
            self.db.rollback()
            logger.warning(
                "Data exchange status update failed - validation error",
                tracking_id=request.tracking_id,
                error_type=type(e).__name__,
                error_message=str(e)
            )
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(
                "Unexpected error during data exchange status update",
                tracking_id=request.tracking_id,
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True
            )
            raise

    def _calculate_amount_with_tax(
        self,
        price: Decimal,
        tax_rate: Decimal,
        tax_classification: str
    ) -> Decimal:
        """税込金額を計算（円単位に丸め。app.utils.money に集約）"""
        amount = calculate_amount_with_tax(price, tax_rate, tax_classification)
        logger.debug(
            "Calculated amount",
            price=str(price),
            tax_rate=str(tax_rate),
            tax_classification=tax_classification,
            calculated_amount=str(amount)
        )
        return amount

    async def _cancel_external_transactions_best_effort(
        self,
        payment_service_id: str,
        external_transaction_ids: list[str],
        reason: str,
    ) -> None:
        """外部決済サービスに採番済みの取引をキャンセルする（失敗しても例外にしない）

        取引可否 NG やローカル保存失敗など、外部側だけに取引が残る状況を避けるための
        補償処理。失敗した場合は ERROR ログを出し、運用で復旧する。
        """
        ids = [i for i in external_transaction_ids if i]
        if not ids:
            return
        try:
            await self.payment_client.cancel_transaction(
                payment_service_id=payment_service_id,
                np_transaction_ids=ids,
            )
            logger.info(
                "External transactions cancelled (compensation)",
                payment_service_id=payment_service_id,
                external_transaction_ids=ids,
                reason=reason,
            )
        except Exception as e:  # noqa: BLE001 - 補償処理は呼び出し元の結果を変えない
            logger.error(
                "Failed to cancel external transactions (compensation) - manual recovery required",
                payment_service_id=payment_service_id,
                external_transaction_ids=ids,
                reason=reason,
                error=str(e),
                exc_info=True,
            )

    def _build_delivery_info(
        self,
        payment_user: PaymentServiceUserRegistration,
    ) -> Optional[dict]:
        """PaymentServiceUserRegistrationから deliveryInfo を構築"""
        if not payment_user.company_name:
            return None

        # NP掛け払いAPIはzipCode・telNoをハイフンなし数字のみで要求
        zip_code = (payment_user.zip_code or "").replace("-", "")
        tel_no = (payment_user.tel_no or "").replace("-", "")
        return {
            "companyName": payment_user.company_name or "",
            "department": payment_user.department or "",
            "customerName": payment_user.customer_name or "",
            "zipCode": zip_code,
            "address": payment_user.address or "",
            "telNo": tel_no,
        }

    async def _request_billing_if_applicable(
        self,
        payment_service_id: str,
        records: list[Transaction],
    ) -> None:
        """条件を満たす場合にNP掛け払い請求確定依頼を実行する

        以下の条件をすべて満たす場合のみ請求確定依頼を実行:
        1. EXTERNAL_PAYMENT_ENABLED が True
        2. supports_transaction_registration(payment_service_id) が True
        3. レコードに external_transaction_id が存在する

        成功時: 各レコードの request_date を現在時刻に更新
        失敗時: PaymentClientError をそのまま raise
        """
        if not settings.EXTERNAL_PAYMENT_ENABLED:
            logger.info(
                "External payment disabled, skipping billing request",
                payment_service_id=payment_service_id,
            )
            return

        if not self.payment_client.supports_transaction_registration(payment_service_id):
            logger.info(
                "Billing request not supported for payment service, skipping",
                payment_service_id=payment_service_id,
            )
            return

        # external_transaction_id を持つレコードを抽出
        np_transaction_ids = list({
            r.external_transaction_id
            for r in records
            if r.external_transaction_id
        })

        if not np_transaction_ids:
            logger.info(
                "No external_transaction_id found, skipping billing request",
                payment_service_id=payment_service_id,
            )
            return

        logger.info(
            "Requesting NP billing confirmation",
            payment_service_id=payment_service_id,
            np_transaction_ids=np_transaction_ids,
        )

        await self.payment_client.request_billing(
            payment_service_id=payment_service_id,
            np_transaction_ids=np_transaction_ids,
        )

        # 請求確定成功 → request_date を更新（CronJobでの重複請求防止）
        now = datetime.now(timezone.utc)
        for record in records:
            if record.external_transaction_id:
                record.request_date = now
                self.transaction_repo.update(record)

        logger.info(
            "NP billing request succeeded, request_date updated",
            payment_service_id=payment_service_id,
            np_transaction_ids=np_transaction_ids,
        )

    def _ensure_confirm_matches_pre_registration(
        self,
        record: Transaction,
        request: DataExchangeNonFeeModelRequest,
        tracking_id: UUID,
    ) -> None:
        """確定リクエストの内容が与信時（事前登録）の内容と一致することを検証する

        決済サービス・金額・税率・税区分が与信時と異なる確定は受け付けない。
        （異なる値を許すと、NP側の与信・取引登録と異なる金額がローカルで請求対象になる）
        """
        mismatches = []
        if record.payment_user is not None and \
                record.payment_user.payment_service_id != request.payment_service_id:
            mismatches.append(
                f"payment_service_id: registered={record.payment_user.payment_service_id}, "
                f"requested={request.payment_service_id}"
            )
        if record.snapshot_price != to_decimal(request.amount):
            mismatches.append(f"amount: registered={record.snapshot_price}, requested={request.amount}")
        if record.snapshot_tax_rate != to_decimal(request.tax_rate):
            mismatches.append(f"tax_rate: registered={record.snapshot_tax_rate}, requested={request.tax_rate}")
        if record.snapshot_tax_classification != request.tax_classification.value:
            mismatches.append(
                f"tax_classification: registered={record.snapshot_tax_classification}, "
                f"requested={request.tax_classification.value}"
            )
        if mismatches:
            logger.warning(
                "Confirmation request does not match pre-registered transaction",
                tracking_id=str(tracking_id),
                mismatches=mismatches,
            )
            raise ResourceConflictError(
                message="Confirmation does not match the pre-registered transaction: " + "; ".join(mismatches),
                resource="Transaction",
            )

    async def confirm_data_exchange_non_fee_model(
        self,
        request: DataExchangeNonFeeModelRequest,
        tracking_id_header: Optional[str] = None,
    ) -> DataExchangeStatusNonFeeModelResponse:
        """データ交換取引金額確定（利用料モデル無し）

        利用料モデルを使用せず、リクエストで直接指定された金額で
        トランザクションを作成し、外部決済サービスに取引登録を実行する。

        Args:
            request: データ交換リクエスト（利用料モデル無し）
            tracking_id_header: HTTPヘッダーのトラッキングID（外部API呼び出し用）

        Returns:
            DataExchangeStatusNonFeeModelResponse: 登録結果
        """
        # tracking_id はリクエスト本文（設計上の想定）または X-TrackingId ヘッダから取得する。
        # 取引可否確認API（利用料モデルなし）が事前登録した行と同じ tracking_id を指定すること。
        tracking_id = UUID(request.tracking_id or tracking_id_header)

        logger.info(
            "Starting data exchange confirmation (non fee model)",
            tracking_id=str(tracking_id),
            provider_id=request.provider_id,
            consumer_id=request.consumer_id,
            payment_service_id=str(request.payment_service_id),
            amount=request.amount,
        )

        billed_external_ids: list[str] = []
        try:
            # 取引可否確認API（利用料モデルなし）で事前登録されたレコードを tracking_id で検索
            pre_registered = [
                r for r in self.transaction_repo.get_all_by_tracking_id(tracking_id)
                if r.data_id is None
            ]

            if pre_registered:
                for record in pre_registered:
                    if record.provider_id != request.provider_id or record.consumer_id != request.consumer_id:
                        raise ResourceConflictError(
                            message=f"Provider/Consumer ID mismatch for existing tracking_id: {tracking_id}",
                            resource="Transaction",
                        )
                    self._ensure_confirm_matches_pre_registration(record, request, tracking_id)

                # 請求確定に使う決済サービスは与信時に保存した値を正とする
                stored_service_ids = {
                    str(r.payment_user.payment_service_id) for r in pre_registered if r.payment_user
                }
                billing_payment_service_id = (
                    stored_service_ids.pop() if len(stored_service_ids) == 1 else str(request.payment_service_id)
                )

                # 既に確定済み（再送）の場合は再登録・再請求せず成功を返す（冪等）
                if all(
                    r.consumer_exchange_status == "completed" and r.provider_exchange_status == "completed"
                    for r in pre_registered
                ):
                    logger.info(
                        "Data exchange already confirmed for tracking_id (idempotent)",
                        tracking_id=str(tracking_id),
                    )
                    return DataExchangeStatusNonFeeModelResponse(
                        status=ProcessStatus.SUCCESS,
                        detail="Data exchange already confirmed"
                    )

                for record in pre_registered:
                    record.consumer_exchange_status = "completed"
                    record.provider_exchange_status = "completed"
                    self.transaction_repo.update(record)

                self.db.flush()

                logger.info(
                    "Pre-registered transaction updated (non fee model)",
                    tracking_id=str(tracking_id),
                    updated_count=len(pre_registered),
                )

                # NP掛け払い請求確定依頼
                await self._request_billing_if_applicable(
                    payment_service_id=billing_payment_service_id,
                    records=pre_registered,
                )
                billed_external_ids = [r.external_transaction_id for r in pre_registered if r.request_date]
            else:
                # 外部決済サービスへの取引登録が必要なサービスでは、取引可否API による
                # 事前登録（与信・NP取引登録）が無い取引を確定させない
                # （NP に取引が無いままローカルだけ請求対象になるのを防ぐ）
                payment_service_id = str(request.payment_service_id)
                if (
                    settings.EXTERNAL_PAYMENT_ENABLED
                    and self.payment_client.supports_transaction_registration(payment_service_id)
                ):
                    logger.warning(
                        "No pre-registered transaction for tracking_id - eligibility check required",
                        tracking_id=str(tracking_id),
                        payment_service_id=payment_service_id,
                    )
                    raise RecordNotFoundError(
                        entity="pre-registered transaction (eligibility check required)",
                        identifier=str(tracking_id),
                    )

                # 外部決済サービス連携が不要な場合のみ新規作成
                price = to_decimal(request.amount)
                calculated_amount = self._calculate_amount_with_tax(
                    price=price,
                    tax_rate=request.tax_rate,
                    tax_classification=request.tax_classification.value,
                )

                transaction = Transaction(
                    tracking_id=tracking_id,
                    fee_model_history_id=None,  # 利用料モデル無し
                    payment_service_user_id=None,  # 利用料モデル無し
                    provider_id=request.provider_id,
                    consumer_id=request.consumer_id,
                    data_id=None,  # 利用料モデル無し
                    snapshot_price=price,
                    snapshot_tax_rate=request.tax_rate,
                    snapshot_tax_classification=request.tax_classification.value,
                    calculated_amount=calculated_amount,
                    consumer_exchange_status="completed",
                    provider_exchange_status="completed",
                    l2_http_status="pending",
                    created_at=request.completed_at,
                )

                self.transaction_repo.create(transaction)
                self.db.flush()

                logger.info(
                    "Transaction created successfully (non fee model)",
                    tracking_id=str(tracking_id),
                    calculated_amount=str(calculated_amount),
                )

            self.db.commit()

            return DataExchangeStatusNonFeeModelResponse(
                status=ProcessStatus.SUCCESS,
                detail="Data exchange confirmed"
            )

        except (RecordNotFoundError, ResourceConflictError, BusinessRuleViolationError) as e:
            self.db.rollback()
            logger.warning(
                "Data exchange confirmation failed - validation error",
                tracking_id=str(tracking_id),
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise
        except PaymentClientError as e:
            self.db.rollback()
            logger.error(
                "External payment billing request failed",
                tracking_id=str(tracking_id),
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True
            )
            raise
        except Exception as e:
            self.db.rollback()
            if billed_external_ids:
                # 請求確定はNP側で完了しているがローカル保存に失敗した。
                # 請求確定の取り消しAPIは無いため、手動復旧が必要な旨を記録する。
                logger.error(
                    "Local save failed after NP billing request succeeded - manual recovery required",
                    tracking_id=str(tracking_id),
                    external_transaction_ids=billed_external_ids,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
            logger.error(
                "Unexpected error during data exchange confirmation (non fee model)",
                tracking_id=str(tracking_id),
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True
            )
            raise
