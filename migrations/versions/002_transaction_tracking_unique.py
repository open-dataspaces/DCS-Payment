"""transactions に (tracking_id, data_id) の一意インデックスを追加

取引可否APIで事前登録する Transaction 行に tracking_id を保存するようにしたため、
同一 tracking_id 配下で data_id（利用料モデル無しは NULL を '' として扱う）が
重複しないことを DB で保証する。二重紐付け・二重登録の防止。

以前の状態登録API は、tracking_id 未設定の事前登録行をすべて同じ tracking_id に
紐付けていたため、既存データに重複がある場合がある。重複行はそれぞれ別の NP 取引を
持ち二重請求の可能性があるため自動では解消せず、一覧を出して中止する。
事前に次の SQL で重複が無いことを確認しておくこと。

    SELECT tracking_id, COALESCE(data_id, '') AS data_id, COUNT(*)
    FROM transactions WHERE tracking_id IS NOT NULL
    GROUP BY 1, 2 HAVING COUNT(*) > 1;

Revision ID: 002_tracking_unique
Revises: 001_initial
Create Date: 2026-09-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '002_tracking_unique'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    duplicates = op.get_bind().execute(sa.text(
        "SELECT tracking_id, COALESCE(data_id, '') AS data_id, COUNT(*) AS cnt "
        "FROM transactions WHERE tracking_id IS NOT NULL "
        "GROUP BY 1, 2 HAVING COUNT(*) > 1 ORDER BY 1, 2"
    )).fetchall()
    if duplicates:
        rows = "\n".join(
            f"  tracking_id={r.tracking_id}, data_id={r.data_id!r}, count={r.cnt}" for r in duplicates
        )
        raise RuntimeError(
            "Cannot create uq_transaction_tracking_data: duplicate (tracking_id, data_id) rows exist. "
            "Resolve them manually (check for double billing) and rerun the migration.\n" + rows
        )

    op.execute(
        "CREATE UNIQUE INDEX uq_transaction_tracking_data "
        "ON transactions (tracking_id, COALESCE(data_id, '')) "
        "WHERE tracking_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index('uq_transaction_tracking_data', table_name='transactions')
