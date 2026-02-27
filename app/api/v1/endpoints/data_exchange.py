from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.clients.payment_client import PaymentClientError
from app.core.custom_exceptions import (
    RecordNotFoundError,
    DuplicateRecordError,
    ResourceConflictError,
    BusinessRuleViolationError,
    DatabaseError,
)
from app.core.logging import get_logger, set_request_id
from app.core.security import (
    verify_access_token,
    verify_request_headers,
    require_permission,
    verify_operator_id,
)
from app.db.session import get_db
from app.schemas.common import ErrorResponse
from app.schemas.fee_model_schema import (
    DataExchangeRequest,
    DataExchangeStatusRequest,
    DataExchangeStatusResponse,
    TransactionEligibilityRequest,
    TransactionEligibilityResponse,
    TransactionEligibilityNonFeeModelRequest,
    TransactionEligibilityNonFeeModelResponse,
    DataExchangeNonFeeModelRequest,
    DataExchangeStatusNonFeeModelResponse,
    ConsumerTransactionRequest,
    ConsumerTransactionListResponse,
)
from app.services.transaction_service import TransactionService
from app.services.payment_billing_service import PaymentBillingService

logger = get_logger(__name__)


router = APIRouter()

# ========================================
# データ交換関連エンドポイント
# ========================================

