"""transactions に completed_at（データ交換完了日時）を追加

支払/請求予定額の期間判定・レスポンスの completed_at・NP の請求書発行基準日は
created_at（取引可否＝与信時刻）ではなく、データ交換状態登録/確定APIで受け取った
完了日時を使う。既存行は NULL のままとし、参照側は COALESCE(completed_at, created_at) で扱う。

Revision ID: 003_completed_at
Revises: 002_tracking_unique
Create Date: 2026-09-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '003_completed_at'
down_revision: Union[str, None] = '002_tracking_unique'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'transactions',
        sa.Column(
            'completed_at', sa.DateTime(timezone=True), nullable=True,
            comment='データ交換完了日時（状態登録/確定APIで受け取った completed_at。支払/請求の期間判定に使用）',
        ),
    )
    op.create_index('ix_transactions_completed_at', 'transactions', ['completed_at'])


def downgrade() -> None:
    op.drop_index('ix_transactions_completed_at', table_name='transactions')
    op.drop_column('transactions', 'completed_at')
