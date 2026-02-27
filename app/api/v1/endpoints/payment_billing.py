from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.custom_exceptions import (
    RecordNotFoundError,
    DatabaseError,
)
from app.core.logging import get_logger, set_request_id
from app.core.security import (
    verify_access_token,
    verify_request_headers,
    require_permission,
)
from app.db.session import get_db
from app.schemas.common import ErrorResponse
from app.schemas.fee_model_schema import (
    PaymentScheduleRequest,
    PaymentAmountListResponse,
    BillingScheduleRequest,
    BillingAmountListResponse,
)
from app.services.payment_billing_service import PaymentBillingService

logger = get_logger(__name__)

router = APIRouter()

# ========================================
# 支払い・請求関連エンドポイント
# ========================================

@router.post(
    "/payment",
    response_model=PaymentAmountListResponse,
    summary="支払予定額取得API",
    description="指定期間・データ提供者IDで支払（受領）予定額リストを返却します。",
    responses={
        status.HTTP_200_OK: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "パラメータエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def get_payment_schedule(
    request: PaymentScheduleRequest,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("payment:post")),
    db: Session = Depends(get_db),
) -> PaymentAmountListResponse:
    """支払予定額を取得"""
    request_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(request_id)

    # アクセストークン内のoperator_idをconsumer_idとして使用
    consumer_id = credential.get("token_info", {}).get("operator_id")

    logger.info(
        "Received payment schedule request",
        endpoint="/payment",
        method="POST",
        consumer_id=consumer_id,
        provider_id=request.provider_id,
        start_date=str(request.start_date),
        end_date=str(request.end_date)
    )

    try:
        service = PaymentBillingService(db)
        result = service.get_payment_schedule(request, consumer_id)

        logger.info(
            "Payment schedule retrieved",
            consumer_id=consumer_id,
            provider_id=request.provider_id,
            total_amount=result.total_amount,
            detail_count=len(result.payment_details),
            status_code=200
        )

        return result

    except DatabaseError as e:
        logger.error(
            "Request failed - database error",
            consumer_id=consumer_id,
            provider_id=request.provider_id,
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
            consumer_id=consumer_id,
            provider_id=request.provider_id,
            error_type=type(e).__name__,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected error"
        )


@router.post(
    "/billing",
    response_model=BillingAmountListResponse,
    summary="請求予定額取得API",
    description="指定期間・データ利用者IDで請求予定額リストを返却します。",
    responses={
        status.HTTP_200_OK: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "パラメータエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def get_billing_schedule(
    request: BillingScheduleRequest,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("billing:post")),
    db: Session = Depends(get_db),
) -> BillingAmountListResponse:
    """請求予定額を取得"""
    request_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(request_id)

    # アクセストークン内のoperator_idをprovider_idとして使用
    provider_id = credential.get("token_info", {}).get("operator_id")

    logger.info(
        "Received billing schedule request",
        endpoint="/billing",
        method="POST",
        provider_id=provider_id,
        consumer_id=request.consumer_id,
        start_date=str(request.start_date),
        end_date=str(request.end_date)
    )

    try:
        service = PaymentBillingService(db)
        result = service.get_billing_schedule(request, provider_id)

        logger.info(
            "Billing schedule retrieved",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            total_amount=result.total_amount,
            detail_count=len(result.billing_details),
            status_code=200
        )

        return result

    except DatabaseError as e:
        logger.error(
            "Request failed - database error",
            provider_id=provider_id,
            consumer_id=request.consumer_id,
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
            provider_id=provider_id,
            consumer_id=request.consumer_id,
            error_type=type(e).__name__,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected error"
        )
