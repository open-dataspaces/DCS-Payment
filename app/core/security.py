"""Common Dependencies"""
import re
from uuid import UUID, uuid4
from typing import Literal
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings
from app.core.logging import get_logger
from app.clients import L3Client, L3ClientError
from app.clients import AuthzClient, AuthzClientError
UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)

logger = get_logger(__name__)

security = HTTPBearer()

# クライアントのインスタンス（シングルトン的に使用）
l3_client = L3Client()
authz_client = AuthzClient()


def validate_tracking_id(tracking_id: str | None) -> str:
    """
    X-TrackingId をUUID形式に厳密にバリデーションする。
    XPath / SQL / その他のインジェクション文字を含む値を全て拒否する。
    """
    if not tracking_id or not tracking_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-TrackingId is required and must not be empty"
        )

    # UUID形式のみ許可（これだけでXPath特殊文字 ' ( ) = を全て排除）
    if not UUID_PATTERN.match(tracking_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-TrackingId must be a valid UUID format"
        )

    return tracking_id

async def verify_access_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Bearerトークンを検証"""
    # 開発環境ではトークン検証をスキップ
    if settings.IS_DEVELOP:
        logger.info("Development mode: skipping token verification")
        return {
            "active": True,
            "user_id": "dev-user",
            "sub": "dev-user",
            "token_info": {
                "operator_id": "dev-operator-id"
            }
        }

    try:
        result = await l3_client.verify_token(credentials.credentials)

        if result.get("active"):
            return result

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    except HTTPException:
        raise
    except L3ClientError as e:
        logger.error("Token validation failed", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token validation failed",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        logger.error(
            "Token validation service error",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise


async def verify_request_headers(
    user_agent: str = Header(..., alias="User-Agent"),
    x_tracking_id: str = Header(..., alias="X-TrackingId"),
    accept_language: str = Header(default="ja-JP", alias="Accept-Language"),
    content_type: Literal["application/json"] = Header(..., alias="Content-Type"),
    x_payment_api_key: str = Header(..., alias="x-payment-api-key")
):
    
    # User-Agent の空文字チェック
    if not user_agent or not user_agent.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User-Agent is required and must not be empty"
        )

    # X-TrackingId を厳密にバリデーション
    validated_tracking_id = validate_tracking_id(x_tracking_id)

    """
    x_payment_api_keyヘッダーのAPIキーの検証をする
    """
    if x_payment_api_key != settings.X_PAYMENT_API_KEY:
        logger.warning(
            "Invalid API key provided",
            extra={"tracking_id": validated_tracking_id }
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key"
        )

    return {
        "user_agent": user_agent,
        "x_tracking_id": validated_tracking_id ,
        "accept_language": accept_language,
        "content_type": content_type,
        "x_payment_api_key": x_payment_api_key
    }


def verify_operator_id(request_id: str, credential: dict, id_type: str) -> None:
    """リクエストのconsumer_id/provider_idとトークン内のoperator_id（user_id）が一致するか検証

    Args:
        request_id: リクエストボディのconsumer_idまたはprovider_id
        credential: verify_access_tokenから取得したトークン情報
        id_type: IDの種類（"Consumer ID" or "Provider ID"）

    Raises:
        HTTPException: IDが一致しない場合、403 Forbiddenを返す
    """
    if settings.IS_DEVELOP:
        logger.debug("Development mode: skipping operator ID verification")
        return

    if not settings.OPERATOR_ID_VERIFICATION_ENABLED:
        logger.debug("Operator ID verification is disabled")
        return

    operator_id = credential.get("token_info", {}).get("operator_id")

    if request_id != operator_id:
        logger.warning(
            f"{id_type} mismatch with operator ID",
            request_id=request_id,
            operator_id=operator_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{id_type} does not match operator ID"
        )


def require_permission(endpoint_name: str, relation: str = "can_access"):
    """認可確認用の依存性を生成"""

    async def check_permission(
        credential: dict = Depends(verify_access_token),
        headers: dict = Depends(verify_request_headers),
    ):
        operator_id = credential.get("token_info", {}).get("operator_id")
        tracking_id = headers.get("x_tracking_id")

        try:
            await authz_client.check_or_raise(
                operator_id=operator_id,
                endpoint_name=endpoint_name,
                relation=relation,
                tracking_id=tracking_id
            )
        except AuthzClientError:
            logger.warning(
                "Authorization denied",
                extra={
                    "operator_id": operator_id,
                    "endpoint_name": endpoint_name,
                    "relation": relation,
                    "tracking_id": tracking_id,
                    "status_code": 403,
                }
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        except Exception as e:
            logger.error(
                "Authorization service error",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "operator_id": operator_id,
                    "endpoint_name": endpoint_name,
                    "tracking_id": tracking_id,
                },
                exc_info=True,
            )
            raise

        return credential

    return check_permission
