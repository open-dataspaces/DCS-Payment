"""
SQLAlchemy Models for Fee Management System

"""

from uuid import uuid4

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base

class PaymentService(Base):
    """決済サービス
    
    決済を処理する外部サービスの情報を管理する
    """
    
    __tablename__ = "payment_services"

    payment_service_id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="決済サービスID"
    )
    payment_service_name = Column(
        String(255),
        nullable=False,
        comment="決済サービス名"
    )
    payment_service_url = Column(
        String(512),
        nullable=False,
        comment="決済サービスURL"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="登録日時"
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新日時"
    )

    # Relationships
    fee_models = relationship(
        "FeeModel",
        back_populates="payment_service",
        cascade="all, delete-orphan"
    )
    user_registrations = relationship(
        "PaymentServiceUserRegistration",
        back_populates="payment_service",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<PaymentService(id={self.payment_service_id}, name='{self.payment_service_name}')>"


class FeeModel(Base):
    """利用料モデル
    
    データ提供者と利用者間の料金設定を管理する
    同一の(provider_id, consumer_id, data_id)の組み合わせで、
    is_active=Trueのレコードは1つのみ存在できる
    """
    
    __tablename__ = "fee_models"

    fee_model_id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="利用料モデルID"
    )
    fee_model_name = Column(
        String(255),
        nullable=False,
        comment="利用料モデル名"
    )
    price = Column(
        Numeric(15, 2),  # 最大9,999,999,999,999.99
        nullable=False,
        comment="金額"
    )
    tax_classification = Column(
        String(50),
        nullable=False,
        comment="税区分(課税/非課税)"
    )
    tax_rate = Column(
        Numeric(5, 4),  # 最大9.9999 (999.99%)
        nullable=False,
        comment="税率"
    )
    provider_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="データ提供者ID(外部システム)"
    )
    consumer_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="データ利用者ID(外部システム)"
    )
    data_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="データID(外部システム)"
    )
    payment_service_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("payment_services.payment_service_id", ondelete="RESTRICT"),
        nullable=False,
        comment="決済サービスID"
    )
    storage_type = Column(
        String(100),
        nullable=False,
        comment="保管先タイプ(provider_env/settlement_service)"
    )
    storage_key = Column(
        String(512),
        nullable=False,
        comment="保管先識別子"
    )
    valid_from = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="有効開始日時"
    )
    valid_to = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="有効終了日時(NULL=現在有効)"
    )
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
        comment="現在有効フラグ"
    )
    version = Column(
        Integer,
        nullable=False,
        default=1,
        comment="バージョン番号"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="登録日時"
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新日時"
    )

    # Constraints
    __table_args__ = (
        # アクティブなモデルの一意性制約
        # UniqueConstraintではなく、unique=TrueのIndexを使用する
        Index(
            "uq_fee_model_active",  # インデックス名
            "provider_id",
            "consumer_id",
            "data_id",
            unique=True,            # ユニーク制約として扱う
            postgresql_where=(Column("is_active") == True)
        ),
        # 税区分の値制約
        CheckConstraint(
            "tax_classification IN ('taxable', 'non_taxable')",
            name="ck_tax_classification"
        ),
        # 保管先タイプの値制約
        CheckConstraint(
            "storage_type IN ('provider_env', 'settlement_service')",
            name="ck_storage_type"
        ),
        # 税率は0以上
        CheckConstraint(
            "tax_rate >= 0",
            name="ck_tax_rate_positive"
        ),
        # 有効期間の整合性
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_valid_period"
        ),
        # 複合インデックス（検索用）
        Index(
            "ix_fee_model_provider_consumer_data",
            "provider_id",
            "consumer_id",
            "data_id"
        ),
        # 有効期間での検索用
        Index(
            "ix_fee_model_valid_period",
            "valid_from",
            "valid_to"
        ),
    )

    # Relationships
    payment_service = relationship(
        "PaymentService",
        back_populates="fee_models"
    )
    history_records = relationship(
        "FeeModelHistory",
        back_populates="fee_model",
        cascade="all, delete-orphan",
        order_by="FeeModelHistory.created_at.desc()"
    )

    def __repr__(self):
        return (
            f"<FeeModel(id={self.fee_model_id}, "
            f"name='{self.fee_model_name}', "
            f"version={self.version}, "
            f"is_active={self.is_active})>"
        )


