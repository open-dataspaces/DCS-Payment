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
    """
    認可サービスクライアント

    L3 の認可判定API（AuthZEN Access Evaluation API）を呼び出す。
    L3 は認可ストア（OpenFGA）に判定を転送し、判定結果をレスポンスの data に格納して返す。
    """

    def __init__(self):
        super().__init__(
            base_url=settings.L3_BASE_URL,
            timeout=5.0
        )
        self.api_key = settings.L3_API_KEY
        self.evaluation_endpoint = settings.L3_AUTHZ_EVALUATION_ENDPOINT.format(store_id=settings.AUTHZ_STORE_ID)

    async def check(
        self,
        operator_id: str,
        endpoint_name: str,
        access_token: str,
        relation: str = "can_access",
        tracking_id: Optional[str] = None
    ) -> bool:
        """
        認可確認を実行

        Args:
            operator_id: オペレーターID
            endpoint_name: エンドポイント名（例: "fee-model:create"）
            access_token: APIの呼び出し元のアクセストークン（L3 の認可判定APIの認証に使用）
            relation: 関係（デフォルト: "can_access"）
            tracking_id: トラッキングID

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
        # AuthZEN の subject / resource / action は、OpenFGA の user / object / relation に対応する
        # （user:{operator_id} が api_endpoint:{エンドポイント名} に対して relation を持つかを判定）
        payload = {
            "subject": {"type": "user", "id": operator_id},
            "resource": {"type": "api_endpoint", "id": safe_endpoint_name},
            "action": {"name": relation}
        }

        headers = {
            "Content-Type": "application/json",
            "Accept-Language": "ja-JP",
            "API-Key": self.api_key,
            "Authorization": f"Bearer {access_token}",
        }
        if tracking_id:
            headers["X-TrackingID"] = tracking_id

        try:
            response = await self.post(
                endpoint=self.evaluation_endpoint,
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

            # L3 のレスポンスは data に認可ストアの判定結果（{"decision": true/false}）を格納している
            allowed = (response.json().get("data") or {}).get("decision")
            if not isinstance(allowed, bool):
                logger.error(
                    "Authorization check failed: invalid response",
                    extra={"response": response.text}
                )
                raise AuthzClientError("Authorization check failed: invalid response")

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
        access_token: str,
        relation: str = "can_access",
        tracking_id: Optional[str] = None
    ) -> None:
        """
        認可確認を実行し、拒否された場合は例外を投げる

        Raises:
            AuthzClientError: 認可拒否またはAPI呼び出し失敗時
        """
        if not await self.check(operator_id, endpoint_name, access_token, relation, tracking_id):
            raise AuthzClientError(
                f"Access denied: {operator_id} cannot {relation} {endpoint_name}"
            )
