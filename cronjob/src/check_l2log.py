import base64

import boto3
import gzip
import io
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import psycopg2
import requests

# ── ログ設定 ──
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# ── パラメータ（環境変数から取得） ──
bucket = os.environ.get("S3_BUCKET", "oranus-hieng")
prefix = os.environ.get("S3_PREFIX", "payment-logs/")

# DB接続情報（Secretから環境変数として渡される）
DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_NAME = os.environ.get("POSTGRES_DB", "postgres")
DB_USER = os.environ.get("POSTGRES_USER", "postgres")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")

# NP掛け払い設定（環境変数から取得）
NP_BASE_URL = os.environ.get("NP_KAKEBARAI_BASE_URL", "")
NP_SHOP_CODE = os.environ.get("NP_KAKEBARAI_SHOP_CODE", "")
NP_SP_CODE = os.environ.get("NP_KAKEBARAI_SP_CODE", "")
NP_TERMINAL_ID = os.environ.get("NP_KAKEBARAI_TERMINAL_ID", "")

# JST (UTC+9) で今日と前日を算出
JST = timezone(timedelta(hours=9))
today = datetime.now(JST).date()
yesterday = today - timedelta(days=1)
target_dates = [yesterday, today]


def get_db_connection():
    """DB接続を取得"""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def get_tracking_ids(start_date, end_date):
    """Transactions テーブルから対象期間の tracking_id を取得（l2_http_status='pending'のみ）"""
    logger.info("DB から tracking_id を取得中 (%s 〜 %s)", start_date, end_date)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT tracking_id
                FROM transactions
                WHERE created_at >= %s
                  AND created_at < %s
                  AND tracking_id IS NOT NULL
                  AND l2_http_status = 'pending'
                """,
                (
                    datetime.combine(start_date, datetime.min.time()),
                    datetime.combine(end_date + timedelta(days=1), datetime.min.time()),
                ),
            )
            rows = cur.fetchall()
            tracking_ids = {row[0] for row in rows}
            logger.info("DB から %d 件の tracking_id を取得", len(tracking_ids))
            return tracking_ids
    finally:
        conn.close()
        logger.debug("DB 接続クローズ")


def update_l2_http_status(found_map, not_found):
    """照合結果に基づいて transactions テーブルの l2_http_status を更新

    Args:
        found_map: dict {tracking_id: statusCode} ログで見つかった tracking_id と実際の statusCode
        not_found: set ログで見つからなかった tracking_id（'pending' のまま据え置き）
    """
    if not found_map and not not_found:
        logger.info("更新対象の tracking_id がありません")
        return

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if found_map:
                # statusCode ごとにグループ化して一括更新
                status_groups = {}
                for tid, status_code in found_map.items():
                    if status_code not in status_groups:
                        status_groups[status_code] = []
                    status_groups[status_code].append(tid)

                for status_code, tid_list in status_groups.items():
                    cur.execute(
                        """
                        UPDATE transactions
                        SET l2_http_status = %s, updated_at = NOW()
                        WHERE tracking_id = ANY(%s::uuid[])
                          AND l2_http_status = 'pending'
                        """,
                        (status_code, tid_list),
                    )
                    updated_count = cur.rowcount
                    logger.info(
                        "l2_http_status='%s' に更新: %d 件 (tracking_id %d 件)",
                        status_code, updated_count, len(tid_list)
                    )

            if not_found:
                logger.info(
                    "ログに未検出の tracking_id: %d 件 (l2_http_status='pending' のまま据え置き)",
                    len(not_found)
                )

        conn.commit()
        logger.info("l2_http_status の更新完了")
    except Exception:
        conn.rollback()
        logger.exception("l2_http_status の更新中にエラーが発生")
        raise
    finally:
        conn.close()
        logger.debug("DB 接続クローズ")


def update_settlement_status():
    """settlement_status を更新

    確定条件（l2_http_status='200' AND consumer_exchange_status='completed'
    AND provider_exchange_status='completed'）を満たすレコードを 'settled' に更新する。
    """
    logger.info("=== settlement_status 更新処理 開始 ===")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE transactions
                SET settlement_status = 'settled', updated_at = NOW()
                WHERE settlement_status = 'unsettled'
                  AND l2_http_status = '200'
                  AND consumer_exchange_status = 'completed'
                  AND provider_exchange_status = 'completed'
                """,
            )
            settled_count = cur.rowcount
            logger.info("settlement_status を 'settled' に更新: %d 件", settled_count)

        conn.commit()
        logger.info("settlement_status の更新完了")
    except Exception:
        conn.rollback()
        logger.exception("settlement_status の更新中にエラーが発生")
        raise
    finally:
        conn.close()
        logger.debug("DB 接続クローズ")


