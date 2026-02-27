from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from enum import Enum
from typing import List, Optional, Any, Dict
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID

# ========================================
# 定義
# ========================================

class TaxClassification(str, Enum):
    """税区分
    
    * `taxable` - 課税
    * `non_taxable` - 非課税
    """
    TAXABLE = "taxable"
    NON_TAXABLE = "non_taxable"

class StorageType(str, Enum):
    """保管先タイプ"""
    PROVIDER_ENV = "provider_env"
    SETTLEMENT_SERVICE = "settlement_service"

class DataExchangeStatus(str, Enum):
    """データ交換ステータス
    
    * `completed` - 交換完了
    * `failed` - 交換失敗
    """
    COMPLETED = "completed"
    FAILED = "failed"

class ProcessStatus(str, Enum):
    """処理成否状態
    
    * `success` - 成功
    * `error` - 失敗
    """
    SUCCESS = "success"
    FAILURE = "error"

class TransactionEligibility(str, Enum):
    """取引可否
    
    * `allowed` - 取引可
    * `denied` - 取引不可
    """
    ALLOWED = "allowed"
    DENIED = "denied"

# ========================================
# カスタムDecimal型の定義
# ========================================

def validate_decimal_places(value: Decimal, decimal_places: int) -> Decimal:
    """Decimalの小数点以下の桁数をバリデート"""
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"invalid format: {value}")
    
    # 小数点以下の桁数をチェック
    decimal_tuple = value.as_tuple()
    if decimal_tuple.exponent < -decimal_places:
        raise ValueError(
            f"Maximum {decimal_places} decimal places allowed (input: {value})"
        )
    
    return value


def round_decimal(value: Decimal, decimal_places: int) -> Decimal:
    """Decimalを指定した小数点以下の桁数に丸める"""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    
    quantize_value = Decimal('0.1') ** decimal_places
    return value.quantize(quantize_value, rounding=ROUND_HALF_UP)


# ========================================
# 基底スキーマ（用途別に分割）
# ========================================

class FeeModelCoreFields(BaseModel):
    """利用料モデルのコアフィールド（更新可能）"""

    fee_model_name: str = Field(..., description="利用料モデル名", min_length=1, max_length=255)
    price: Decimal = Field(..., description="金額", ge=Decimal("-9999999999999.99"), le=Decimal("9999999999999.99"))
    tax_classification: TaxClassification = Field(
        ...,
        description="税区分 (taxable: 課税, non_taxable: 非課税)"
    )
    tax_rate: Decimal = Field(..., description="税率", ge=0, le=1)

    @field_validator('price')
    @classmethod
    def validate_price(cls, v: Decimal) -> Decimal:
        """金額の小数点以下2桁バリデーション"""
        return validate_decimal_places(v, 2)
    
    @field_validator('tax_rate')
    @classmethod
    def validate_tax_rate(cls, v: Decimal) -> Decimal:
        """税率の小数点以下4桁バリデーション"""
        return validate_decimal_places(v, 4)


class FeeModelIdentifiers(BaseModel):
    """利用料モデルの識別子フィールド（更新不可）"""
    
    provider_id: str = Field(
        ...,
        description="データ提供者ID(外部システムにて発行)",
        min_length=1,
        max_length=255
    )
    consumer_id: str = Field(
        ...,
        description="データ利用者ID(外部システムにて発行)",
        min_length=1,
        max_length=255
    )
    data_id: str = Field(
        ...,
        description="データID(外部システムにて発行)",
        min_length=1,
        max_length=255
    )
    payment_service_id: UUID = Field(..., description="決済サービスID")


class FeeModelOptionalFields(BaseModel):
    """利用料モデルのオプションフィールド"""
    
    storage_type: Optional[StorageType] = Field(
        None, 
        description="保管先タイプ(将来拡張用)"
    )
    storage_key: Optional[str] = Field(
        None, 
        description="保管先識別子(将来拡張用)", 
        max_length=255
    )
    valid_to: Optional[datetime] = Field(None, description="有効終了日時(NULL=現在有効)")


