"""L3 Authentication Service Client"""
from typing import Optional

from app.clients.base_client import BaseClient
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class L3ClientError(Exception):
    """L3クライアントエラー"""
    pass


class L3Client(BaseClient):
    """L3認証サービスクライアント"""

    def __init__(self):
        super().__init__(
            base_url=settings.L3_BASE_URL,
            timeout=10.0
        )
        self.introspect_endpoint = settings.L3_INTROSPECT_ENDPOINT
        self.api_key = settings.L3_API_KEY
        self.client_id = settings.L3_CLIENT_ID
        self.client_secret = settings.L3_CLIENT_SECRET

    def _get_headers(self, tracking_id: Optional[str] = None) -> dict:
        """共通ヘッダーを取得"""
        headers = {
            "Content-Type": "application/json",
            "Accept-Language": "ja-JP",
            "API-Key": self.api_key,
        }
        if tracking_id:
            headers["X-TrackingID"] = tracking_id
        return headers

    async def verify_token(
        self,
        access_token: str,
        tracking_id: Optional[str] = None
    ) -> dict:
        """
        アクセストークンを検証

        Args:
            access_token: 検証するトークン
            tracking_id: トラッキングID

        Returns:
            dict: トークン情報 {"active": bool, "user_id": str, ...}

        Raises:
            L3ClientError: API呼び出し失敗時
        """
        logger.debug("Verifying access token with L3")

        response = await self.post(
            endpoint=self.introspect_endpoint,
            headers=self._get_headers(tracking_id),
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "access_token": access_token,
            }
        )

        if not response.is_success:
            logger.error(
                "L3 token verification failed",
                extra={"status_code": response.status_code}
            )
            raise L3ClientError(f"Token verification failed: {response.status_code}")

        return response.json().get("data", {})