class FeeModelHistory(Base):
    """利用料モデル履歴
    
    FeeModelの変更履歴を記録する
    すべての変更（作成、更新、削除）とトランザクション時のスナップショットを保持
    """
    
    __tablename__ = "fee_model_history"

    fee_model_history_id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="履歴ID"
    )
    fee_model_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fee_models.fee_model_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="元のモデルID"
    )
    fee_model_name = Column(
        String(255),
        nullable=False,
        comment="利用料モデル名"
    )
    price = Column(
        Numeric(15, 2),
        nullable=False,
        comment="金額"
    )
    tax_classification = Column(
        String(50),
        nullable=False,
        comment="税区分"
    )
    tax_rate = Column(
        Numeric(5, 4),
        nullable=False,
        comment="税率"
    )
    provider_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="データ提供者ID(外部システム)"
    )
    consumer_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="データ利用者ID(外部システム)"
    )
    data_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="データID(外部システム)"
    )
    payment_service_id = Column(
        PG_UUID(as_uuid=True),
        nullable=False,
        comment="決済サービスID"
    )
    storage_type = Column(
        String(100),
        nullable=False,
        comment="保管先タイプ"
    )
    storage_key = Column(
        String(512),
        nullable=False,
        comment="保管先識別子"
    )
    valid_from = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="有効開始日時"
    )
    valid_to = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="有効終了日時"
    )
    change_type = Column(
        String(50),
        nullable=False,
        comment="変更タイプ(create/update/delete/snapshot)"
    )
    change_reason = Column(
        Text,
        nullable=True,
        comment="変更理由"
    )
    version = Column(
        Integer,
        nullable=False,
        comment="バージョン番号"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="履歴記録日時"
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('create', 'update', 'delete', 'snapshot')",
            name="ck_change_type"
        ),
        # 複合インデックス（検索用）
        Index(
            "ix_fee_model_history_provider_consumer_data",
            "provider_id",
            "consumer_id",
            "data_id"
        ),
        # 時系列検索用
        Index(
            "ix_fee_model_history_created",
            "created_at"
        ),
        # モデルIDと変更タイプでの検索用
        Index(
            "ix_fee_model_history_model_type",
            "fee_model_id",
            "change_type"
        ),
    )

    # Relationships
    fee_model = relationship(
        "FeeModel",
        back_populates="history_records"
    )
    transactions = relationship(
        "Transaction",
        back_populates="fee_model_history"
    )

    def __repr__(self):
        return (
            f"<FeeModelHistory(id={self.fee_model_history_id}, "
            f"model_id={self.fee_model_id}, "
            f"change_type='{self.change_type}', "
            f"version={self.version})>"
        )


class PaymentServiceUserRegistration(Base):
    """決済サービスユーザ登録
    
    データ利用者とデータ提供者の決済サービスへの登録情報を管理
    """
    
    __tablename__ = "payment_service_user_registrations"

    payment_service_user_id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="決済サービスユーザID"
    )
    payment_service_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("payment_services.payment_service_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="決済サービスID"
    )
    consumer_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="データ利用者ID(外部システム)"
    )
    provider_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="データ提供者ID(外部システム)"
    )
    company_name = Column(
        String(255),
        nullable=True,
        comment="企業名"
    )
    department = Column(
        String(255),
        nullable=True,
        comment="部署名"
    )
    customer_name = Column(
        String(255),
        nullable=True,
        comment="担当者名"
    )
    zip_code = Column(
        String(20),
        nullable=True,
        comment="郵便番号"
    )
    address = Column(
        String(512),
        nullable=True,
        comment="住所"
    )
    tel_no = Column(
        String(20),
        nullable=True,
        comment="電話番号"
    )
    external_buyer_id = Column(
        String(20),
        nullable=True,
        index=True,
        comment="外部購入企業ID"
    )
    external_data = Column(
        JSONB,
        nullable=True,
        comment="外部決済サービス固有データ"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="登録日時"
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新日時"
    )

    # Constraints
    __table_args__ = (
        # 同一決済サービス内での一意性
        UniqueConstraint(
            "payment_service_id",
            "consumer_id",
            "provider_id",
            name="uq_payment_user_registration"
        ),
        # 複合インデックス（検索用）
        Index(
            "ix_payment_user_consumer_provider",
            "consumer_id",
            "provider_id"
        ),
    )

    # Relationships
    payment_service = relationship(
        "PaymentService",
        back_populates="user_registrations"
    )
    transactions = relationship(
        "Transaction",
        back_populates="payment_user"
    )

    def __repr__(self):
        return (
            f"<PaymentServiceUserRegistration("
            f"id={self.payment_service_user_id}, "
            f"consumer='{self.consumer_id}', "
            f"provider='{self.provider_id}')>"
        )