class FeeModelClientManagedFields(BaseModel):
    """クライアントが管理可能なフィールド"""

    valid_from: datetime = Field(..., description="有効開始日時")
    is_active: bool = Field(True, description="現在有効フラグ", strict=True)
    version: int = Field(1, description="バージョン番号", ge=1)


class FeeModelSystemFields(BaseModel):
    """システム管理フィールド（サーバー側で自動管理）"""
    
    created_at: datetime = Field(..., description="登録日時")
    updated_at: datetime = Field(..., description="更新日時")


# ========================================
# リクエスト・レスポンススキーマ
# ========================================

class FeeModelCreateRequest(
    FeeModelCoreFields,
    FeeModelIdentifiers,
    FeeModelOptionalFields,
    FeeModelClientManagedFields
):
    """利用料モデル作成リクエスト
    
    クライアントから送信するフィールド：
    - コアフィールド（必須）
    - 識別子フィールド（必須）
    - オプションフィールド
    - クライアント管理フィールド（valid_from, is_active, version）
    
    サーバー側で自動生成されるフィールド：
    - created_at, updated_at（現在時刻）
    """
    
    # デフォルト値を設定
    is_active: bool = Field(True, description="現在有効フラグ", strict=True)
    version: int = Field(1, description="バージョン番号", ge=1)
    
    @field_validator('version')
    @classmethod
    def validate_version(cls, v: int) -> int:
        """バージョン番号のバリデーション"""
        if v < 1:
            raise ValueError('Version number must be 1 or greater')
        return v
    
    @model_validator(mode='after')
    def validate_active_state(self):
        """is_activeとvalid_toの整合性チェック"""
        if not self.is_active and self.valid_to is None:
            raise ValueError(
                'valid_to is required when is_active is False'
            )
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError(
                'valid_to must be after valid_from'
            )
        return self
    
    model_config = ConfigDict(
        extra="forbid",  # 未定義フィールドを即座に拒否
        json_schema_extra={
            "examples": [
                {
                    "fee_model_name": "スタンダードプラン",
                    "price": "5000.00",
                    "tax_classification": "taxable",
                    "tax_rate": "0.1000",
                    "provider_id": "provider_001",
                    "consumer_id": "consumer_001",
                    "data_id": "data_001",
                    "payment_service_id": "123e4567-e89b-12d3-a456-426614174000",
                    "storage_type": "provider_env",
                    "storage_key": "storage/standard/001",
                    "valid_from": "2024-01-01T00:00:00Z",
                    "valid_to": None,
                    "is_active": True,
                    "version": 1
                },
                {
                    "fee_model_name": "終了予定プラン",
                    "price": "3000.00",
                    "tax_classification": "taxable",
                    "tax_rate": "0.1000",
                    "provider_id": "provider_002",
                    "consumer_id": "consumer_002",
                    "data_id": "data_002",
                    "payment_service_id": "123e4567-e89b-12d3-a456-426614174000",
                    "valid_from": "2024-01-01T00:00:00Z",
                    "valid_to": "2024-12-31T23:59:59Z",
                    "is_active": False,
                    "version": 1
                }
            ]
        }
    )


