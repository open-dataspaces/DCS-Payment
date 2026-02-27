import hashlib
from email.utils import formatdate
from time import mktime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.custom_exceptions import (
    RecordNotFoundError,
    DuplicateRecordError,
    OptimisticLockError,
    ResourceConflictError,
    DatabaseError,
)
from app.core.logging import get_logger, set_request_id
from app.core.security import verify_request_headers, verify_access_token
from app.core.security import require_permission
from app.db.session import get_db
from app.schemas.common import ErrorResponse
from app.schemas.fee_model_schema import (
    FeeModelCreateRequest,
    FeeModelUpdateRequest,
    FeeModelResponse,
    FeeModelListResponse,
)
from app.services.fee_model_service import FeeModelService

logger = get_logger(__name__)


router = APIRouter()

# ========================================
# 利用料モデル関連エンドポイント
# ========================================

@router.post(
    "/fee-model",
    response_model=FeeModelResponse,
    status_code=status.HTTP_201_CREATED,
    summary="利用料モデル登録API",
    description="利用料モデルを新規登録します。",
    responses={
        status.HTTP_201_CREATED: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "パラメータエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"description": "リソース競合エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def create_fee_model(
    request: FeeModelCreateRequest,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("fee-model:post")),
    db: Session = Depends(get_db),
) -> FeeModelResponse:
    """利用料モデルを作成"""
    request_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(request_id)

    logger.info(
        "Received create fee model request",
        endpoint="/api/v1/fee-model",
        method="POST",
        provider_id=request.provider_id,
        consumer_id=request.consumer_id,
        data_id=request.data_id,
        user_agent=headers.get('user_agent')
    )

    try:
        service = FeeModelService(db)
        result = service.create_fee_model(request)

        logger.info(
            "Fee model created successfully",
            fee_model_id=str(result.fee_model_id),
            status_code=201
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
    except (DuplicateRecordError, ResourceConflictError) as e:
        logger.warning(
            "Request failed - resource conflict",
            error=str(e),
            status_code=409
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
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


@router.get(
    "/fee-model",
    response_model=FeeModelListResponse,
    summary="利用料モデル一覧取得API",
    description="すべての利用料モデルを一覧取得します。",
    responses={
        status.HTTP_200_OK: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "パラメータエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def list_fee_models(
    response: Response,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("fee-model:get")),
    db: Session = Depends(get_db),
) -> FeeModelListResponse:
    """利用料モデル一覧を取得"""
    request_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(request_id)

    skip = 0
    limit = 10000
    provider_id = None
    consumer_id = None
    is_active = None

    logger.info(
        "Received list fee models request",
        endpoint="/api/v1/fee-model",
        method="GET",
        skip=skip,
        limit=limit,
        provider_id=provider_id,
        consumer_id=consumer_id,
        is_active=is_active
    )

    try:
        service = FeeModelService(db)
        result = service.list_fee_models(
            skip=skip,
            limit=limit,
            provider_id=provider_id,
            consumer_id=consumer_id,
            is_active=is_active
        )

        logger.info(
            "Fee models retrieved successfully",
            total=len(result.models),
            returned=len(result.models),
            status_code=200
        )

        # ETag/Last-Modified ヘッダを設定
        if result.models:
            # Last-Modified: 最新のupdated_atを使用
            latest_updated = max(m.updated_at for m in result.models)
            response.headers["Last-Modified"] = formatdate(
                timeval=mktime(latest_updated.timetuple()),
                localtime=False,
                usegmt=True
            )
            # ETag: fee_model_idリストとupdated_atのハッシュ
            etag_source = "|".join(
                f"{m.fee_model_id}:{m.updated_at.isoformat()}"
                for m in result.models
            )
            response.headers["ETag"] = f'"{hashlib.md5(etag_source.encode()).hexdigest()}"'

        return result

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


@router.put(
    "/fee-model/{fee_model_id}",
    response_model=FeeModelResponse,
    summary="利用料モデル変更API",
    description="指定した利用料モデルを更新します。",
    responses={
        status.HTTP_200_OK: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "パラメータエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"description": "該当データなし", "model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"description": "リソース競合エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def update_fee_model(
    fee_model_id: str,
    request: FeeModelUpdateRequest,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("fee-model:put")),
    db: Session = Depends(get_db),
) -> FeeModelResponse:
    """利用料モデルを更新"""
    request_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(request_id)

    logger.info(
        "Received update fee model request",
        endpoint=f"/api/v1/fee-model/{fee_model_id}",
        method="PUT",
        fee_model_id=fee_model_id
    )

    # UUID変換を最初に実行（早期リターンパターン）
    try:
        uuid_id = UUID(fee_model_id)
    except ValueError:
        logger.warning(
            "Invalid UUID format provided",
            fee_model_id=fee_model_id,
            status_code=400
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UUID format: {fee_model_id}"
        )

    # ビジネスロジックの実行
    try:
        service = FeeModelService(db)
        result = service.update_fee_model(uuid_id, request)

        logger.info(
            "Fee model updated successfully",
            fee_model_id=str(uuid_id),
            status_code=200
        )

        return result

    except RecordNotFoundError as e:
        logger.warning(
            "Request failed - resource not found",
            fee_model_id=str(uuid_id),
            error=str(e),
            status_code=404
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except (OptimisticLockError, ResourceConflictError) as e:
        logger.warning(
            "Request failed - resource conflict",
            fee_model_id=str(uuid_id),
            error=str(e),
            status_code=409
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except DatabaseError as e:
        logger.error(
            "Request failed - database error",
            fee_model_id=str(uuid_id),
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
            fee_model_id=str(uuid_id),
            error_type=type(e).__name__,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected error"
        )


@router.delete(
    "/fee-model/{fee_model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="利用料モデル削除API",
    description="指定した利用料モデルを削除します。",
    responses={
        status.HTTP_204_NO_CONTENT: {"description": "成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "パラメータエラー", "model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"description": "認証エラー", "model": ErrorResponse},
        status.HTTP_403_FORBIDDEN: {"description": "認可エラー", "model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"description": "該当データなし", "model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"description": "リソース競合エラー", "model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "サーバエラー", "model": ErrorResponse},
    }
)
async def delete_fee_model(
    fee_model_id: str,
    headers: dict = Depends(verify_request_headers),
    credential: dict = Depends(verify_access_token),
    _: None = Depends(require_permission("fee-model:delete")),
    db: Session = Depends(get_db),
):
    """利用料モデルを削除"""
    request_id = headers.get('x-trackingId') or str(uuid4())
    set_request_id(request_id)

    logger.info(
        "Received delete fee model request",
        endpoint=f"/api/v1/fee-model/{fee_model_id}",
        method="DELETE",
        fee_model_id=fee_model_id
    )

    # UUID変換を最初に実行（早期リターンパターン）
    try:
        uuid_id = UUID(fee_model_id)
    except ValueError:
        logger.warning(
            "Invalid UUID format provided",
            fee_model_id=fee_model_id,
            status_code=400
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UUID format: {fee_model_id}"
        )

    # ビジネスロジックの実行
    try:
        service = FeeModelService(db)
        service.delete_fee_model(uuid_id)

        logger.info(
            "Fee model deleted successfully",
            fee_model_id=str(uuid_id),
            status_code=204
        )

        return None

    except RecordNotFoundError as e:
        logger.warning(
            "Request failed - resource not found",
            fee_model_id=str(uuid_id),
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
            fee_model_id=str(uuid_id),
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
            fee_model_id=str(uuid_id),
            error_type=type(e).__name__,
            error=str(e),
            status_code=500,
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="unexpected error"
        )