class Transaction(Base):
    """取引

    データ交換時の取引情報を管理
    料金情報はスナップショットとして保存され、後の変更の影響を受けない
    """

    __tablename__ = "transactions"

    transaction_id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        comment="取引ID"
    )
    tracking_id = Column(
        PG_UUID(as_uuid=True),
        nullable=True,  # 取引可否API時はNULL、データ交換状態登録APIで更新
        index=True,
        comment="トラッキングID"
    )
    external_transaction_id = Column(
        String(20),
        nullable=True,
        index=True,
        comment="外部取引ID（外部決済サービスの取引登録APIレスポンスから取得）"
    )
    fee_model_history_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("fee_model_history.fee_model_history_id", ondelete="RESTRICT"),
        nullable=True,  # 利用料モデル無しの場合はNULL
        index=True,
        comment="使用した履歴バージョンID"
    )
    payment_service_user_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "payment_service_user_registrations.payment_service_user_id",
            ondelete="RESTRICT"
        ),
        nullable=True,  # 利用料モデル無しの場合はNULL
        index=True,
        comment="決済サービスユーザID"
    )
    provider_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="データ提供者ID(外部システム/検索キー)"
    )
    consumer_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="データ利用者ID(外部システム/検索キー)"
    )
    data_id = Column(
        String(255),
        nullable=True,  # 利用料モデル無しの場合はNULL
        index=True,
        comment="データID(外部システム/検索キー)"
    )
    snapshot_price = Column(
        Numeric(15, 2),
        nullable=False,
        comment="スナップショット:金額"
    )
    snapshot_tax_rate = Column(
        Numeric(5, 4),
        nullable=False,
        comment="スナップショット:税率"
    )
    snapshot_tax_classification = Column(
        String(50),
        nullable=False,
        comment="スナップショット:税区分"
    )
    calculated_amount = Column(
        Numeric(15, 2),
        nullable=False,
        comment="計算済金額(税込)"
    )
    consumer_exchange_status = Column(
        String(50),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
        comment="消費者データ交換ステータス(pending/completed/failed)"
    )
    provider_exchange_status = Column(
        String(50),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
        comment="提供者データ交換ステータス(pending/completed/failed)"
    )
    l2_http_status = Column(
        String(10),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
        comment="L2ログHTTPステータス(pending/HTTPステータスコード)"
    )
    settlement_status = Column(
        String(50),
        nullable=False,
        default="unsettled",
        server_default="unsettled",
        index=True,
        comment="外部精算決済状態(settled/unsettled/cancelled)"
    )
    order_details = Column(
        Text,
        nullable=True,
        comment="注文内容"
    )
    request_date = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="請求日"
    )
    payment_deadline = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="支払期限"
    )
    paid_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="支払完了日時"
    )
    external_data = Column(
        JSONB,
        nullable=True,
        comment="外部決済サービス固有データ"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="登録日時"
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新日時"
    )

    # Constraints
    __table_args__ = (
        CheckConstraint(
            "consumer_exchange_status IN ('pending', 'completed', 'failed')",
            name="ck_consumer_exchange_status"
        ),
        CheckConstraint(
            "provider_exchange_status IN ('pending', 'completed', 'failed')",
            name="ck_provider_exchange_status"
        ),
        CheckConstraint(
            "settlement_status IN ('settled', 'unsettled', 'cancelled')",
            name="ck_settlement_status"
        ),
        # 支払期限は請求日より後
        CheckConstraint(
            "payment_deadline IS NULL OR request_date IS NULL OR payment_deadline >= request_date",
            name="ck_payment_deadline_after_request"
        ),
        # 複合インデックス（検索用）
        Index(
            "ix_transaction_provider_consumer_data",
            "provider_id",
            "consumer_id",
            "data_id"
        ),
        # 請求日での検索用
        Index(
            "ix_transaction_request_date",
            "request_date"
        ),
    )

    # Relationships
    fee_model_history = relationship(
        "FeeModelHistory",
        back_populates="transactions"
    )
    payment_user = relationship(
        "PaymentServiceUserRegistration",
        back_populates="transactions"
    )

    def __repr__(self):
        return (
            f"<Transaction(transaction_id={self.transaction_id}, "
            f"tracking_id={self.tracking_id}, "
            f"external_transaction_id={self.external_transaction_id}, "
            f"consumer_status='{self.consumer_exchange_status}', "
            f"provider_status='{self.provider_exchange_status}', "
            f"amount={self.calculated_amount})>"
        )