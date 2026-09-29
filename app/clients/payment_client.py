"""External Payment Service Client"""
import base64
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import uuid4

from app.clients.base_client import BaseClient
from app.core.config import settings
from app.core.logging import get_logger
from app.utils.money import tax_rate_to_percent, to_external_amount_str

logger = get_logger(__name__)


class PaymentClientError(Exception):
    """外部サービス接続エラー"""
    pass


class NpKakebaraiClientError(Exception):
    """NP掛け払いサービス接続エラー"""
    pass


NP_AUTHORI_OK = "00"

NP_AUTHORI_NG_REASONS = {
    "21": "未払いNG",
    "22": "金額超過NG",
    "24": "総合判断NG",
}


class TransactionItemResult:
    """取引登録結果（取引 item 単位）

    NP掛け払いは複数 item を1リクエストで登録でき、item ごとに npTransactionId と
    審査結果が返る。item は shopTransactionId（"{shop_transaction_id}_{index}"）で
    送信した取引と対応付ける。
    """

    def __init__(
        self,
        index: int,
        shop_transaction_id: str,
        data_id: Optional[str],
        external_transaction_id: Optional[str],
        authori_result: Optional[str],
        authori_ng_reason: Optional[str] = None,
    ):
        self.index = index
        self.shop_transaction_id = shop_transaction_id
        self.data_id = data_id
        self.external_transaction_id = external_transaction_id
        self.authori_result = authori_result
        self.authori_ng_reason = authori_ng_reason

    @property
    def is_success(self) -> bool:
        return self.authori_result == NP_AUTHORI_OK

    @property
    def ng_reason_text(self) -> str:
        return NP_AUTHORI_NG_REASONS.get(
            self.authori_ng_reason, f"Unknown({self.authori_ng_reason})"
        )