@router.post(
    "/data-exchange/transaction/eligibility",
    response_model=TransactionEligibilityResponse,
    summary="取引可否確認API",
    description="取引可否確認API",
    responses={
        status.HTTP_200_OK: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "リクエストエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"description": "データなし", "model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"description": "リソース競合エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def check_transaction_eligibility(
    request: TransactionEligibilityRequest,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("eligibility:post")),
    db: Session = Depends(get_db),
) -> TransactionEligibilityResponse:
    """取引可否を確認"""
    # リクエストIDを設定
    tracking_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(tracking_id)

    logger.info(
        "Received transaction eligibility check request",
        endpoint="/data-exchange/transaction/eligibility",
        method="POST",
        provider_id=request.provider_id,
        consumer_id=request.consumer_id,
        data_id_count=len(request.data_id_list)
    )

    # consumer_idとoperator_idの一致検証
    verify_operator_id(request.consumer_id, credential, "Consumer ID")

    service = TransactionService(db)
    try:
        # 与信確認を含む取引可否確認を実行
        result = await service.check_transaction_eligibility(
            request,
            tracking_id=tracking_id,
        )

        logger.info(
            "Transaction eligibility checked",
            status=result.status,
            status_code=200
        )

        return result

    except RecordNotFoundError as e:
        logger.warning(
            "Request failed - resource not found",
            error=str(e),
            status_code=404
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except DatabaseError as e:
        logger.error(
            "Request failed - database error",
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred"
        )
    except Exception as e:
        logger.error(
            "Request failed - unexpected error",
            error_type=type(e).__name__,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected error"
        )
    finally:
        await service.close()


@router.post(
    "/data-exchange/status",
    response_model=DataExchangeStatusResponse,
    summary="データ交換状態登録API",
    description="データ交換処理の完了を精算決済機能に登録します。",
    responses={
        status.HTTP_200_OK: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "リクエストエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"description": "データなし", "model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"description": "リソース競合エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def register_data_exchange_status(
    request: DataExchangeRequest,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("data-exchange:post")),
    db: Session = Depends(get_db),
) -> DataExchangeStatusResponse:
    """データ交換状態を登録

    トランザクション作成後、外部決済サービスが取引登録をサポートしている場合は
    取引登録APIを呼び出す。
    """
    request_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(request_id)

    logger.info(
        "Received data exchange status registration request",
        endpoint="/data-exchange/status",
        method="POST",
        tracking_id=request.tracking_id,
        provider_id=request.provider_id,
        consumer_id=request.consumer_id,
        status=request.status
    )

    # 登録者IDを取得
    operator_id = credential.get("token_info", {}).get("operator_id")

    # operator_idがconsumer_idまたはprovider_idのいずれかと一致することを検証
    # 開発モードまたは検証無効時はスキップ
    if not settings.IS_DEVELOP and settings.OPERATOR_ID_VERIFICATION_ENABLED:
        if operator_id != request.consumer_id and operator_id != request.provider_id:
            logger.warning(
                "Operator ID does not match consumer_id or provider_id",
                operator_id=operator_id,
                consumer_id=request.consumer_id,
                provider_id=request.provider_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operator ID does not match consumer_id or provider_id"
            )

    service = TransactionService(db)
    try:
        result = await service.register_data_exchange(
            request,
            tracking_id_header=request_id,
            operator_id=operator_id,
        )

        logger.info(
            "Data exchange status registered",
            tracking_id=request.tracking_id,
            result_status=result.status,
            status_code=200
        )

        return result

    except RecordNotFoundError as e:
        logger.warning(
            "Request failed - resource not found",
            tracking_id=request.tracking_id,
            error=str(e),
            status_code=404
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except DuplicateRecordError as e:
        logger.warning(
            "Request failed - duplicate record",
            tracking_id=request.tracking_id,
            error=str(e),
            status_code=409
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except ResourceConflictError as e:
        logger.warning(
            "Request failed - resource conflict",
            tracking_id=request.tracking_id,
            error=str(e),
            status_code=409
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except BusinessRuleViolationError as e:
        logger.warning(
            "Request failed - business rule violation",
            tracking_id=request.tracking_id,
            error=str(e),
            status_code=400
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except PaymentClientError as e:
        logger.error(
            "Request failed - external payment service error",
            tracking_id=request.tracking_id,
            error=str(e),
            status_code=502,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="External payment service error"
        )
    except DatabaseError as e:
        logger.error(
            "Request failed - database error",
            tracking_id=request.tracking_id,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred"
        )
    except Exception as e:
        logger.error(
            "Request failed - unexpected error",
            tracking_id=request.tracking_id,
            error_type=type(e).__name__,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected error"
        )
    finally:
        await service.close()


@router.put(
    "/data-exchange/status",
    response_model=DataExchangeStatusResponse,
    summary="データ交換状態更新API",
    description="データ交換処理のステータスを変更します。",
    responses={
        status.HTTP_200_OK: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "リクエストエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"description": "データなし", "model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"description": "リソース競合エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def update_data_exchange_status(
    request: DataExchangeStatusRequest,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("data-exchange:put")),
    db: Session = Depends(get_db),
) -> DataExchangeStatusResponse:
    """データ交換状態を更新"""
    request_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(request_id)

    # 登録者IDを取得
    operator_id = credential.get("token_info", {}).get("operator_id")

    logger.info(
        "Received data exchange status update request",
        endpoint="/data-exchange/status",
        method="PUT",
        tracking_id=request.tracking_id,
        status=request.status,
        operator_id=operator_id
    )

    service = TransactionService(db)
    try:
        result = service.update_data_exchange_status(request, operator_id=operator_id)

        logger.info(
            "Data exchange status updated",
            tracking_id=request.tracking_id,
            result_status=result.status,
            status_code=200
        )

        return result

    except RecordNotFoundError as e:
        logger.warning(
            "Request failed - resource not found",
            tracking_id=request.tracking_id,
            error=str(e),
            status_code=404
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except ResourceConflictError as e:
        logger.warning(
            "Request failed - resource conflict",
            tracking_id=request.tracking_id,
            error=str(e),
            status_code=409
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except BusinessRuleViolationError as e:
        logger.warning(
            "Request failed - business rule violation",
            tracking_id=request.tracking_id,
            error=str(e),
            status_code=400
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except DatabaseError as e:
        logger.error(
            "Request failed - database error",
            tracking_id=request.tracking_id,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred"
        )
    except Exception as e:
        logger.error(
            "Request failed - unexpected error",
            tracking_id=request.tracking_id,
            error_type=type(e).__name__,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected error"
        )
    finally:
        await service.close()


# --------------------------------------------------------------------
# 利用料モデル無しAPI
# --------------------------------------------------------------------
@router.post(
    "/data-exchange/non-fee-model/transaction/eligibility",
    summary="取引可否確認API(利用料モデル無)",
    description="取引可否確認API(データID、利用料モデルが無い場合に使用)",
    response_model=TransactionEligibilityNonFeeModelResponse,
    responses={
        status.HTTP_200_OK: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "リクエストエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"description": "データなし", "model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"description": "リソース競合エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def check_transaction_eligibility_non_fee_model(
    request: TransactionEligibilityNonFeeModelRequest,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("eligibility-non-fee-model:post")),
    db: Session = Depends(get_db),
) -> TransactionEligibilityNonFeeModelResponse:
    """取引可否を確認（利用料モデル無し）"""
    tracking_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(tracking_id)

    logger.info(
        "Received transaction eligibility check request (non fee model)",
        endpoint="/data-exchange/non-fee-model/transaction/eligibility",
        method="POST",
        provider_id=request.provider_id,
        consumer_id=request.consumer_id,
        payment_service_id=str(request.payment_service_id),
        amount=request.amount,
    )

    # consumer_idとoperator_idの一致検証
    verify_operator_id(request.consumer_id, credential, "Consumer ID")

    service = TransactionService(db)
    try:
        # 与信確認を含む取引可否確認を実行（利用料モデル無し）
        result = await service.check_transaction_eligibility_non_fee_model(
            request,
            tracking_id=tracking_id,
        )

        logger.info(
            "Transaction eligibility checked (non fee model)",
            status=result.status,
            status_code=200
        )

        return result

    except RecordNotFoundError as e:
        logger.warning(
            "Request failed - resource not found",
            error=str(e),
            status_code=404
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except DatabaseError as e:
        logger.error(
            "Request failed - database error",
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred"
        )
    except Exception as e:
        logger.error(
            "Request failed - unexpected error",
            error_type=type(e).__name__,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected error"
        )
    finally:
        # PaymentClientのリソースをクリーンアップ
        await service.close()


@router.post(
    "/data-exchange/non-fee-model/confirm",
    summary="データ交換取引金額確定用API(利用料モデル無)",
    description="データ交換取引金額確定用API(データID、利用料モデルが無い場合に使用)",
    response_model=DataExchangeStatusNonFeeModelResponse,
    responses={
        status.HTTP_200_OK: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse, "description": "リクエストエラー"},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse, "description": "認証エラー"},
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse, "description": "認可エラー"},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "データなし"},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse, "description": "リソース競合エラー"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse, "description": "サーバエラー"},
    }
)
async def confirm_data_exchange(
    request: DataExchangeNonFeeModelRequest,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("data-exchange-non-fee-model:post")),
    db: Session = Depends(get_db),
) -> DataExchangeStatusNonFeeModelResponse:
    """データ交換取引金額確定（利用料モデル無し）

    利用料モデルを使用せず、リクエストで直接指定された金額で
    トランザクションを作成し、外部決済サービスに取引登録を実行する。
    """
    request_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(request_id)

    logger.info(
        "Received data exchange confirm request (non fee model)",
        endpoint="/data-exchange/non-fee-model/confirm",
        method="POST",
        provider_id=request.provider_id,
        consumer_id=request.consumer_id,
        payment_service_id=str(request.payment_service_id),
        amount=request.amount,
    )

    # consumer_idとoperator_idの一致検証
    verify_operator_id(request.consumer_id, credential, "Consumer ID")

    service = TransactionService(db)
    try:
        # 取引登録を含むデータ交換取引金額確定を実行
        result = await service.confirm_data_exchange_non_fee_model(
            request,
            tracking_id_header=request_id,
        )

        logger.info(
            "Data exchange confirmed (non fee model)",
            result_status=result.status,
            status_code=200
        )

        return result

    except PaymentClientError as e:
        logger.error(
            "Request failed - external payment service error",
            error=str(e),
            status_code=502,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="External payment service error"
        )
    except DatabaseError as e:
        logger.error(
            "Request failed - database error",
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred"
        )
    except Exception as e:
        logger.error(
            "Request failed - unexpected error",
            error_type=type(e).__name__,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected error"
        )
    finally:
        # PaymentClientのリソースをクリーンアップ
        await service.close()


# --------------------------------------------------------------------
# 精算決済トランザクション取得API
# --------------------------------------------------------------------
@router.post(
    "/data-exchange/settlement/transactions",
    response_model=ConsumerTransactionListResponse,
    summary="決済状態取得API",
    description="指定期間・データ利用者ID・決済状態でトランザクション一覧を返却します。",
    responses={
        status.HTTP_200_OK: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "パラメータエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def get_consumer_transactions(
    request: ConsumerTransactionRequest,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("data-exchange-transactions:post")),
    db: Session = Depends(get_db),
) -> ConsumerTransactionListResponse:
    """利用者トランザクションを取得"""
    request_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(request_id)

    # アクセストークン内のoperator_idをprovider_idとして使用
    provider_id = credential.get("token_info", {}).get("operator_id")

    logger.info(
        "Received consumer transactions request",
        endpoint="/data-exchange/settlement/transactions",
        method="POST",
        provider_id=provider_id,
        consumer_id=request.consumer_id,
        start_date=str(request.start_date),
        end_date=str(request.end_date),
        settlement_status=request.settlement_status.value,
    )

    try:
        billing_service = PaymentBillingService(db)
        result = billing_service.get_consumer_transactions(request, provider_id)

        logger.info(
            "Consumer transactions retrieved",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            total_count=result.total_count,
            status_code=200,
        )

        return result

    except DatabaseError as e:
        logger.error(
            "Request failed - database error",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            error=str(e),
            status_code=500,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error occurred"
        )
    except Exception as e:
        logger.error(
            "Request failed - unexpected error",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            error_type=type(e).__name__,
            error=str(e),
            status_code=500,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected error"
        )