"""Base HTTP Client"""
import httpx
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class BaseClient:
    """外部API共通クライアント（非同期）"""

    def __init__(
        self,
        base_url: str,
        timeout: float = 10.0,
        retries: int = 1
    ):
        self.base_url = base_url
        self.timeout = httpx.Timeout(timeout)
        self.retries = retries
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """AsyncClientを取得（遅延初期化）"""
        if self._client is None or self._client.is_closed:
            transport = httpx.AsyncHTTPTransport(retries=self.retries)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=transport,
            )
        return self._client

    async def close(self) -> None:
        """クライアントをクローズ"""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        headers: Optional[dict] = None,
        json: Optional[dict] = None,
        **kwargs
    ) -> httpx.Response:
        """HTTPリクエストを実行"""
        client = await self._get_client()

        logger.debug(
            "External API request",
            extra={"method": method, "base_url": self.base_url, "endpoint": endpoint}
        )

        try:
            response = await client.request(
                method=method,
                url=endpoint,
                headers=headers,
                json=json,
                **kwargs
            )

            logger.debug(
                "External API response",
                extra={"status_code": response.status_code}
            )

            return response

        except httpx.RequestError as e:
            logger.error("External API error", extra={"error": str(e)}, exc_info=True)
            raise

    async def get(self, endpoint: str, **kwargs) -> httpx.Response:
        return await self._request("GET", endpoint, **kwargs)

    async def post(self, endpoint: str, **kwargs) -> httpx.Response:
        return await self._request("POST", endpoint, **kwargs)

    async def put(self, endpoint: str, **kwargs) -> httpx.Response:
        return await self._request("PUT", endpoint, **kwargs)

    async def delete(self, endpoint: str, **kwargs) -> httpx.Response:
        return await self._request("DELETE", endpoint, **kwargs)