class TransactionRegistrationResult:
    """取引登録結果（リクエスト単位）

    is_success は全 item の審査が OK の場合のみ True。
    items に item 単位の結果を保持する。
    """

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
        items: Optional[list[TransactionItemResult]] = None,
    ):
        self.is_success = is_success
        self.buyer_id = buyer_id
        self.transaction_count = transaction_count
        self.detail = detail
        self.raw_response = raw_response
        self.items: list[TransactionItemResult] = items or []
        first = self.items[0] if self.items else None
        # 後方互換: 先頭 item の値
        self.authori_result = authori_result if authori_result is not None else (first.authori_result if first else None)
        self.authori_ng_reason = authori_ng_reason if authori_ng_reason is not None else (first.authori_ng_reason if first else None)
        self.external_transaction_id = external_transaction_id if external_transaction_id is not None else (first.external_transaction_id if first else None)

    @property
    def ok_items(self) -> list[TransactionItemResult]:
        return [it for it in self.items if it.is_success]

    @property
    def ng_items(self) -> list[TransactionItemResult]:
        return [it for it in self.items if not it.is_success]

    @property
    def registered_external_transaction_ids(self) -> list[str]:
        """NP側に登録された（審査OK/NGを問わず ID が採番された）取引ID"""
        return [it.external_transaction_id for it in self.items if it.external_transaction_id]

    def external_transaction_id_by_data_id(self) -> dict[Optional[str], Optional[str]]:
        return {it.data_id: it.external_transaction_id for it in self.items}


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

            # 審査結果を item ごとにパース
            # shopTransactionId で送信 item と対応付ける（順序に依存しない）
            result_data = response.get("result", {})
            buyer_data = result_data.get("buyer", {})
            transaction_infoes = buyer_data.get("transactionInfoes", [])

            sent_by_shop_txn_id = {
                info["shopTransactionId"]: (i, txn)
                for i, (info, txn) in enumerate(zip(transaction_infos, transactions))
            }
            items: list[TransactionItemResult] = []
            unmatched: list[str] = []
            for entry in transaction_infoes:
                shop_txn_id = entry.get("shopTransactionId")
                status_info = entry.get("status", {}) or {}
                authori_info = status_info.get("transactionAuthoriInfo", {}) or {}
                if shop_txn_id not in sent_by_shop_txn_id:
                    unmatched.append(shop_txn_id)
                    continue
                index, txn = sent_by_shop_txn_id[shop_txn_id]
                items.append(TransactionItemResult(
                    index=index,
                    shop_transaction_id=shop_txn_id,
                    data_id=txn.data_id,
                    external_transaction_id=entry.get("npTransactionId"),
                    authori_result=authori_info.get("transactionAuthoriResult"),
                    authori_ng_reason=authori_info.get("transactionAuthoriNgReason"),
                ))
            items.sort(key=lambda it: it.index)

            missing = [
                info["shopTransactionId"]
                for info in transaction_infos
                if info["shopTransactionId"] not in {it.shop_transaction_id for it in items}
            ]
            if unmatched or missing:
                logger.error(
                    "NP Kakebarai response items do not match request items",
                    extra={
                        "np_buyer_id": np_buyer_id,
                        "unmatched_in_response": unmatched,
                        "missing_in_response": missing,
                    }
                )
                # 対応付けできない場合は成功扱いにしない（採番済みIDは呼び出し側でキャンセル可能）
                return TransactionRegistrationResult(
                    is_success=False,
                    buyer_id=np_buyer_id,
                    transaction_count=len(transactions),
                    detail="Transaction registration error: response items do not match request",
                    raw_response=response,
                    items=items,
                )

            # 全 item の審査が OK の場合のみ成功
            ng_items = [it for it in items if not it.is_success]
            if ng_items:
                ng_details = ", ".join(
                    f"{it.data_id}: {it.ng_reason_text} "
                    f"(authori_result={it.authori_result}, ng_reason={it.authori_ng_reason})"
                    for it in ng_items
                )
                logger.warning(
                    "NP Kakebarai transaction authori NG",
                    extra={
                        "np_buyer_id": np_buyer_id,
                        "ng_count": len(ng_items),
                        "ok_count": len(items) - len(ng_items),
                        "ng_items": [
                            {"data_id": it.data_id, "authori_result": it.authori_result,
                             "authori_ng_reason": it.authori_ng_reason}
                            for it in ng_items
                        ],
                    }
                )
                return TransactionRegistrationResult(
                    is_success=False,
                    buyer_id=np_buyer_id,
                    transaction_count=len(transactions),
                    detail=f"Transaction authori NG: {ng_details}",
                    raw_response=response,
                    items=items,
                )

            logger.info(
                "NP Kakebarai transaction registration completed",
                extra={
                    "np_buyer_id": np_buyer_id,
                    "transaction_count": len(transactions),
                    "external_transaction_ids": [it.external_transaction_id for it in items],
                }
            )

            return TransactionRegistrationResult(
                is_success=True,
                buyer_id=np_buyer_id,
                transaction_count=len(transactions),
                detail="Transaction registration completed",
                raw_response=response,
                items=items,
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

        # 金額は円単位に丸め済みの値を受け取る（NP掛け払いAPIは整数円の文字列で受け取る）
        # 丸めは app.utils.money に集約し、DB保存額と同じ値を送る
        amount_str = to_external_amount_str(transaction.amount)
        tax_rate_percent = tax_rate_to_percent(transaction.tax_rate)

        info: dict[str, Any] = {
            "requestId": uuid4().hex[:8],
            "shopTransactionId": shop_txn_id,
            "shopOrderDate": transaction.order_date.strftime("%Y-%m-%d"),
            "totalAmount": amount_str,
            "transactionDetails": [
                {
                    "detail": transaction.detail,
                    "price": amount_str,
                    "quantity": "1",
                    "billedTaxKind": f"T{tax_rate_percent:02d}",
                }
            ],
            "taxRateSummaries": [
                {
                    "taxRate": str(tax_rate_percent),
                    "totalAmount": amount_str,
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


def _raise_if_np_errors(body: Any, api_name: str) -> None:
    """2xx でも本文に errors がある場合は受け付けられていないためエラーにする（CronJob と同じ判定）"""
    if isinstance(body, dict) and body.get("errors"):
        raise NpKakebaraiClientError(f"{api_name} returned errors: {body['errors']}")


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
            body = response.json()
            _raise_if_np_errors(body, "Transaction cancel API")
            return body
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
            bill_issue_base_date: 請求書発行基準日（YYYY-MM-DD）（任意、未指定時は JST の今日の日付）

        Returns:
            請求確定依頼結果

        Raises:
            NpKakebaraiClientError: API呼び出しエラー
        """
        headers = self._get_headers(tracking_id)
        if bill_issue_base_date is None:
            # サーバのタイムゾーン（コンテナは UTC）によらず JST の日付にする
            bill_issue_base_date = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
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
            body = response.json()
            _raise_if_np_errors(body, "Billing request API")
            return body
        except Exception as e:
            logger.error(
                "NP Kakebarai billing request API error",
                extra={"np_transaction_ids": np_transaction_ids, "error": str(e)}
            )
            raise NpKakebaraiClientError(f"Billing request API error: {e}") from e
