"""
FeeModelService - 利用料モデルサービス

利用料モデルのCRUD操作を担当
"""

import time
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.custom_exceptions import (
    RecordNotFoundError,
    OptimisticLockError,
    ResourceConflictError,
)
from app.core.logging import get_logger
from app.models.payment_model import FeeModel
from app.schemas.fee_model_schema import (
    FeeModelCreateRequest,
    FeeModelUpdateRequest,
    FeeModelResponse,
    FeeModelListResponse,
)
from app.repositories.payment_repository import (
    FeeModelRepository,
    FeeModelHistoryRepository,
    PaymentServiceRepository,
)

logger = get_logger(__name__)


class FeeModelService:
    """利用料モデルサービス"""

    def __init__(self, db: Session):
        self.db = db
        self.fee_model_repo = FeeModelRepository(db)
        self.history_repo = FeeModelHistoryRepository(db)
        self.payment_service_repo = PaymentServiceRepository(db)
        logger.debug("FeeModelService initialized")

    def create_fee_model(
        self,
        request: FeeModelCreateRequest
    ) -> FeeModelResponse:
        """利用料モデルを作成"""
        start_time = time.time()

        logger.info(
            "Starting fee model creation",
            provider_id=request.provider_id,
            consumer_id=request.consumer_id,
            data_id=request.data_id,
            is_active=request.is_active,
            price=str(request.price)
        )

        try:
            # 決済サービスの存在確認（事前登録が必要）
            payment_service = self.payment_service_repo.get_by_id(
                request.payment_service_id
            )
            if not payment_service:
                logger.warning(
                    "Payment service not found",
                    payment_service_id=str(request.payment_service_id)
                )
                raise RecordNotFoundError(
                    entity="Payment Service",
                    identifier=str(request.payment_service_id)
                )

            # 同じ識別子でアクティブなモデルが存在しないかチェック
            if request.is_active:
                exists = self.fee_model_repo.check_active_exists(
                    provider_id=request.provider_id,
                    consumer_id=request.consumer_id,
                    data_id=request.data_id
                )
                if exists:
                    logger.warning(
                        "Active fee model already exists",
                        provider_id=request.provider_id,
                        consumer_id=request.consumer_id,
                        data_id=request.data_id
                    )
                    raise ResourceConflictError(
                        message="Active fee model already exists for this identifier",
                        resource=f"provider={request.provider_id}, consumer={request.consumer_id}, data={request.data_id}"
                    )

            # 利用料モデルを作成
            fee_model = FeeModel(
                fee_model_name=request.fee_model_name,
                price=request.price,
                tax_classification=request.tax_classification.value,
                tax_rate=request.tax_rate,
                provider_id=request.provider_id,
                consumer_id=request.consumer_id,
                data_id=request.data_id,
                payment_service_id=request.payment_service_id,
                storage_type=request.storage_type.value if request.storage_type else "provider_env",
                storage_key=request.storage_key or "",
                valid_from=request.valid_from,
                valid_to=request.valid_to,
                is_active=request.is_active,
                version=request.version
            )

            fee_model = self.fee_model_repo.create(fee_model)

            # 履歴を作成
            self.history_repo.create_snapshot(
                fee_model=fee_model,
                change_type="create",
                change_reason="Created"
            )

            self.db.commit()

            execution_time = time.time() - start_time
            logger.info(
                "Fee model creation completed successfully",
                fee_model_id=str(fee_model.fee_model_id),
                fee_model_name=fee_model.fee_model_name,
                provider_id=fee_model.provider_id,
                execution_time=f"{execution_time:.3f}s"
            )

            return FeeModelResponse.model_validate(fee_model)

        except (RecordNotFoundError, ResourceConflictError) as e:
            self.db.rollback()
            execution_time = time.time() - start_time
            logger.error(
                "Fee model creation failed due to business rule violation",
                error_type=type(e).__name__,
                error_message=str(e),
                provider_id=request.provider_id,
                consumer_id=request.consumer_id,
                execution_time=f"{execution_time:.3f}s"
            )
            raise
        except Exception as e:
            self.db.rollback()
            execution_time = time.time() - start_time
            logger.error(
                "Unexpected error during fee model creation",
                error_type=type(e).__name__,
                error_message=str(e),
                provider_id=request.provider_id,
                execution_time=f"{execution_time:.3f}s",
                exc_info=True
            )
            raise

    def list_fee_models(
        self,
        skip: int = 0,
        limit: int = 20,
        provider_id: Optional[str] = None,
        consumer_id: Optional[str] = None,
        is_active: Optional[bool] = None
    ) -> FeeModelListResponse:
        """利用料モデル一覧を取得"""
        logger.debug(
            "Listing fee models",
            skip=skip,
            limit=limit,
            provider_id=provider_id,
            consumer_id=consumer_id,
            is_active=is_active
        )

        models, total = self.fee_model_repo.list_all(
            skip=skip,
            limit=limit,
            provider_id=provider_id,
            consumer_id=consumer_id,
            is_active=is_active
        )

        logger.info(
            "Fee models list retrieved successfully",
            total_count=total,
            returned_count=len(models),
            skip=skip,
            limit=limit
        )

        return FeeModelListResponse(
            models=[FeeModelResponse.model_validate(m) for m in models],

        )

    def update_fee_model(
        self,
        fee_model_id: UUID,
        request: FeeModelUpdateRequest
    ) -> FeeModelResponse:
        """利用料モデルを更新"""
        start_time = time.time()

        logger.info(
            "Starting fee model update",
            fee_model_id=str(fee_model_id),
            requested_version=request.version
        )

        try:
            fee_model = self.fee_model_repo.get_by_id_or_raise(fee_model_id)

            # 楽観的ロックのチェック
            if request.version is not None and fee_model.version != request.version:
                logger.warning(
                    "Optimistic lock error - version mismatch",
                    fee_model_id=str(fee_model_id),
                    expected_version=request.version,
                    actual_version=fee_model.version
                )
                raise OptimisticLockError(
                    entity="fee_model",
                    expected_version=request.version,
                    actual_version=fee_model.version
                )

            # アクティブ化する場合、他のアクティブなモデルをチェック
            if request.is_active and not fee_model.is_active:
                exists = self.fee_model_repo.check_active_exists(
                    provider_id=fee_model.provider_id,
                    consumer_id=fee_model.consumer_id,
                    data_id=fee_model.data_id,
                    exclude_id=fee_model_id
                )
                if exists:
                    logger.warning(
                        "Active fee model already exists for this identifier",
                        fee_model_id=str(fee_model_id),
                        provider_id=fee_model.provider_id,
                        consumer_id=fee_model.consumer_id,
                        data_id=fee_model.data_id
                    )
                    raise ResourceConflictError(
                        message="An active fee model with the same identifier already exists",
                        resource=f"fee_model_id={fee_model_id}"
                    )

            # 更新前の状態を履歴に保存
            self.history_repo.create_snapshot(
                fee_model=fee_model,
                change_type="update",
                change_reason="Pre-update snapshot"
            )

            # フィールドを更新
            updated_fields = []
            if request.fee_model_name is not None:
                fee_model.fee_model_name = request.fee_model_name
                updated_fields.append("fee_model_name")
            if request.price is not None:
                fee_model.price = request.price
                updated_fields.append("price")
            if request.tax_classification is not None:
                fee_model.tax_classification = request.tax_classification.value
                updated_fields.append("tax_classification")
            if request.tax_rate is not None:
                fee_model.tax_rate = request.tax_rate
                updated_fields.append("tax_rate")
            if request.storage_type is not None:
                fee_model.storage_type = request.storage_type.value
                updated_fields.append("storage_type")
            if request.storage_key is not None:
                fee_model.storage_key = request.storage_key
                updated_fields.append("storage_key")
            if request.valid_from is not None:
                fee_model.valid_from = request.valid_from
                updated_fields.append("valid_from")
            if request.valid_to is not None:
                fee_model.valid_to = request.valid_to
                updated_fields.append("valid_to")
            if request.is_active is not None:
                fee_model.is_active = request.is_active
                updated_fields.append("is_active")

            # バージョンをインクリメント
            fee_model.version += 1

            fee_model = self.fee_model_repo.update(fee_model)
            self.db.commit()

            execution_time = time.time() - start_time
            logger.info(
                "Fee model update completed successfully",
                fee_model_id=str(fee_model_id),
                updated_fields=updated_fields,
                new_version=fee_model.version,
                execution_time=f"{execution_time:.3f}s"
            )

            return FeeModelResponse.model_validate(fee_model)

        except (RecordNotFoundError, OptimisticLockError, ResourceConflictError) as e:
            self.db.rollback()
            execution_time = time.time() - start_time
            logger.error(
                "Fee model update failed due to business rule violation",
                fee_model_id=str(fee_model_id),
                error_type=type(e).__name__,
                error_message=str(e),
                execution_time=f"{execution_time:.3f}s"
            )
            raise
        except Exception as e:
            self.db.rollback()
            execution_time = time.time() - start_time
            logger.error(
                "Unexpected error during fee model update",
                fee_model_id=str(fee_model_id),
                error_type=type(e).__name__,
                error_message=str(e),
                execution_time=f"{execution_time:.3f}s",
                exc_info=True
            )
            raise

    def delete_fee_model(self, fee_model_id: UUID) -> None:
        """利用料モデルを削除"""
        logger.info(
            "Starting fee model deletion",
            fee_model_id=str(fee_model_id)
        )

        try:
            fee_model = self.fee_model_repo.get_by_id_or_raise(fee_model_id)

            # 削除前の状態を履歴に保存
            self.history_repo.create_snapshot(
                fee_model=fee_model,
                change_type="delete",
                change_reason="Deleted"
            )

            self.fee_model_repo.delete(fee_model)
            self.db.commit()

            logger.info(
                "Fee model deletion completed successfully",
                fee_model_id=str(fee_model_id)
            )

        except RecordNotFoundError as e:
            self.db.rollback()
            logger.error(
                "Fee model deletion failed - not found",
                fee_model_id=str(fee_model_id),
                error_message=str(e)
            )
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(
                "Unexpected error during fee model deletion",
                fee_model_id=str(fee_model_id),
                error_type=type(e).__name__,
                error_message=str(e),
                exc_info=True
            )
            raise