class FeeModelUpdateRequest(BaseModel):
    """利用料モデル更新リクエスト
    
    更新可能なフィールドのみ含む（すべて任意 = 部分更新対応）
    
    更新不可能なフィールド：
    - provider_id, consumer_id, data_id, payment_service_id（識別子）
    - created_at, updated_at（システム管理）
    
    更新可能なフィールド：
    - fee_model_name, price, tax_classification, tax_rate（コア情報）
    - storage_type, storage_key, valid_from, valid_to（オプション情報）
    - is_active, version（クライアント管理フィールド）
    """
    
    # コアフィールド（すべて任意）
    fee_model_name: Optional[str] = Field(
        None,
        description="利用料モデル名",
        min_length=1,
        max_length=255
    )
    price: Optional[Decimal] = Field(None, description="金額", ge=Decimal("-9999999999999.99"), le=Decimal("9999999999999.99"))
    tax_classification: Optional[TaxClassification] = Field(
        None, 
        description="税区分 (taxable: 課税, non_taxable: 非課税)"
    )
    tax_rate: Optional[Decimal] = Field(None, description="税率", ge=0, le=1)
    
    # オプションフィールド（すべて任意）
    storage_type: Optional[StorageType] = Field(
        None, 
        description="保管先タイプ(将来拡張用)"
    )
    storage_key: Optional[str] = Field(
        None, 
        description="保管先識別子(将来拡張用)", 
        max_length=255
    )
    valid_from: Optional[datetime] = Field(None, description="有効開始日時")
    valid_to: Optional[datetime] = Field(None, description="有効終了日時(NULL=現在有効)")
    
    # クライアント管理フィールド（任意）
    is_active: Optional[bool] = Field(None, description="現在有効フラグ", strict=True)
    version: Optional[int] = Field(
        None, 
        description="バージョン番号（楽観的ロック用）", 
        ge=1
    )
    
    @field_validator('price')
    @classmethod
    def validate_price(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        """金額の小数点以下2桁バリデーション"""
        if v is None:
            return v
        return validate_decimal_places(v, 2)
    
    @field_validator('tax_rate')
    @classmethod
    def validate_tax_rate(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        """税率の小数点以下4桁バリデーション"""
        if v is None:
            return v
        return validate_decimal_places(v, 4)
    
    @field_validator('version')
    @classmethod
    def validate_version(cls, v: Optional[int]) -> Optional[int]:
        """バージョン番号のバリデーション"""
        if v is not None and v < 1:
            raise ValueError('Version number must be 1 or greater')
        return v

    model_config = ConfigDict(
        extra="forbid",  # 未定義フィールドを即座に拒否
    )

class FeeModelResponse(
    FeeModelCoreFields, 
    FeeModelIdentifiers, 
    FeeModelOptionalFields, 
    FeeModelClientManagedFields,
    FeeModelSystemFields
):
    """利用料モデルレスポンス
    
    すべてのフィールドを含む
    """
    
    fee_model_id: UUID = Field(..., description="利用料モデルID")
    
    model_config = ConfigDict(from_attributes=True)


class FeeModelListResponse(BaseModel):
    """利用料モデル一覧レスポンス"""
    
    models: List[FeeModelResponse] = Field(..., description="利用料モデル一覧")


# 支払い
class PaymentScheduleRequest(BaseModel):
    provider_id: str = Field(..., description="データ提供者ID", min_length=1, max_length=255)
    start_date: date = Field(..., description="開始年月日（YYYY-MM-DD）")
    end_date: date = Field(..., description="終了年月日（YYYY-MM-DD）")

    model_config = ConfigDict(
        extra="forbid",  # 未定義フィールドを即座に拒否
    )

# 請求
class BillingScheduleRequest(BaseModel):
    consumer_id: str = Field(..., description="データ利用者ID", min_length=1, max_length=255)
    start_date: date = Field(..., description="開始年月日（YYYY-MM-DD）")
    end_date: date = Field(..., description="終了年月日（YYYY-MM-DD）")

    model_config = ConfigDict(
        extra="forbid",  # 未定義フィールドを即座に拒否
    )

# 支払予定額
class PaymentAmountItem(BaseModel):
    tracking_id: str = Field(..., description="トラッキングID")
    fee_model_id: Optional[str] = Field(None, description="利用料モデルID")
    payment_service_id: Optional[str] = Field(None, description="決済サービスID")
    provider_id: str = Field(..., description="データ提供者ID")
    consumer_id: str = Field(..., description="データ利用者ID")
    data_id_list: List[str] = Field(..., description="データIDのリスト")
    completed_at: datetime = Field(..., description="データ交換処理完了日時")
    amount: float = Field(..., description="支払い予定額")
    tax_rate: float = Field(..., description="税率")

class PaymentAmountListResponse(BaseModel):
    payment_details: List[PaymentAmountItem] = Field(..., description="支払いリスト")
    total_amount: float = Field(..., description="支払い総額")


class BillingAmountItem(BaseModel):
    tracking_id: str = Field(..., description="トラッキングID")
    fee_model_id: Optional[str] = Field(None, description="利用料モデルID")
    payment_service_id: Optional[str] = Field(None, description="決済サービスID")
    provider_id: str = Field(..., description="データ提供者ID")
    consumer_id: str = Field(..., description="データ利用者ID")
    data_id_list: List[str] = Field(..., description="データIDのリスト")
    completed_at: datetime = Field(..., description="データ交換処理完了日時")
    amount: float = Field(..., description="請求予定額")
    tax_rate: float = Field(..., description="税率")

class BillingAmountListResponse(BaseModel):
    billing_details: List[BillingAmountItem] = Field(..., description="請求リスト")
    total_amount: float = Field(..., description="請求総額")


# データ交換処理状態登録
class DataExchangeRequest(BaseModel):

    tracking_id: str = Field(..., description="トラッキングID", min_length=1)
    provider_id: str = Field(..., description="データ提供者ID", min_length=1, max_length=255)
    consumer_id: str = Field(..., description="データ利用者ID", min_length=1, max_length=255)
    data_id_list: List[str] = Field(..., description="データIDのリスト(外部システムにて発行)", min_length=1)
    completed_at: datetime = Field(..., description="データ交換処理完了日時（ISO8601）")
    status: DataExchangeStatus = Field(..., description="データ交換ステータス (completed: 交換完了, failed: 交換失敗)")

    @field_validator('data_id_list')
    @classmethod
    def validate_data_id_list_elements(cls, v: List[str]) -> List[str]:
        """データIDリストの各要素が空文字でないことを検証"""
        for i, item in enumerate(v):
            if not item or not item.strip():
                raise ValueError(f'data_id_list[{i}] must not be empty')
        return v

    model_config = ConfigDict(
        extra="forbid",  # 未定義フィールドを即座に拒否
    )

class DataExchangeStatusResponse(BaseModel):
    status: ProcessStatus = Field(..., description="処理成否状態(success:成功、error:失敗)")
    detail: str = Field(..., description="処理成否詳細")

# データ交換処理状態更新
class DataExchangeStatusRequest(BaseModel):
    tracking_id: str = Field(..., description="トラッキングID", min_length=1)
    status: DataExchangeStatus = Field(..., description="データ交換ステータス (completed: 交換完了, failed: 交換失敗)")

    model_config = ConfigDict(
        extra="forbid",  # 未定義フィールドを即座に拒否
    )

class TransactionEligibilityRequest(BaseModel):
    provider_id: str = Field(..., description="データ提供者ID(外部システムにて発行)", min_length=1, max_length=255)
    consumer_id: str = Field(..., description="データ利用者ID(外部システムにて発行)", min_length=1, max_length=255)
    data_id_list: List[str] = Field(..., description="データIDのリスト", min_length=1)

    @field_validator('data_id_list')
    @classmethod
    def validate_data_id_list_elements(cls, v: List[str]) -> List[str]:
        """データIDリストの各要素が空文字でないことを検証"""
        for i, item in enumerate(v):
            if not item or not item.strip():
                raise ValueError(f'data_id_list[{i}] must not be empty')
        return v

    model_config = ConfigDict(
        extra="forbid",  # 未定義フィールドを即座に拒否
    )


class TransactionEligibilityResponse(BaseModel):
    status: TransactionEligibility = Field(..., description="取引可否(allowed:取引可, denied:取引不可)")
    detail: str = Field(..., description="処理成否詳細")

# --------------------------------------------------------------------
# 利用料モデルなし
# --------------------------------------------------------------------

class TransactionEligibilityNonFeeModelRequest(BaseModel):
    provider_id: str = Field(..., description="データ提供者ID(外部システムにて発行)", min_length=1, max_length=255)
    consumer_id: str = Field(..., description="データ利用者ID(外部システムにて発行)", min_length=1, max_length=255)
    payment_service_id: UUID = Field(..., description="決済サービスID")
    tax_classification: TaxClassification = Field(..., description="税区分 (taxable: 課税, non_taxable: 非課税)")
    tax_rate: Decimal = Field(..., description="税率", decimal_places=4, ge=0, le=1)
    completed_at: datetime = Field(..., description="データ交換処理完了日時（ISO8601）")
    amount: float = Field(..., description="支払い予定額")

    model_config = ConfigDict(
        extra="forbid",  # 未定義フィールドを即座に拒否
    )

class TransactionEligibilityNonFeeModelResponse(BaseModel):
    status: TransactionEligibility = Field(..., description="取引可否(allowed:取引可, denied:取引不可)")
    detail: str = Field(..., description="処理成否詳細")

class DataExchangeNonFeeModelRequest(BaseModel):
    provider_id: str = Field(..., description="データ提供者ID(外部システムにて発行)", min_length=1, max_length=255)
    consumer_id: str = Field(..., description="データ利用者ID(外部システムにて発行)", min_length=1, max_length=255)
    payment_service_id: UUID = Field(..., description="決済サービスID")
    tax_classification: TaxClassification = Field(..., description="税区分 (taxable: 課税, non_taxable: 非課税)")
    tax_rate: Decimal = Field(..., description="税率", decimal_places=4, ge=0, le=1)
    completed_at: datetime = Field(..., description="データ交換処理完了日時（ISO8601）")
    amount: float = Field(..., description="支払い予定額")

    model_config = ConfigDict(
        extra="forbid",  # 未定義フィールドを即座に拒否
    )

class DataExchangeStatusNonFeeModelResponse(BaseModel):
    status: ProcessStatus = Field(..., description="処理成否状態(success:成功、error:失敗)")
    detail: str = Field(..., description="処理成否詳細")


# --------------------------------------------------------------------
# 利用者トランザクション取得API
# --------------------------------------------------------------------

class SettlementStatus(str, Enum):
    """決済状態

    * `settled` - 確定済み
    * `unsettled` - 未確定
    * `cancelled` - キャンセル済み
    """
    SETTLED = "settled"
    UNSETTLED = "unsettled"
    CANCELLED = "cancelled"


class ConsumerTransactionRequest(BaseModel):
    consumer_id: str = Field(..., description="データ利用者ID", min_length=1, max_length=255)
    start_date: date = Field(..., description="開始年月日（YYYY-MM-DD）")
    end_date: date = Field(..., description="終了年月日（YYYY-MM-DD）")
    settlement_status: SettlementStatus = Field(..., description="決済状態フィルタ (settled/unsettled/cancelled)")

    model_config = ConfigDict(
        extra="forbid",
    )


class ConsumerTransactionItem(BaseModel):
    transaction_id: str = Field(..., description="取引ID")
    tracking_id: Optional[str] = Field(None, description="トラッキングID")
    external_transaction_id: Optional[str] = Field(None, description="外部取引ID")
    provider_id: str = Field(..., description="データ提供者ID")
    consumer_id: str = Field(..., description="データ利用者ID")
    data_id: Optional[str] = Field(None, description="データID")
    snapshot_price: Decimal = Field(..., description="スナップショット金額")
    snapshot_tax_rate: Decimal = Field(..., description="スナップショット税率")
    snapshot_tax_classification: str = Field(..., description="スナップショット税区分")
    calculated_amount: Decimal = Field(..., description="計算済金額（税込）")
    consumer_exchange_status: str = Field(..., description="利用者データ交換ステータス")
    provider_exchange_status: str = Field(..., description="提供者データ交換ステータス")
    l2_http_status: str = Field(..., description="L2 HTTPステータス")
    order_details: Optional[str] = Field(None, description="注文内容")
    request_date: Optional[datetime] = Field(None, description="請求日")
    payment_deadline: Optional[datetime] = Field(None, description="支払期限")
    paid_at: Optional[datetime] = Field(None, description="支払完了日時")
    created_at: datetime = Field(..., description="登録日時")
    updated_at: datetime = Field(..., description="更新日時")


class ConsumerTransactionListResponse(BaseModel):
    transactions: List[ConsumerTransactionItem] = Field(..., description="トランザクション一覧")
    total_count: int = Field(..., description="取得件数")
