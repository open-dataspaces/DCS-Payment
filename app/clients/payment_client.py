"""External Payment Service Client"""
import base64
from datetime import date
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import uuid4

from app.clients.base_client import BaseClient
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class PaymentClientError(Exception):
    """外部サービス接続エラー"""
    pass


class NpKakebaraiClientError(Exception):
    """NP掛け払いサービス接続エラー"""
    pass


class TransactionRegistrationResult:
    """取引登録結果"""

    def __init__(
        self,
        is_success: bool,
        buyer_id: str,
        transaction_count: int = 0,
        detail: Optional[str] = None,
        raw_response: Optional[dict[str, Any]] = None,
        authori_result: Optional[str] = None,
        authori_ng_reason: Optional[str] = None,
        external_transaction_id: Optional[str] = None,
    ):
        self.is_success = is_success
        self.buyer_id = buyer_id
        self.transaction_count = transaction_count
        self.detail = detail
        self.raw_response = raw_response
        self.authori_result = authori_result
        self.authori_ng_reason = authori_ng_reason
        self.external_transaction_id = external_transaction_id


class TransactionInfo:
    """取引情報（決済サービスに登録する取引データ）"""

    def __init__(
        self,
        data_id: str,
        amount: Decimal,
        tax_rate: Decimal,
        order_date: date,
        detail: Optional[str] = None,
    ):
        self.data_id = data_id
        self.amount = amount
        self.tax_rate = tax_rate
        self.order_date = order_date
        self.detail = detail or f"Data exchange: {data_id}"


