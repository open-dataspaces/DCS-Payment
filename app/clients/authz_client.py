"""Authorization Service Client - 認可確認クライアント"""
from typing import Optional

from app.clients.base_client import BaseClient
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class AuthzClientError(Exception):
    """認可クライアントエラー"""
    pass


class AuthzClient(BaseClient):
    """認可サービスクライアント"""

    def __init__(self):
        super().__init__(
            base_url=settings.AUTHZ_BASE_URL,
            timeout=5.0
        )
        self.store_id = settings.AUTHZ_OPENFGA_STORE_ID
        self.authorization_model_id = settings.AUTHZ_OPENFGA_MODEL_ID

    async def check(
        self,
        operator_id: str,
        endpoint_name: str,
        relation: str = "can_access",
        tracking_id: Optional[str] = None
    ) -> bool:
        """
        認可確認を実行

        Args:
            operator_id: オペレーターID
            endpoint_name: エンドポイント名（例: "fee-model:create"）
            relation: 関係（デフォルト: "can_access"）
            tracking_id: トラッキングID（ログ用）

        Returns:
            bool: 認可された場合True

        Raises:
            AuthzClientError: API呼び出し失敗時
        """
        # AUTHZ_ENABLEDがFalseの場合、認可チェックをスキップ
        if not settings.AUTHZ_ENABLED:
            logger.debug(
                "Authorization check skipped (AUTHZ_ENABLED=False)",
                extra={
                    "operator_id": operator_id,
                    "endpoint_name": endpoint_name,
                    "tracking_id": tracking_id
                }
            )
            return True

        logger.debug(
            "Authorization check started",
            extra={
                "operator_id": operator_id,
                "endpoint_name": endpoint_name,
                "relation": relation,
                "tracking_id": tracking_id
            }
        )

        # OpenFGAのobject形式は type:id で、idに : は使えないため - に変換
        safe_endpoint_name = endpoint_name.replace(":", "-")
        payload = {
            "authorization_model_id": self.authorization_model_id,
            "tuple_key": {
                "user": f"user:{operator_id}",
                "relation": relation,
                "object": f"api_endpoint:{safe_endpoint_name}"
            }
        }

        headers = {
            "Content-Type": "application/json",
        }
        if tracking_id:
            headers["X-TrackingId"] = tracking_id

        try:
            response = await self.post(
                endpoint=f"/stores/{self.store_id}/check",
                headers=headers,
                json=payload
            )

            if not response.is_success:
                logger.error(
                    "Authorization check failed",
                    extra={
                        "status_code": response.status_code,
                        "response": response.text
                    }
                )
                raise AuthzClientError(
                    f"Authorization check failed: {response.status_code}"
                )

            result = response.json()
            allowed = result.get("allowed", False)

            logger.info(
                "Authorization check completed",
                extra={
                    "operator_id": operator_id,
                    "endpoint_name": endpoint_name,
                    "allowed": allowed
                }
            )

            return allowed

        except AuthzClientError:
            raise
        except Exception as e:
            logger.error(
                "Authorization check error",
                extra={"error": str(e)},
                exc_info=True
            )
            raise AuthzClientError(f"Authorization check error: {e}")

    async def check_or_raise(
        self,
        operator_id: str,
        endpoint_name: str,
        relation: str = "can_access",
        tracking_id: Optional[str] = None
    ) -> None:
        """
        認可確認を実行し、拒否された場合は例外を投げる

        Raises:
            AuthzClientError: 認可拒否またはAPI呼び出し失敗時
        """
        if not await self.check(operator_id, endpoint_name, relation, tracking_id):
            raise AuthzClientError(
                f"Access denied: {operator_id} cannot {relation} {endpoint_name}"
            )
