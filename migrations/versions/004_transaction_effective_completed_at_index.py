"""transactions の完了日時インデックスを COALESCE(completed_at, created_at) の式インデックスにする

支払/請求の期間判定・L2ログ突合の対象判定は COALESCE(completed_at, created_at) で
絞り込むため、003 で作成した completed_at 単独のインデックスは使われない。
同じ式のインデックスに置き換える。

Revision ID: 004_effective_completed_at
Revises: 003_completed_at
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '004_effective_completed_at'
down_revision: Union[str, None] = '003_completed_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_transactions_completed_at', table_name='transactions')
    op.create_index(
        'ix_transactions_effective_completed_at', 'transactions',
        [sa.text('COALESCE(completed_at, created_at)')],
    )


def downgrade() -> None:
    op.drop_index('ix_transactions_effective_completed_at', table_name='transactions')
    op.create_index('ix_transactions_completed_at', 'transactions', ['completed_at'])
