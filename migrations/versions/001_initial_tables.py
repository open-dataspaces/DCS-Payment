"""Initial tables - 全テーブル統合版

Revision ID: 001_initial
Revises:
Create Date: 2026-01-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ===========================================
    # ENUM Types
    # ===========================================
    notification_status = postgresql.ENUM(
        'enabled', 'disabled', 'deleted',
        name='notificationstatus',
        create_type=False
    )
    confirmation_status = postgresql.ENUM(
        'confirmed', 'unconfirmed', 'deleted',
        name='confirmationstatus',
        create_type=False
    )

    # Create ENUM types
    op.execute("CREATE TYPE notificationstatus AS ENUM ('enabled', 'disabled', 'deleted')")
    op.execute("CREATE TYPE confirmationstatus AS ENUM ('confirmed', 'unconfirmed', 'deleted')")

    # ===========================================
    # Payment Services Table
    # ===========================================
    op.create_table(
        'payment_services',
        sa.Column('payment_service_id', postgresql.UUID(as_uuid=True), nullable=False, comment='決済サービスID'),
        sa.Column('payment_service_name', sa.String(255), nullable=False, comment='決済サービス名'),
        sa.Column('payment_service_url', sa.String(512), nullable=False, comment='決済サービスURL'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='登録日時'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='更新日時'),
        sa.PrimaryKeyConstraint('payment_service_id')
    )

    # ===========================================
    # Fee Models Table
    # ===========================================
    op.create_table(
        'fee_models',
        sa.Column('fee_model_id', postgresql.UUID(as_uuid=True), nullable=False, comment='利用料モデルID'),
        sa.Column('fee_model_name', sa.String(255), nullable=False, comment='利用料モデル名'),
        sa.Column('price', sa.Numeric(15, 2), nullable=False, comment='金額'),
        sa.Column('tax_classification', sa.String(50), nullable=False, comment='税区分(課税/非課税)'),
        sa.Column('tax_rate', sa.Numeric(5, 4), nullable=False, comment='税率'),
        sa.Column('provider_id', sa.String(255), nullable=False, comment='データ提供者ID(外部システム)'),
        sa.Column('consumer_id', sa.String(255), nullable=False, comment='データ利用者ID(外部システム)'),
        sa.Column('data_id', sa.String(255), nullable=False, comment='データID(外部システム)'),
        sa.Column('payment_service_id', postgresql.UUID(as_uuid=True), nullable=False, comment='決済サービスID'),
        sa.Column('storage_type', sa.String(100), nullable=False, comment='保管先タイプ(provider_env/settlement_service)'),
        sa.Column('storage_key', sa.String(512), nullable=False, comment='保管先識別子'),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False, comment='有効開始日時'),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True, comment='有効終了日時(NULL=現在有効)'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true'), comment='現在有効フラグ'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1', comment='バージョン番号'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='登録日時'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='更新日時'),
        sa.PrimaryKeyConstraint('fee_model_id'),
        sa.ForeignKeyConstraint(['payment_service_id'], ['payment_services.payment_service_id'], ondelete='RESTRICT'),
        sa.CheckConstraint("tax_classification IN ('taxable', 'non_taxable')", name='ck_tax_classification'),
        sa.CheckConstraint("storage_type IN ('provider_env', 'settlement_service')", name='ck_storage_type'),
        # Note: ck_price_positive制約は削除（マイナス金額を許容）
        sa.CheckConstraint('tax_rate >= 0', name='ck_tax_rate_positive'),
        sa.CheckConstraint('valid_to IS NULL OR valid_to > valid_from', name='ck_valid_period'),
    )

    # Fee Models Indexes
    op.create_index('ix_fee_models_provider_id', 'fee_models', ['provider_id'])
    op.create_index('ix_fee_models_consumer_id', 'fee_models', ['consumer_id'])
    op.create_index('ix_fee_models_data_id', 'fee_models', ['data_id'])
    op.create_index('ix_fee_models_is_active', 'fee_models', ['is_active'])
    op.create_index('ix_fee_model_provider_consumer_data', 'fee_models', ['provider_id', 'consumer_id', 'data_id'])
    op.create_index('ix_fee_model_valid_period', 'fee_models', ['valid_from', 'valid_to'])

    # Partial unique index for active fee models
    op.execute("""
        CREATE UNIQUE INDEX uq_fee_model_active
        ON fee_models (provider_id, consumer_id, data_id)
        WHERE is_active = true
    """)

    # ===========================================
    # Fee Model History Table
    # ===========================================
    op.create_table(
        'fee_model_history',
        sa.Column('fee_model_history_id', postgresql.UUID(as_uuid=True), nullable=False, comment='履歴ID'),
        sa.Column('fee_model_id', postgresql.UUID(as_uuid=True), nullable=False, comment='元のモデルID'),
        sa.Column('fee_model_name', sa.String(255), nullable=False, comment='利用料モデル名'),
        sa.Column('price', sa.Numeric(15, 2), nullable=False, comment='金額'),
        sa.Column('tax_classification', sa.String(50), nullable=False, comment='税区分'),
        sa.Column('tax_rate', sa.Numeric(5, 4), nullable=False, comment='税率'),
        sa.Column('provider_id', sa.String(255), nullable=False, comment='データ提供者ID(外部システム)'),
        sa.Column('consumer_id', sa.String(255), nullable=False, comment='データ利用者ID(外部システム)'),
        sa.Column('data_id', sa.String(255), nullable=False, comment='データID(外部システム)'),
        sa.Column('payment_service_id', postgresql.UUID(as_uuid=True), nullable=False, comment='決済サービスID'),
        sa.Column('storage_type', sa.String(100), nullable=False, comment='保管先タイプ'),
        sa.Column('storage_key', sa.String(512), nullable=False, comment='保管先識別子'),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False, comment='有効開始日時'),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=False, comment='有効終了日時'),
        sa.Column('change_type', sa.String(50), nullable=False, comment='変更タイプ(create/update/delete/snapshot)'),
        sa.Column('change_reason', sa.Text(), nullable=True, comment='変更理由'),
        sa.Column('version', sa.Integer(), nullable=False, comment='バージョン番号'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='履歴記録日時'),
        sa.PrimaryKeyConstraint('fee_model_history_id'),
        sa.ForeignKeyConstraint(['fee_model_id'], ['fee_models.fee_model_id'], ondelete='CASCADE'),
        sa.CheckConstraint("change_type IN ('create', 'update', 'delete', 'snapshot')", name='ck_change_type'),
    )

    # Fee Model History Indexes
    op.create_index('ix_fee_model_history_fee_model_id', 'fee_model_history', ['fee_model_id'])
    op.create_index('ix_fee_model_history_provider_id', 'fee_model_history', ['provider_id'])
    op.create_index('ix_fee_model_history_consumer_id', 'fee_model_history', ['consumer_id'])
    op.create_index('ix_fee_model_history_data_id', 'fee_model_history', ['data_id'])
    op.create_index('ix_fee_model_history_provider_consumer_data', 'fee_model_history', ['provider_id', 'consumer_id', 'data_id'])
    op.create_index('ix_fee_model_history_created', 'fee_model_history', ['created_at'])
    op.create_index('ix_fee_model_history_model_type', 'fee_model_history', ['fee_model_id', 'change_type'])

    # ===========================================
    # Payment Service User Registrations Table
    # ===========================================
    op.create_table(
        'payment_service_user_registrations',
        sa.Column('payment_service_user_id', postgresql.UUID(as_uuid=True), nullable=False, comment='決済サービスユーザID'),
        sa.Column('payment_service_id', postgresql.UUID(as_uuid=True), nullable=False, comment='決済サービスID'),
        sa.Column('consumer_id', sa.String(255), nullable=False, comment='データ利用者ID(外部システム)'),
        sa.Column('provider_id', sa.String(255), nullable=False, comment='データ提供者ID(外部システム)'),
        # 配送先情報 (002_delivery_info)
        sa.Column('company_name', sa.String(255), nullable=True, comment='企業名'),
        sa.Column('department', sa.String(255), nullable=True, comment='部署名'),
        sa.Column('customer_name', sa.String(255), nullable=True, comment='担当者名'),
        sa.Column('zip_code', sa.String(20), nullable=True, comment='郵便番号'),
        sa.Column('address', sa.String(512), nullable=True, comment='住所'),
        sa.Column('tel_no', sa.String(20), nullable=True, comment='電話番号'),
        # 外部決済サービス連携 (004_np_buyer_id -> 005_rename_np_to_external)
        sa.Column('external_buyer_id', sa.String(20), nullable=True, comment='外部購入企業ID'),
        sa.Column('external_data', JSONB, nullable=True, comment='外部決済サービス固有データ'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='登録日時'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='更新日時'),
        sa.PrimaryKeyConstraint('payment_service_user_id'),
        sa.ForeignKeyConstraint(['payment_service_id'], ['payment_services.payment_service_id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('payment_service_id', 'consumer_id', 'provider_id', name='uq_payment_user_registration'),
    )

    # Payment Service User Registrations Indexes
    op.create_index('ix_payment_service_user_registrations_payment_service_id', 'payment_service_user_registrations', ['payment_service_id'])
    op.create_index('ix_payment_service_user_registrations_consumer_id', 'payment_service_user_registrations', ['consumer_id'])
    op.create_index('ix_payment_service_user_registrations_provider_id', 'payment_service_user_registrations', ['provider_id'])
    op.create_index('ix_payment_user_consumer_provider', 'payment_service_user_registrations', ['consumer_id', 'provider_id'])
    op.create_index('ix_payment_user_external_buyer_id', 'payment_service_user_registrations', ['external_buyer_id'])

    # ===========================================
    # Transactions Table
    # ===========================================
    op.create_table(
        'transactions',
        sa.Column('transaction_id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()'), comment='取引ID'),
        sa.Column('tracking_id', postgresql.UUID(as_uuid=True), nullable=True, comment='トラッキングID'),
        sa.Column('fee_model_history_id', postgresql.UUID(as_uuid=True), nullable=True, comment='使用した履歴バージョンID(利用料モデル無しの場合NULL)'),
        sa.Column('payment_service_user_id', postgresql.UUID(as_uuid=True), nullable=True, comment='決済サービスユーザID(利用料モデル無しの場合NULL)'),
        sa.Column('provider_id', sa.String(255), nullable=False, comment='データ提供者ID(外部システム/検索キー)'),
        sa.Column('consumer_id', sa.String(255), nullable=False, comment='データ利用者ID(外部システム/検索キー)'),
        sa.Column('data_id', sa.String(255), nullable=True, comment='データID(利用料モデル無しの場合NULL)'),
        sa.Column('snapshot_price', sa.Numeric(15, 2), nullable=False, comment='スナップショット:金額'),
        sa.Column('snapshot_tax_rate', sa.Numeric(5, 4), nullable=False, comment='スナップショット:税率'),
        sa.Column('snapshot_tax_classification', sa.String(50), nullable=False, comment='スナップショット:税区分'),
        sa.Column('calculated_amount', sa.Numeric(15, 2), nullable=False, comment='計算済金額(税込)'),
        sa.Column('consumer_exchange_status', sa.String(50), nullable=False, server_default='pending', comment='消費者データ交換ステータス(pending/completed/failed)'),
        sa.Column('provider_exchange_status', sa.String(50), nullable=False, server_default='pending', comment='提供者データ交換ステータス(pending/completed/failed)'),
        sa.Column('l2_http_status', sa.String(10), nullable=False, server_default='pending', comment='L2ログHTTPステータス(pending/HTTPステータスコード)'),
        sa.Column('settlement_status', sa.String(50), nullable=False, server_default='unsettled', comment='精算決済状態(settled/unsettled/cancelled)'),
        sa.Column('order_details', sa.Text(), nullable=True, comment='注文内容'),
        sa.Column('request_date', sa.DateTime(timezone=True), nullable=True, comment='請求日'),
        sa.Column('payment_deadline', sa.DateTime(timezone=True), nullable=True, comment='支払期限'),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True, comment='支払完了日時'),
        # 外部決済サービス連携 (003_np_transaction -> 005_rename_np_to_external)
        sa.Column('external_transaction_id', sa.String(20), nullable=True, comment='外部取引ID'),
        sa.Column('external_data', JSONB, nullable=True, comment='外部決済サービス固有データ'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='登録日時'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='更新日時'),
        sa.PrimaryKeyConstraint('transaction_id'),
        sa.ForeignKeyConstraint(['fee_model_history_id'], ['fee_model_history.fee_model_history_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['payment_service_user_id'], ['payment_service_user_registrations.payment_service_user_id'], ondelete='RESTRICT'),
        sa.CheckConstraint("consumer_exchange_status IN ('pending', 'completed', 'failed')", name='ck_consumer_exchange_status'),
        sa.CheckConstraint("provider_exchange_status IN ('pending', 'completed', 'failed')", name='ck_provider_exchange_status'),
        sa.CheckConstraint("settlement_status IN ('settled', 'unsettled', 'cancelled')", name='ck_settlement_status'),
                sa.CheckConstraint('payment_deadline IS NULL OR request_date IS NULL OR payment_deadline >= request_date', name='ck_payment_deadline_after_request'),
    )

    # Transactions Indexes
    op.create_index('ix_transactions_tracking_id', 'transactions', ['tracking_id'])
    op.create_index('ix_transactions_fee_model_history_id', 'transactions', ['fee_model_history_id'])
    op.create_index('ix_transactions_payment_service_user_id', 'transactions', ['payment_service_user_id'])
    op.create_index('ix_transactions_provider_id', 'transactions', ['provider_id'])
    op.create_index('ix_transactions_consumer_id', 'transactions', ['consumer_id'])
    op.create_index('ix_transactions_data_id', 'transactions', ['data_id'])
    op.create_index('ix_transactions_consumer_exchange_status', 'transactions', ['consumer_exchange_status'])
    op.create_index('ix_transactions_provider_exchange_status', 'transactions', ['provider_exchange_status'])
    op.create_index('ix_transactions_l2_http_status', 'transactions', ['l2_http_status'])
    op.create_index('ix_transactions_settlement_status', 'transactions', ['settlement_status'])
    op.create_index('ix_transaction_provider_consumer_data', 'transactions', ['provider_id', 'consumer_id', 'data_id'])
    op.create_index('ix_transaction_request_date', 'transactions', ['request_date'])
    op.create_index('ix_transactions_external_transaction_id', 'transactions', ['external_transaction_id'])

    # ===========================================
    # Notification Type Table
    # ===========================================
    op.create_table(
        'notification_type',
        sa.Column('type_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('type_code', sa.String(100), nullable=False),
        sa.Column('type_name', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('type_id'),
        sa.UniqueConstraint('type_code'),
        sa.UniqueConstraint('type_name'),
    )

    # ===========================================
    # Notification Target List Table
    # ===========================================
    op.create_table(
        'notification_target_list',
        sa.Column('target_list_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('owner_id', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('target_list_id'),
    )

    # ===========================================
    # Target IDs Table
    # ===========================================
    op.create_table(
        'target_ids',
        sa.Column('target_list_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('target_id', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('target_list_id', 'target_id'),
        sa.ForeignKeyConstraint(['target_list_id'], ['notification_target_list.target_list_id'], ondelete='CASCADE'),
    )

    # ===========================================
    # Notification Table
    # ===========================================
    op.create_table(
        'notification',
        sa.Column('notification_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('type_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('target_ids', postgresql.ARRAY(sa.String(255)), nullable=True, comment='通知受信者IDリスト'),
        sa.Column('status', postgresql.ENUM('enabled', 'disabled', 'deleted', name='notificationstatus', create_type=False), nullable=False),
        sa.Column('data_id', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('notification_id'),
        sa.ForeignKeyConstraint(['type_id'], ['notification_type.type_id']),
        sa.CheckConstraint('array_length(target_ids, 1) > 0 OR target_ids IS NULL', name='check_target_ids_non_empty'),
    )

    # Notification Indexes
    op.create_index('ix_notification_type_id', 'notification', ['type_id'])

    # ===========================================
    # Notification Target List Map Table (Many-to-Many)
    # ===========================================
    op.create_table(
        'notification_target_list_map',
        sa.Column('notification_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('target_list_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint('notification_id', 'target_list_id'),
        sa.ForeignKeyConstraint(['notification_id'], ['notification.notification_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_list_id'], ['notification_target_list.target_list_id'], ondelete='CASCADE'),
    )

    # ===========================================
    # Notification Confirmed Table
    # ===========================================
    op.create_table(
        'notification_confirmed',
        sa.Column('notification_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('target_id', sa.String(255), nullable=False),
        sa.Column('status', postgresql.ENUM('confirmed', 'unconfirmed', 'deleted', name='confirmationstatus', create_type=False), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('notification_id', 'target_id'),
        sa.ForeignKeyConstraint(['notification_id'], ['notification.notification_id'], ondelete='CASCADE'),
    )

    # ===========================================
    # Notification Data Confirmed Table
    # ===========================================
    op.create_table(
        'notification_data_confirmed',
        sa.Column('notification_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('target_id', sa.String(255), nullable=False),
        sa.Column('status', postgresql.ENUM('confirmed', 'unconfirmed', 'deleted', name='confirmationstatus', create_type=False), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('notification_id', 'target_id'),
        sa.ForeignKeyConstraint(['notification_id'], ['notification.notification_id'], ondelete='CASCADE'),
    )

    # ===========================================
    # Email Send Logs Table
    # ===========================================
    op.create_table(
        'email_send_logs',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text('gen_random_uuid()'),
            comment='ログID'
        ),
        sa.Column(
            'user_id',
            sa.String(255),
            nullable=False,
            comment='ユーザID（provider_id または consumer_id）'
        ),
        sa.Column(
            'user_type',
            sa.String(50),
            nullable=False,
            comment='ユーザ種別（provider または consumer）'
        ),
        sa.Column(
            'email_address',
            sa.String(255),
            nullable=False,
            comment='送信先メールアドレス'
        ),
        sa.Column(
            'subject',
            sa.String(500),
            nullable=False,
            comment='メール件名'
        ),
        sa.Column(
            'send_status',
            sa.String(50),
            nullable=False,
            comment='送信ステータス（success, failed, skipped）'
        ),
        sa.Column(
            'message_id',
            sa.String(255),
            nullable=True,
            comment='SES MessageId'
        ),
        sa.Column(
            'error_message',
            sa.Text(),
            nullable=True,
            comment='エラーメッセージ'
        ),
        sa.Column(
            'period_start',
            sa.DateTime(timezone=True),
            nullable=False,
            comment='対象期間開始日'
        ),
        sa.Column(
            'period_end',
            sa.DateTime(timezone=True),
            nullable=False,
            comment='対象期間終了日'
        ),
        sa.Column(
            'total_amount',
            sa.Numeric(15, 2),
            nullable=True,
            comment='合計金額'
        ),
        sa.Column(
            'item_count',
            sa.Integer(),
            nullable=True,
            comment='明細件数'
        ),
        sa.Column(
            'sent_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment='送信日時'
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment='登録日時'
        ),
        sa.PrimaryKeyConstraint('id'),
        comment='メール送信ログ'
    )

    # Email Send Logs Indexes
    op.create_index('idx_email_send_logs_user_id', 'email_send_logs', ['user_id'])
    op.create_index('idx_email_send_logs_sent_at', 'email_send_logs', ['sent_at'])
    op.create_index('idx_email_send_logs_send_status', 'email_send_logs', ['send_status'])


def downgrade() -> None:
    """
    WARNING: このダウングレードは全てのテーブルとデータを削除します。
    本番環境での実行は推奨されません。

    実行する場合は環境変数 ALLOW_DESTRUCTIVE_MIGRATION=true を設定してください。
    """
    import os

    # 本番環境での安全チェック
    allow_destructive = os.environ.get('ALLOW_DESTRUCTIVE_MIGRATION', 'false').lower() == 'true'
    environment = os.environ.get('ENVIRONMENT', 'PRODUCTION')

    if environment == 'PRODUCTION' and not allow_destructive:
        raise RuntimeError(
            "DANGER: Downgrade of initial migration will DELETE ALL DATA. "
            "This operation is blocked in PRODUCTION. "
            "If you really want to proceed, set ALLOW_DESTRUCTIVE_MIGRATION=true"
        )

    # Drop email_send_logs
    op.drop_index('idx_email_send_logs_send_status', table_name='email_send_logs')
    op.drop_index('idx_email_send_logs_sent_at', table_name='email_send_logs')
    op.drop_index('idx_email_send_logs_user_id', table_name='email_send_logs')
    op.drop_table('email_send_logs')

    # Drop tables in reverse order (due to foreign key dependencies)
    op.drop_table('notification_data_confirmed')
    op.drop_table('notification_confirmed')
    op.drop_table('notification_target_list_map')
    op.drop_table('notification')
    op.drop_table('target_ids')
    op.drop_table('notification_target_list')
    op.drop_table('notification_type')
    op.drop_table('transactions')
    op.drop_table('payment_service_user_registrations')
    op.drop_table('fee_model_history')
    op.drop_index('uq_fee_model_active', table_name='fee_models')
    op.drop_table('fee_models')
    op.drop_table('payment_services')

    # Drop ENUM types
    op.execute('DROP TYPE IF EXISTS confirmationstatus')
    op.execute('DROP TYPE IF EXISTS notificationstatus')
