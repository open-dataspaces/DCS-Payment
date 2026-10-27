"""主キーに DEFAULT gen_random_uuid() を設定する

設計書では各テーブルの主キーは DB 側で自動採番する定義だが、001 では
transactions と email_send_logs 以外に既定値が付いていなかった。
アプリはアプリ側で UUID を生成するため動作に影響はないが、psql などで
主キーを省略して INSERT すると NOT NULL 違反になるため、設計書どおりに揃える。

Revision ID: 005_pk_defaults
Revises: 004_effective_completed_at
Create Date: 2026-10-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

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


def downgrade() -> None:
    for table, column in PRIMARY_KEYS:
        op.alter_column(table, column, server_default=None)