class PaymentClient:
    """外部決済サービスクライアント（ファサード）

    複数の決済サービスクライアントを統合し、統一的なインターフェースを提供する。
    現在はNP掛け払いクライアントをサポート。
    """

    def __init__(self):
        self._np_kakebarai_client: Optional[NpKakebaraiClient] = None

    @property
    def np_kakebarai(self) -> "NpKakebaraiClient":
        """NP掛け払いクライアントを取得（遅延初期化）"""
        if self._np_kakebarai_client is None:
            self._np_kakebarai_client = NpKakebaraiClient()
        return self._np_kakebarai_client

    async def close(self) -> None:
        """全クライアントをクローズ"""
        if self._np_kakebarai_client is not None:
            await self._np_kakebarai_client.close()
            self._np_kakebarai_client = None

    def is_np_kakebarai(self, payment_service_id: str) -> bool:
        """NP掛け払いサービスかどうかを判定

        Args:
            payment_service_id: 決済サービスID（UUID文字列）

        Returns:
            NP掛け払いサービスの場合True
        """
        if not settings.NP_KAKEBARAI_SERVICE_ID:
            return False
        return str(payment_service_id) == settings.NP_KAKEBARAI_SERVICE_ID

    def supports_transaction_registration(self, payment_service_id: str) -> bool:
        """取引登録をサポートしているサービスかどうかを判定

        Args:
            payment_service_id: 決済サービスID（UUID文字列）

        Returns:
            取引登録をサポートしている場合True
        """
        # 現在はNP掛け払いのみサポート
        return self.is_np_kakebarai(payment_service_id)

    async def register_transaction(
        self,
        external_buyer_id: str,
        payment_service_id: str,
        shop_transaction_id: str,
        transactions: list[TransactionInfo],
        tracking_id: Optional[str] = None,
        delivery_info: Optional[dict[str, str]] = None,
    ) -> TransactionRegistrationResult:
        """取引登録を実行

        external_buyer_id（外部購入企業ID）を使って外部決済サービスに取引を登録する。
        決済サービスIDに基づいて適切なAPIを呼び出す。

        Args:
            external_buyer_id: 外部購入企業ID（ローカルDBから取得）
            payment_service_id: 決済サービスID（UUID文字列）
            shop_transaction_id: 加盟店取引ID（tracking_idなど）
            transactions: 取引情報リスト
            tracking_id: トラッキングID（任意）
            delivery_info: 配送先情報（任意）

        Returns:
            TransactionRegistrationResult: 取引登録結果

        Raises:
            PaymentClientError: API呼び出しエラーまたは未サポートのサービス
        """
        # NP掛け払いの場合
        if self.is_np_kakebarai(payment_service_id):
            return await self._register_transaction_np_kakebarai(
                np_buyer_id=external_buyer_id,
                shop_transaction_id=shop_transaction_id,
                transactions=transactions,
                tracking_id=tracking_id,
                delivery_info=delivery_info,
            )

        # 未サポートの決済サービス
        logger.warning(
            "Transaction registration not supported for payment service",
            extra={
                "external_buyer_id": external_buyer_id,
                "payment_service_id": payment_service_id,
            }
        )
        raise PaymentClientError(
            f"Transaction registration not supported for payment service: {payment_service_id}"
        )

    async def _register_transaction_np_kakebarai(
        self,
        np_buyer_id: str,
        shop_transaction_id: str,
        transactions: list[TransactionInfo],
        tracking_id: Optional[str] = None,
        delivery_info: Optional[dict[str, str]] = None,
    ) -> TransactionRegistrationResult:
        """NP掛け払いの取引登録を実行

        Args:
            np_buyer_id: NP購入企業ID
            shop_transaction_id: 加盟店取引ID
            transactions: 取引情報リスト
            tracking_id: トラッキングID
            delivery_info: 配送先情報（任意）

        Returns:
            TransactionRegistrationResult: 取引登録結果
        """
        try:
            # 取引情報をNP掛け払いAPI用に変換
            transaction_infos = [
                self._build_np_transaction_info(
                    shop_transaction_id=shop_transaction_id,
                    transaction=txn,
                    index=i,
                    delivery_info=delivery_info,
                )
                for i, txn in enumerate(transactions)
            ]

            # NP掛け払いの取引登録APIを直接呼び出し
            response = await self.np_kakebarai.register_transactions(
                np_buyer_id=np_buyer_id,
                transaction_infos=transaction_infos,
                tracking_id=tracking_id
            )

            # エラーチェック
            if response.get("errors"):
                error_codes = []
                error_messages = []
                for e in response["errors"]:
                    error_codes.extend(e.get("codes", []))
                    if e.get("message"):
                        error_messages.append(e["message"])
                error_codes_str = ", ".join(error_codes) if error_codes else ""
                error_messages_str = ", ".join(error_messages) if error_messages else str(response["errors"])
                logger.error(
                    "NP Kakebarai transaction registration error",
                    extra={
                        "np_buyer_id": np_buyer_id,
                        "error_codes": error_codes,
                        "errors": error_messages,
                    }
                )
                # NPエラーコードをdetailに含める
                detail_parts = ["Transaction registration error"]
                if error_codes_str:
                    detail_parts.append(f"error_codes=[{error_codes_str}]")
                if error_messages_str:
                    detail_parts.append(error_messages_str)
                return TransactionRegistrationResult(
                    is_success=False,
                    buyer_id=np_buyer_id,
                    transaction_count=0,
                    detail=": ".join(detail_parts),
                    raw_response=response,
                )

            # 審査結果をパース
            authori_result = None
            authori_ng_reason = None
            external_transaction_id = None

            result_data = response.get("result", {})
            buyer_data = result_data.get("buyer", {})
            transaction_infoes = buyer_data.get("transactionInfoes", [])

            if transaction_infoes:
                first_txn = transaction_infoes[0]
                external_transaction_id = first_txn.get("npTransactionId")
                status_info = first_txn.get("status", {})
                authori_info = status_info.get("transactionAuthoriInfo", {})
                authori_result = authori_info.get("transactionAuthoriResult")
                authori_ng_reason = authori_info.get("transactionAuthoriNgReason")

            # 審査結果に基づいて成否を判定
            is_success = (authori_result == "00")

            if not is_success:
                ng_reason_map = {
                    "21": "未払いNG",
                    "22": "金額超過NG",
                    "24": "総合判断NG",
                }
                ng_reason_text = ng_reason_map.get(
                    authori_ng_reason, f"Unknown({authori_ng_reason})"
                )
                # NPエラーコードをdetailに含める
                np_error_code = f"authori_result={authori_result}, ng_reason={authori_ng_reason}"
                logger.warning(
                    "NP Kakebarai transaction authori NG",
                    extra={
                        "np_buyer_id": np_buyer_id,
                        "authori_result": authori_result,
                        "authori_ng_reason": authori_ng_reason,
                        "ng_reason_text": ng_reason_text,
                    }
                )
                return TransactionRegistrationResult(
                    is_success=False,
                    buyer_id=np_buyer_id,
                    transaction_count=len(transactions),
                    detail=f"Transaction authori NG: {ng_reason_text} ({np_error_code})",
                    raw_response=response,
                    authori_result=authori_result,
                    authori_ng_reason=authori_ng_reason,
                    external_transaction_id=external_transaction_id,
                )

            logger.info(
                "NP Kakebarai transaction registration completed",
                extra={
                    "np_buyer_id": np_buyer_id,
                    "transaction_count": len(transactions),
                    "authori_result": authori_result,
                    "external_transaction_id": external_transaction_id,
                }
            )

            return TransactionRegistrationResult(
                is_success=True,
                buyer_id=np_buyer_id,
                transaction_count=len(transactions),
                detail="Transaction registration completed",
                raw_response=response,
                authori_result=authori_result,
                authori_ng_reason=authori_ng_reason,
                external_transaction_id=external_transaction_id,
            )

        except NpKakebaraiClientError as e:
            logger.error(
                "NP Kakebarai transaction registration API error",
                extra={"np_buyer_id": np_buyer_id, "error": str(e)}
            )
            raise PaymentClientError(f"Transaction registration failed: {e}") from e
        except Exception as e:
            logger.error(
                "Unexpected error during NP Kakebarai transaction registration",
                extra={"np_buyer_id": np_buyer_id, "error": str(e)}
            )
            raise PaymentClientError(f"Transaction registration failed: {e}") from e

    def _build_np_transaction_info(
        self,
        shop_transaction_id: str,
        transaction: TransactionInfo,
        index: int = 0,
        delivery_info: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """NP掛け払い取引登録用の取引情報を構築

        Args:
            shop_transaction_id: 加盟店取引ID（tracking_idなど）
            transaction: 取引情報
            index: 取引のインデックス（複数取引時の識別用）
            delivery_info: 配送先情報（任意）

        Returns:
            NP掛け払い取引登録用の取引情報
        """
        # shopTransactionId は最大40文字: shop_transaction_id + "_" + index
        shop_txn_id = f"{shop_transaction_id}_{index}"[:40]

        # 金額を整数に変換（NP掛け払いAPIは文字列で受け取る）
        amount_int = int(transaction.amount)
        tax_rate_percent = int(transaction.tax_rate * 100)

        info: dict[str, Any] = {
            "requestId": uuid4().hex[:8],
            "shopTransactionId": shop_txn_id,
            "shopOrderDate": transaction.order_date.strftime("%Y-%m-%d"),
            "totalAmount": str(amount_int),
            "transactionDetails": [
                {
                    "detail": transaction.detail,
                    "price": str(amount_int),
                    "quantity": "1",
                    "billedTaxKind": f"T{tax_rate_percent:02d}",
                }
            ],
            "taxRateSummaries": [
                {
                    "taxRate": str(tax_rate_percent),
                    "totalAmount": str(amount_int),
                }
            ],
        }
        if delivery_info:
            info["deliveryInfo"] = delivery_info
        return info

    async def cancel_transaction(
        self,
        payment_service_id: str,
        np_transaction_ids: list[str],
    ) -> dict[str, Any]:
        """取引キャンセル"""
        if self.is_np_kakebarai(payment_service_id):
            return await self.np_kakebarai.cancel_transactions(np_transaction_ids)
        raise PaymentClientError(f"Cancel not supported for: {payment_service_id}")

    async def request_billing(
        self,
        payment_service_id: str,
        np_transaction_ids: list[str],
        bill_issue_base_date: Optional[str] = None,
    ) -> dict[str, Any]:
        """請求確定依頼"""
        if self.is_np_kakebarai(payment_service_id):
            try:
                return await self.np_kakebarai.request_billing(
                    np_transaction_ids, bill_issue_base_date=bill_issue_base_date
                )
            except NpKakebaraiClientError as e:
                raise PaymentClientError(f"Billing request failed: {e}") from e
            except Exception as e:
                raise PaymentClientError(f"Billing request failed: {e}") from e
        raise PaymentClientError(f"Billing request not supported for: {payment_service_id}")


class NpKakebaraiClient(BaseClient):
    """NP掛け払いサービスクライアント

    NP掛け払いAPIへの接続を行うクライアント。
    Basic認証を使用してAPIアクセスを行う。

    使用可能なAPI（NP掛け払いAPI.md記載）:
    1. POST /v1/transactions — 取引登録
    2. POST /v1/transactions/cancel — 取引キャンセル
    3. POST /v1/bills/request — 請求確定依頼
    """

    def __init__(self):
        super().__init__(
            base_url=settings.NP_KAKEBARAI_BASE_URL,
            timeout=30.0
        )
        self.shop_code = settings.NP_KAKEBARAI_SHOP_CODE
        self.sp_code = settings.NP_KAKEBARAI_SP_CODE
        self.terminal_id = settings.NP_KAKEBARAI_TERMINAL_ID

    def _encode_basic_auth(self) -> str:
        """Basic認証用のエンコード文字列を生成"""
        credentials = f"{self.shop_code}:{self.sp_code}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    def _get_headers(self, tracking_id: Optional[str] = None) -> dict:
        """共通ヘッダーを取得"""
        host = urlparse(settings.NP_KAKEBARAI_BASE_URL).hostname or ""
        headers = {
            "Host": host,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": self._encode_basic_auth(),
            "X-NP-Terminal-Id": self.terminal_id,
            "X-HTTP-Method-Override": "POST",
        }
        if tracking_id:
            headers["X-TrackingID"] = tracking_id
        return headers

    async def register_transactions(
        self,
        np_buyer_id: str,
        transaction_infos: list[dict[str, Any]],
        tracking_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """取引登録API (POST /v1/transactions)

        取引情報を登録する。

        Args:
            np_buyer_id: NP購入企業ID（11桁）
            transaction_infos: 取引情報リスト
            tracking_id: トラッキングID（任意）

        Returns:
            取引登録結果

        Raises:
            NpKakebaraiClientError: API呼び出しエラー
        """
        headers = self._get_headers(tracking_id)
        payload = {
            "request": {
                "npBuyerId": np_buyer_id,
                "transactionInfoes": transaction_infos
            }
        }

        try:
            response = await self.post(
                "/v1/transactions",
                headers=headers,
                json=payload
            )
            if response.status_code >= 400:
                # NPエラーコードを抽出
                error_codes = []
                try:
                    error_data = response.json()
                    for error in error_data.get("errors", []):
                        error_codes.extend(error.get("codes", []))
                except Exception:
                    pass
                error_codes_str = ", ".join(error_codes) if error_codes else "unknown"
                logger.error(
                    "NP Kakebarai transaction registration API error response",
                    extra={
                        "np_buyer_id": np_buyer_id,
                        "status_code": response.status_code,
                        "error_codes": error_codes,
                        "response_body": response.text,
                        "request_payload": payload,
                    }
                )
                raise NpKakebaraiClientError(
                    f"Transaction registration API error "
                    f"(status={response.status_code}, error_codes=[{error_codes_str}])"
                )
            return response.json()
        except NpKakebaraiClientError:
            raise
        except Exception as e:
            # HTTPStatusErrorの場合、レスポンスからNPエラーコードを抽出
            error_codes = []
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_data = e.response.json()
                    for error in error_data.get("errors", []):
                        error_codes.extend(error.get("codes", []))
                except Exception:
                    pass
            error_codes_str = ", ".join(error_codes) if error_codes else ""
            logger.error(
                "NP Kakebarai transaction registration API error",
                extra={
                    "np_buyer_id": np_buyer_id,
                    "error": str(e),
                    "error_codes": error_codes,
                }
            )
            if error_codes_str:
                raise NpKakebaraiClientError(
                    f"Transaction registration API error "
                    f"(error_codes=[{error_codes_str}]): {e}"
                ) from e
            raise NpKakebaraiClientError(f"Transaction registration API error: {e}") from e

    async def cancel_transactions(
        self,
        np_transaction_ids: list[str],
        tracking_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """取引キャンセルAPI (POST /v1/transactions/cancel)

        NP取引IDを指定して取引をキャンセルする。

        Args:
            np_transaction_ids: NP取引IDリスト
            tracking_id: トラッキングID（任意）

        Returns:
            取引キャンセル結果

        Raises:
            NpKakebaraiClientError: API呼び出しエラー
        """
        headers = self._get_headers(tracking_id)
        payload = {
            "requests": np_transaction_ids
        }

        try:
            response = await self.post(
                "/v1/transactions/cancel",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(
                "NP Kakebarai transaction cancel API error",
                extra={"np_transaction_ids": np_transaction_ids, "error": str(e)}
            )
            raise NpKakebaraiClientError(f"Transaction cancel API error: {e}") from e

    async def request_billing(
        self,
        np_transaction_ids: list[str],
        tracking_id: Optional[str] = None,
        bill_issue_base_date: Optional[str] = None,
    ) -> dict[str, Any]:
        """請求確定依頼API (POST /v1/bills/request)

        NP取引IDを指定して請求確定を依頼する。

        Args:
            np_transaction_ids: NP取引IDリスト
            tracking_id: トラッキングID（任意）
            bill_issue_base_date: 請求書発行基準日（YYYY-MM-DD）（任意、未指定時は今日の日付）

        Returns:
            請求確定依頼結果

        Raises:
            NpKakebaraiClientError: API呼び出しエラー
        """
        headers = self._get_headers(tracking_id)
        if bill_issue_base_date is None:
            bill_issue_base_date = date.today().strftime("%Y-%m-%d")
        payload = {
            "requests": [
                {
                    "npTransactionId": np_txn_id,
                    "billIssueBaseDate": bill_issue_base_date,
                }
                for np_txn_id in np_transaction_ids
            ]
        }

        try:
            response = await self.post(
                "/v1/bills/request",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(
                "NP Kakebarai billing request API error",
                extra={"np_transaction_ids": np_transaction_ids, "error": str(e)}
            )
            raise NpKakebaraiClientError(f"Billing request API error: {e}") from e