def collect_logs_from_s3(s3, target_dates):
    """S3 から対象日付のログを取得し、全行を結合して返す"""
    all_lines = []
    total_files = 0

    for target_date in target_dates:
        date_str = target_date.strftime("%Y%m%d")
        search_prefix = f"{prefix}{date_str}"
        logger.info("--- 日付: %s の処理を開始 (prefix=%s) ---", date_str, search_prefix)

        day_file_count = 0
        continuation_token = None

        while True:
            list_kwargs = {"Bucket": bucket, "Prefix": search_prefix}
            if continuation_token:
                list_kwargs["ContinuationToken"] = continuation_token

            logger.debug("list_objects_v2 呼び出し: %s", list_kwargs)
            resp = s3.list_objects_v2(**list_kwargs)
            logger.debug(
                "list_objects_v2 レスポンス: KeyCount=%s, IsTruncated=%s",
                resp.get("KeyCount", 0),
                resp.get("IsTruncated", False),
            )

            contents = resp.get("Contents", [])
            if not contents:
                logger.debug("該当オブジェクトなし")

            for obj in contents:
                key = obj["Key"]
                size = obj["Size"]
                day_file_count += 1
                total_files += 1
                logger.info("[ファイル %d] 取得開始: %s (size=%d bytes)", day_file_count, key, size)

                try:
                    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as f:
                        raw = f.read().decode("utf-8")
                    lines = raw.splitlines()
                    all_lines.extend(lines)

                    logger.info("[ファイル %d] 処理完了: %s (%d 行)", day_file_count, key, len(lines))

                except gzip.BadGzipFile:
                    logger.error("gzip 展開失敗 (不正なフォーマット): %s", key)
                except Exception:
                    logger.exception("予期しないエラー: %s", key)

            if resp.get("IsTruncated"):
                continuation_token = resp["NextContinuationToken"]
            else:
                break

        logger.info("日付 %s 完了: %d ファイル処理", date_str, day_file_count)

    logger.info("S3 ログ取得完了: 合計 %d ファイル / %d 行", total_files, len(all_lines))
    return all_lines


def parse_gateway_responses(log_lines):
    """ログ行から gatewayResponse の trackingId → statusCode マッピングを構築

    ログ形式:
      各行は JSON オブジェクト。textPayload 内に埋め込み JSON があり、
      logType="gatewayResponse" のエントリから trackingId と statusCode を抽出する。

    Returns:
        dict: {trackingId: statusCode} のマッピング
    """
    # textPayload 内の埋め込み JSON を抽出する正規表現
    # ログプレフィックス（日時 [スレッド] INFO クラス名 - [MDC:] ）の後に JSON がある
    json_pattern = re.compile(r'\{[^{]*"logType"[^}]*\}')

    tracking_status_map = {}
    parse_errors = 0

    for line in log_lines:
        try:
            # 外側の JSON をパース
            outer = json.loads(line)
            text_payload = outer.get("textPayload", "")

            if '"gatewayResponse"' not in text_payload:
                continue

            # textPayload 内の埋め込み JSON を抽出
            match = json_pattern.search(text_payload)
            if not match:
                continue

            inner = json.loads(match.group())

            if inner.get("logType") != "gatewayResponse":
                continue

            tracking_id = inner.get("trackingId")
            status_code = inner.get("statusCode")

            if tracking_id and status_code:
                tracking_status_map[tracking_id] = str(status_code)
                logger.debug(
                    "gatewayResponse 検出: trackingId=%s, statusCode=%s",
                    tracking_id, status_code
                )

        except (json.JSONDecodeError, TypeError):
            parse_errors += 1
            continue

    if parse_errors > 0:
        logger.warning("JSON パースエラー: %d 行", parse_errors)

    logger.info(
        "gatewayResponse パース完了: %d 件の trackingId → statusCode マッピングを取得",
        len(tracking_status_map)
    )
    return tracking_status_map


