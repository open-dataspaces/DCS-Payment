"""金額計算ユーティリティ

金額の丸めは本モジュールに集約する。
- 税込金額は円単位（整数）に丸める（円未満は切り捨て）。
- DB(transactions.calculated_amount)・外部決済サービス(NP掛け払い)への送信額・
  支払/請求予定額 API の返却額は、すべてこの丸め後の値を使う。
"""
import decimal
from decimal import Decimal

# transactions.calculated_amount / fee_models.price は Numeric(15, 2)
MAX_AMOUNT = Decimal("9999999999999.99")

YEN = Decimal("1")


def to_decimal(value) -> Decimal:
    """float 由来の誤差を持ち込まないよう文字列経由で Decimal 化する"""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def calculate_amount_with_tax(price, tax_rate, tax_classification: str) -> Decimal:
    """税込金額を計算し、円単位に丸めて返す

    Args:
        price: 税抜金額
        tax_rate: 税率（0.1 = 10%）
        tax_classification: 'taxable' / 'non_taxable'
    """
    price = to_decimal(price)
    tax_rate = to_decimal(tax_rate)
    if tax_classification == "taxable":
        amount = price * (Decimal("1") + tax_rate)
    else:
        amount = price
    return amount.quantize(YEN, rounding=decimal.ROUND_DOWN)


def to_external_amount_str(amount) -> str:
    """外部決済サービス（NP掛け払い）に送る金額文字列（整数円）を返す

    丸め済みの値を受け取る前提。整数でない値が来た場合は不整合なので例外にする。
    """
    amount = to_decimal(amount)
    if amount != amount.to_integral_value():
        raise ValueError(f"amount must be an integral yen value: {amount}")
    return str(int(amount))


def tax_rate_to_percent(tax_rate) -> int:
    """税率（0.1）をパーセント整数（10）に変換する"""
    tax_rate = to_decimal(tax_rate)
    return int((tax_rate * Decimal("100")).quantize(YEN, rounding=decimal.ROUND_HALF_UP))


def taxed_amount_exact(price, tax_rate, tax_classification: str) -> Decimal:
    """丸め前の税込金額（上限チェック用）"""
    price = to_decimal(price)
    tax_rate = to_decimal(tax_rate)
    if tax_classification == "taxable":
        return price * (Decimal("1") + tax_rate)
    return price


def validate_amount_range(price, tax_rate, tax_classification: str) -> None:
    """税抜金額と税込金額が DB カラム（Numeric(15,2)）に収まることを検証する

    利用料モデルの price は 0・負の値も許容するため、-MAX_AMOUNT〜MAX_AMOUNT の範囲で検証する
    （0 以上に限る項目は、呼び出し側のスキーマで ge=0 を指定する）。

    Raises:
        ValueError: 範囲外の場合
    """
    price = to_decimal(price)
    if price > MAX_AMOUNT:
        raise ValueError(f"price must be {MAX_AMOUNT} or less")
    if price < -MAX_AMOUNT:
        raise ValueError(f"price must be -{MAX_AMOUNT} or greater")
    # 円未満は切り捨てるため、丸め後の絶対値は丸め前を超えない（丸め前の値で検証すれば足りる）
    taxed = taxed_amount_exact(price, tax_rate, tax_classification)
    if taxed > MAX_AMOUNT:
        raise ValueError(f"amount including tax ({taxed}) exceeds the maximum {MAX_AMOUNT}")
    if taxed < -MAX_AMOUNT:
        raise ValueError(f"amount including tax ({taxed}) is below the minimum -{MAX_AMOUNT}")
