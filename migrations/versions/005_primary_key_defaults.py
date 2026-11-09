"""主キーに DEFAULT gen_random_uuid() を設定し、取引に取引可否確認のトラッキングIDを追加する

1. 主キーの既定値
   設計書では各テーブルの主キーは DB 側で自動採番する定義だが、001 では
   transactions と email_send_logs 以外に既定値が付いていなかった。
   アプリはアプリ側で UUID を生成するため動作に影響はないが、psql などで
   主キーを省略して INSERT すると NOT NULL 違反になるため、設計書どおりに揃える。

2. transactions.eligibility_tracking_id（取引可否確認のトラッキングID）
   事前登録した取引の tracking_id は、状態登録・金額確定で引き当てた時にデータ取得の
   X-TrackingId に付け替える。付け替えた後も取引可否確認の X-TrackingId を判定に使えるよう、
   別の列に残す。
   - 同じ X-TrackingId での取引可否確認の再送を、引き当て後も検出する（NP に二重に取引登録しない）
   - 状態登録・金額確定の tracking_id に取引可否確認の X-TrackingId が指定された場合を、
     引き当て後も検出する（409）
   既存の行のうち、事前登録のまま（両当事者とも pending）の行は tracking_id が取引可否確認の
   X-TrackingId のため、その値を設定する。引き当て済みの行と 1.0.0 で事前登録した行
   （tracking_id が NULL）は、取引可否確認の X-TrackingId が分からないため NULL のままにする。

Revision ID: 005_pk_defaults
Revises: 004_effective_completed_at
Create Date: 2026-10-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '005_pk_defaults'
down_revision: Union[str, None] = '004_effective_completed_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRIMARY_KEYS = [
    ('payment_services', 'payment_service_id'),
    ('fee_models', 'fee_model_id'),
    ('fee_model_history', 'fee_model_history_id'),
    ('payment_service_user_registrations', 'payment_service_user_id'),
]


def upgrade() -> None:
    for table, column in PRIMARY_KEYS:
        op.alter_column(table, column, server_default=sa.text('gen_random_uuid()'))

    op.add_column(
        'transactions',
        sa.Column(
            'eligibility_tracking_id', postgresql.UUID(as_uuid=True), nullable=True,
            comment='取引可否確認のトラッキングID',
        ),
    )
    op.execute(
        "UPDATE transactions SET eligibility_tracking_id = tracking_id "
        "WHERE tracking_id IS NOT NULL "
        "AND consumer_exchange_status = 'pending' AND provider_exchange_status = 'pending'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_transaction_eligibility_tracking_data "
        "ON transactions (eligibility_tracking_id, COALESCE(data_id, '')) "
        "WHERE eligibility_tracking_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index('uq_transaction_eligibility_tracking_data', table_name='transactions')
    op.drop_column('transactions', 'eligibility_tracking_id')

    for table, column in PRIMARY_KEYS:
        op.alter_column(table, column, server_default=None)