def check_tracking_ids_in_logs(tracking_ids, log_lines):
    """各 tracking_id のログ照合を行い、statusCode マッピングを返す

    Returns:
        tuple: (found_map, not_found)
            - found_map: dict {tracking_id: statusCode} ログで見つかった tracking_id と statusCode
            - not_found: set ログで見つからなかった tracking_id
    """
    logger.info("=== tracking_id 照合開始 (%d 件) ===", len(tracking_ids))

    # ログから gatewayResponse の trackingId → statusCode マッピングを構築
    tracking_status_map = parse_gateway_responses(log_lines)

    found_map = {}
    not_found = set()

    for tid in tracking_ids:
        if tid in tracking_status_map:
            found_map[tid] = tracking_status_map[tid]
        else:
            not_found.add(tid)

    logger.info(
        "照合結果: ログに存在=%d 件, 存在しない=%d 件",
        len(found_map), len(not_found)
    )

    if found_map:
        # statusCode ごとの件数をログ出力
        status_counts = {}
        for status_code in found_map.values():
            status_counts[status_code] = status_counts.get(status_code, 0) + 1
        for code, count in sorted(status_counts.items()):
            logger.info("  statusCode=%s: %d 件", code, count)

    if not_found:
        logger.warning("=== ログに存在しない tracking_id (%d 件) ===", len(not_found))
        for tid in sorted(not_found):
            logger.warning("  未検出: %s", tid)
    else:
        logger.info("全ての tracking_id がログに存在します")

    return found_map, not_found


def get_np_headers():
    """NP掛け払いAPI用のヘッダーを取得"""
    from urllib.parse import urlparse
    host = urlparse(NP_BASE_URL).hostname or ""
    credentials = f"{NP_SHOP_CODE}:{NP_SP_CODE}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    return {
        "Host": host,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Basic {encoded}",
        "X-NP-Terminal-Id": NP_TERMINAL_ID,
        "X-HTTP-Method-Override": "POST",
    }


def cancel_np_transactions(np_transaction_ids):
    """NP掛け払い取引キャンセルAPI呼び出し"""
    if not NP_BASE_URL or not np_transaction_ids:
        return None

    headers = get_np_headers()
    payload = {
        "requests": np_transaction_ids
    }

    try:
        resp = requests.post(
            f"{NP_BASE_URL}/v1/transactions/cancel",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.error("NP取引キャンセルAPIエラーレスポンス: status=%s, body=%s, payload=%s",
                         resp.status_code, resp.text, payload)
        resp.raise_for_status()
        result = resp.json()
        logger.info("NP取引キャンセル完了: %d 件", len(np_transaction_ids))
        return result
    except Exception:
        logger.exception("NP取引キャンセルAPIエラー: %s", np_transaction_ids)
        return None


def request_np_billing(np_transaction_records):
    """NP掛け払い請求確定依頼API呼び出し

    Args:
        np_transaction_records: list of (np_transaction_id, created_at) tuples
    """
    if not NP_BASE_URL or not np_transaction_records:
        return None

    headers = get_np_headers()
    payload = {
        "requests": [
            {
                "npTransactionId": np_txn_id,
                "billIssueBaseDate": created_at.strftime("%Y-%m-%d"),
            }
            for np_txn_id, created_at in np_transaction_records
        ]
    }

    try:
        resp = requests.post(
            f"{NP_BASE_URL}/v1/bills/request",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            logger.error("NP請求確定依頼APIエラーレスポンス: status=%s, body=%s, payload=%s",
                         resp.status_code, resp.text, payload)
        resp.raise_for_status()
        result = resp.json()
        logger.info("NP請求確定依頼完了: %d 件", len(np_transaction_records))
        return result
    except Exception:
        logger.exception("NP請求確定依頼APIエラー: %s", np_transaction_records)
        return None


def get_stale_pre_registered_transactions():
    """tracking_idがNULLのまま24時間経過したレコードを取得
    （exchange_statusが両方completedでないもの）"""
    logger.info("tracking_id=NULL の未完了レコードを取得中")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT external_transaction_id
                FROM transactions
                WHERE tracking_id IS NULL
                  AND external_transaction_id IS NOT NULL
                  AND created_at < NOW() - INTERVAL '24 hours'
                  AND NOT (consumer_exchange_status = 'completed'
                           AND provider_exchange_status = 'completed')
                """,
            )
            rows = cur.fetchall()
            np_transaction_ids = [row[0] for row in rows if row[0]]
            logger.info(
                "tracking_id=NULL の未完了レコード: %d 件", len(np_transaction_ids)
            )
            return np_transaction_ids
    finally:
        conn.close()
        logger.debug("DB 接続クローズ")


def process_billing_and_cancellation():
    """l2_http_statusの更新後、請求確定/キャンセルを実行"""
    logger.info("=== 請求確定/キャンセル処理 開始 ===")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 全transactionレコードのステータスサマリーをログ出力（デバッグ用）
            cur.execute(
                """
                SELECT external_transaction_id, tracking_id,
                       l2_http_status, consumer_exchange_status, provider_exchange_status,
                       request_date, created_at
                FROM transactions
                WHERE external_transaction_id IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 50
                """,
            )
            all_records = cur.fetchall()
            logger.info("=== transactionレコード ステータス一覧 (最新50件) ===")
            for rec in all_records:
                logger.info(
                    "  ext_txn_id=%s, tracking_id=%s, l2=%s, consumer=%s, provider=%s, request_date=%s, created=%s",
                    rec[0], rec[1], rec[2], rec[3], rec[4], rec[5], rec[6],
                )

            # 1. 請求確定対象: l2_http_status=200 かつ 両方completed
            cur.execute(
                """
                SELECT DISTINCT external_transaction_id, created_at
                FROM transactions
                WHERE l2_http_status = '200'
                  AND consumer_exchange_status = 'completed'
                  AND provider_exchange_status = 'completed'
                  AND external_transaction_id IS NOT NULL
                  AND request_date IS NULL
                """,
            )
            billing_records = [(row[0], row[1]) for row in cur.fetchall() if row[0]]

            if billing_records:
                logger.info("請求確定依頼対象: %d 件", len(billing_records))
                for ext_id, created in billing_records:
                    logger.info("  請求確定対象: ext_txn_id=%s, created=%s", ext_id, created)
                result = request_np_billing(billing_records)
                if result is not None:
                    # 請求確定成功後、request_dateを更新して再請求を防止
                    billed_ids = [rec[0] for rec in billing_records]
                    cur.execute(
                        """
                        UPDATE transactions
                        SET request_date = NOW(), updated_at = NOW()
                        WHERE external_transaction_id = ANY(%s)
                          AND request_date IS NULL
                        """,
                        (billed_ids,),
                    )
                    conn.commit()
                    logger.info("request_date 更新完了: %d 件", cur.rowcount)
            else:
                logger.info("請求確定依頼対象: なし")

            # 2. キャンセル対象: l2_http_status が 200以外（かつpending以外）
            cur.execute(
                """
                SELECT DISTINCT external_transaction_id
                FROM transactions
                WHERE l2_http_status != 'pending'
                  AND l2_http_status != '200'
                  AND external_transaction_id IS NOT NULL
                """,
            )
            cancel_ids = [row[0] for row in cur.fetchall() if row[0]]

            if cancel_ids:
                logger.info("取引キャンセル対象（L2ステータス異常）: %d 件", len(cancel_ids))
                for cid in cancel_ids:
                    logger.info("  キャンセル対象: ext_txn_id=%s", cid)
                cancel_np_transactions(cancel_ids)
                # キャンセル済みのsettlement_statusを更新
                cur.execute(
                    """
                    UPDATE transactions
                    SET settlement_status = 'cancelled', updated_at = NOW()
                    WHERE external_transaction_id = ANY(%s)
                      AND settlement_status = 'unsettled'
                    """,
                    (cancel_ids,),
                )
                conn.commit()
                logger.info("settlement_status を 'cancelled' に更新（L2異常）: %d 件", cur.rowcount)
            else:
                logger.info("取引キャンセル対象（L2ステータス異常）: なし")

    finally:
        conn.close()
        logger.debug("DB 接続クローズ")

    # 3. キャンセル対象: tracking_id=NULLで24時間経過かつexchange未完了
    stale_ids = get_stale_pre_registered_transactions()
    if stale_ids:
        logger.info("取引キャンセル対象（24時間経過・未完了）: %d 件", len(stale_ids))
        for sid in stale_ids:
            logger.info("  キャンセル対象（stale）: ext_txn_id=%s", sid)
        cancel_np_transactions(stale_ids)
        # キャンセル済みのsettlement_statusを更新
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE transactions
                    SET settlement_status = 'cancelled', updated_at = NOW()
                    WHERE external_transaction_id = ANY(%s)
                      AND settlement_status = 'unsettled'
                    """,
                    (stale_ids,),
                )
            conn.commit()
            logger.info("settlement_status を 'cancelled' に更新（24時間経過）: %d 件", cur.rowcount)
        except Exception:
            conn.rollback()
            logger.exception("settlement_status の更新中にエラーが発生（stale）")
            raise
        finally:
            conn.close()
            logger.debug("DB 接続クローズ")
    else:
        logger.info("取引キャンセル対象（24時間経過・未完了）: なし")

    logger.info("=== 請求確定/キャンセル処理 完了 ===")


def main():
    logger.info("=== CronJob 開始 ===")
    logger.info("対象バケット: %s / プレフィックス: %s", bucket, prefix)
    logger.info("対象日付: %s, %s (JST)", yesterday, today)

    # 1. DB から tracking_id を取得
    tracking_ids = get_tracking_ids(yesterday, today)
    if not tracking_ids:
        logger.info("対象の tracking_id がありません。L2ログチェックをスキップします。")
    else:
        # 2. S3 からログを取得
        s3 = boto3.client("s3")
        log_lines = collect_logs_from_s3(s3, target_dates)

        # 3. tracking_id の照合（gatewayResponse から statusCode を抽出）
        found_map, not_found = check_tracking_ids_in_logs(tracking_ids, log_lines)

        # 4. DB の l2_http_status を更新（実際の statusCode を登録）
        update_l2_http_status(found_map, not_found)

        logger.info("  tracking_id 総数: %d", len(tracking_ids))
        logger.info("  ログに存在:       %d", len(found_map))
        logger.info("  ログに未検出:     %d", len(not_found))

    # 5. settlement_status を更新（確定条件を満たすレコードを 'settled' に）
    update_settlement_status()

    # 6. 請求確定/キャンセル処理
    process_billing_and_cancellation()


if __name__ == "__main__":
    main()