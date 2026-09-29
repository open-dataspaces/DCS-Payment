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

# L2 ログ突合の最大遡及日数。
# 未検出のまま（l2_http_status='pending'）この日数を超えた取引は 'timeout' とみなし、
# NP取引キャンセルの対象にする（未請求・未キャンセルのまま残さない）
L2_LOG_LOOKBACK_DAYS = int(os.environ.get("L2_LOG_LOOKBACK_DAYS", "7"))

# JST (UTC+9)
JST = timezone(timedelta(hours=9))

L2_STATUS_TIMEOUT = "timeout"

# L2 ログの statusCode が 2xx のものをデータ交換成功とみなす（200 に限定しない）。
# アプリ側（Transaction.l2_succeeded()）と同じ判定にすること。
L2_SUCCESS_SQL = "l2_http_status ~ '^2[0-9]{2}$'"


def jst_today():
    return datetime.now(JST).date()


def get_db_connection():
    """DB接続を取得"""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def get_pending_tracking_ids(today, lookback_days):
    """L2 ログ突合が必要な tracking_id を取得する

    対象: l2_http_status='pending' かつ tracking_id あり かつ
          少なくとも一方の当事者がデータ交換を報告済み かつ
          データ交換完了日時（completed_at、無ければ created_at）が遡及期間内
    作成日で「前日〜当日」に限定すると、2日を超えて未検出の行が永久に pending のまま
    残るため、遡及期間内の pending 行はすべて毎回照合する。

    Returns:
        tuple: (tracking_ids: set, oldest_date: date or None)
    """
    lookback_start = today - timedelta(days=lookback_days)
    logger.info("DB から未照合の tracking_id を取得中 (遡及 %d 日: %s 〜)", lookback_days, lookback_start)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tracking_id,
                       MIN((COALESCE(completed_at, created_at) AT TIME ZONE 'Asia/Tokyo')::date)
                FROM transactions
                WHERE tracking_id IS NOT NULL
                  AND l2_http_status = 'pending'
                  -- 取引可否APIで事前登録しただけ（両当事者とも未報告）の行は
                  -- データ交換が未実施のため L2 ログ突合の対象外
                  AND NOT (consumer_exchange_status = 'pending'
                           AND provider_exchange_status = 'pending')
                  AND COALESCE(completed_at, created_at) >= %s
                GROUP BY tracking_id
                """,
                (datetime.combine(lookback_start, datetime.min.time(), tzinfo=JST),),
            )
            rows = cur.fetchall()
            tracking_ids = {row[0] for row in rows}
            oldest = min((row[1] for row in rows), default=None)
            logger.info("DB から %d 件の tracking_id を取得 (最古: %s)", len(tracking_ids), oldest)
            return tracking_ids, oldest
    finally:
        conn.close()
        logger.debug("DB 接続クローズ")


def mark_timed_out_transactions(today, lookback_days):
    """遡及期間を超えても L2 ログで未検出の取引を 'timeout' に更新する

    'timeout' は 2xx 以外のステータスとして扱われ、後続のキャンセル処理で
    NP取引がキャンセルされ settlement_status='cancelled' になる。
    """
    lookback_start = today - timedelta(days=lookback_days)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE transactions
                SET l2_http_status = %s, updated_at = NOW()
                WHERE tracking_id IS NOT NULL
                  AND l2_http_status = 'pending'
                  AND NOT (consumer_exchange_status = 'pending'
                           AND provider_exchange_status = 'pending')
                  AND COALESCE(completed_at, created_at) < %s
                """,
                (L2_STATUS_TIMEOUT, datetime.combine(lookback_start, datetime.min.time(), tzinfo=JST)),
            )
            count = cur.rowcount
        conn.commit()
        if count:
            logger.warning(
                "L2 ログ未検出のまま %d 日を超えた取引を '%s' に更新: %d 件（後続でNP取引キャンセル）",
                lookback_days, L2_STATUS_TIMEOUT, count,
            )
        else:
            logger.info("L2 ログ未検出タイムアウト対象: なし")
        return count
    except Exception:
        conn.rollback()
        logger.exception("timeout 更新中にエラーが発生")
        raise
    finally:
        conn.close()
        logger.debug("DB 接続クローズ")


def build_target_dates(oldest_date, today):
    """S3 ログを読む日付の一覧（最古の取引日〜当日）"""
    start = oldest_date if oldest_date and oldest_date < today else today - timedelta(days=1)
    return [start + timedelta(days=i) for i in range((today - start).days + 1)]


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

    確定条件（l2_http_status が 2xx AND consumer_exchange_status='completed'
    AND provider_exchange_status='completed'）を満たすレコードを 'settled' に更新する。
    """
    logger.info("=== settlement_status 更新処理 開始 ===")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE transactions
                SET settlement_status = 'settled', updated_at = NOW()
                WHERE settlement_status = 'unsettled'
                  AND {L2_SUCCESS_SQL}
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


def _np_response_has_errors(body):
    """NP掛け払いAPIレスポンス本文にエラーが含まれるか"""
    return isinstance(body, dict) and bool(body.get("errors"))


def cancel_np_transactions(np_transaction_ids):
    """NP掛け払い取引キャンセルAPI呼び出し

    Returns:
        list: キャンセルに成功した npTransactionId のリスト。
              API エラー（HTTP エラー・本文に errors あり・例外）の場合は空リスト。
              呼び出し側は成功した ID のみローカルを 'cancelled' に更新すること。
    """
    if not NP_BASE_URL or not np_transaction_ids:
        return []

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
            return []
        result = resp.json()
        if _np_response_has_errors(result):
            # どの取引が失敗したか特定できないため全件失敗扱い（次回 cron で再試行）
            logger.error("NP取引キャンセルAPIが errors を返却: body=%s, payload=%s", result, payload)
            return []
        logger.info("NP取引キャンセル完了: %d 件", len(np_transaction_ids))
        return list(np_transaction_ids)
    except Exception:
        logger.exception("NP取引キャンセルAPIエラー: %s", np_transaction_ids)
        return []


def request_np_billing(np_transaction_records):
    """NP掛け払い請求確定依頼API呼び出し

    Args:
        np_transaction_records: list of (np_transaction_id, completed_at) tuples
            completed_at はデータ交換完了日時（未設定の既存行は created_at）。
            請求書発行基準日（billIssueBaseDate）として使用する。
    """
    if not NP_BASE_URL or not np_transaction_records:
        return None

    headers = get_np_headers()
    payload = {
        "requests": [
            {
                "npTransactionId": np_txn_id,
                "billIssueBaseDate": completed_at.strftime("%Y-%m-%d"),
            }
            for np_txn_id, completed_at in np_transaction_records
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
            return None
        result = resp.json()
        if _np_response_has_errors(result):
            logger.error("NP請求確定依頼APIが errors を返却: body=%s, payload=%s", result, payload)
            return None
        logger.info("NP請求確定依頼完了: %d 件", len(np_transaction_records))
        return result
    except Exception:
        logger.exception("NP請求確定依頼APIエラー: %s", np_transaction_records)
        return None


def get_stale_pre_registered_transactions():
    """取引可否APIで事前登録されたまま24時間経過したレコードの外部取引IDを取得

    事前登録行は tracking_id 付きで保存されるため、「両当事者ともデータ交換状態が
    pending のまま（=データ交換が報告されていない）」かつ未精算のものを対象にする。
    """
    logger.info("事前登録のまま24時間経過した未完了レコードを取得中")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT external_transaction_id
                FROM transactions
                WHERE external_transaction_id IS NOT NULL
                  AND settlement_status = 'unsettled'
                  AND consumer_exchange_status = 'pending'
                  AND provider_exchange_status = 'pending'
                  AND created_at < NOW() - INTERVAL '24 hours'
                """,
            )
            rows = cur.fetchall()
            np_transaction_ids = [row[0] for row in rows if row[0]]
            logger.info(
                "事前登録のまま24時間経過した未完了レコード: %d 件", len(np_transaction_ids)
            )
            return np_transaction_ids
    finally:
        conn.close()
        logger.debug("DB 接続クローズ")


def get_unmatched_report_transactions(today, lookback_days):
    """当事者の報告が揃わず確定できない未精算取引の外部取引IDを取得する

    L2 ログ・提供者・利用者の 3 つがすべて成功の場合のみ確定（settled）とするため、
    次の取引は確定できない。請求せずに NP 取引をキャンセルする。
      - 一方の当事者でも failed を報告した取引（failed → completed には戻せないため確定不能）
      - 一方の当事者が completed、他方が pending のまま遡及期間を超えた取引
    L2 ステータスが 2xx 以外（timeout 含む）の取引は「L2ステータス異常」でキャンセルするため除く。
    """
    lookback_start = today - timedelta(days=lookback_days)
    logger.info("当事者報告が不一致・未報告の未精算レコードを取得中")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT external_transaction_id
                FROM transactions
                WHERE external_transaction_id IS NOT NULL
                  AND settlement_status = 'unsettled'
                  AND request_date IS NULL
                  AND (l2_http_status = 'pending' OR {L2_SUCCESS_SQL})
                  AND (
                        consumer_exchange_status = 'failed'
                     OR provider_exchange_status = 'failed'
                     OR (NOT (consumer_exchange_status = 'pending'
                              AND provider_exchange_status = 'pending')
                         AND NOT (consumer_exchange_status = 'completed'
                                  AND provider_exchange_status = 'completed')
                         AND COALESCE(completed_at, created_at) < %s)
                  )
                """,
                (datetime.combine(lookback_start, datetime.min.time(), tzinfo=JST),),
            )
            rows = cur.fetchall()
            np_transaction_ids = [row[0] for row in rows if row[0]]
            logger.info("当事者報告が不一致・未報告の未精算レコード: %d 件", len(np_transaction_ids))
            return np_transaction_ids
    finally:
        conn.close()
        logger.debug("DB 接続クローズ")


def cancel_and_mark(np_transaction_ids, reason):
    """NP取引をキャンセルし、NP側でキャンセルに成功した取引のみ 'cancelled' に更新する

    失敗分は 'unsettled' のまま残し次回 cron で再試行する。
    """
    if not np_transaction_ids:
        logger.info("取引キャンセル対象（%s）: なし", reason)
        return
    logger.info("取引キャンセル対象（%s）: %d 件", reason, len(np_transaction_ids))
    for nid in np_transaction_ids:
        logger.info("  キャンセル対象（%s）: ext_txn_id=%s", reason, nid)
    cancelled_ids = cancel_np_transactions(np_transaction_ids)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            updated = mark_cancelled(cur, cancelled_ids)
        conn.commit()
        logger.info(
            "settlement_status を 'cancelled' に更新（%s）: %d 件 (キャンセル失敗 %d 件)",
            reason, updated, len(np_transaction_ids) - len(cancelled_ids),
        )
    except Exception:
        conn.rollback()
        logger.exception("settlement_status の更新中にエラーが発生（%s）", reason)
        raise
    finally:
        conn.close()
        logger.debug("DB 接続クローズ")


def mark_cancelled(cur, external_transaction_ids):
    """NP側でキャンセル済みの取引の settlement_status を 'cancelled' に更新する"""
    if not external_transaction_ids:
        return 0
    cur.execute(
        """
        UPDATE transactions
        SET settlement_status = 'cancelled', updated_at = NOW()
        WHERE external_transaction_id = ANY(%s)
          AND settlement_status = 'unsettled'
        """,
        (list(external_transaction_ids),),
    )
    return cur.rowcount


def process_billing_and_cancellation(today=None, lookback_days=None):
    """l2_http_statusの更新後、請求確定/キャンセルを実行"""
    today = today or jst_today()
    lookback_days = L2_LOG_LOOKBACK_DAYS if lookback_days is None else lookback_days
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

            # 1. 請求確定対象: l2_http_status が 2xx かつ 両方completed
            #    （キャンセル済みの取引は、後から当事者が completed を報告しても請求しない）
            cur.execute(
                f"""
                SELECT DISTINCT external_transaction_id,
                       COALESCE(completed_at, created_at) AS completed_at
                FROM transactions
                WHERE {L2_SUCCESS_SQL}
                  AND consumer_exchange_status = 'completed'
                  AND provider_exchange_status = 'completed'
                  AND external_transaction_id IS NOT NULL
                  AND request_date IS NULL
                  AND settlement_status != 'cancelled'
                """,
            )
            billing_records = [(row[0], row[1]) for row in cur.fetchall() if row[0]]

            if billing_records:
                logger.info("請求確定依頼対象: %d 件", len(billing_records))
                for ext_id, completed in billing_records:
                    logger.info("  請求確定対象: ext_txn_id=%s, completed_at=%s", ext_id, completed)
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

            # 2. キャンセル対象: l2_http_status が 2xx以外（かつpending以外）で未精算のもの
            #    （既に cancelled のものを毎回再キャンセル依頼しないよう settlement_status で絞る）
            cur.execute(
                f"""
                SELECT DISTINCT external_transaction_id
                FROM transactions
                WHERE l2_http_status != 'pending'
                  AND NOT {L2_SUCCESS_SQL}
                  AND external_transaction_id IS NOT NULL
                  AND settlement_status = 'unsettled'
                """,
            )
            cancel_ids = [row[0] for row in cur.fetchall() if row[0]]

            if cancel_ids:
                logger.info("取引キャンセル対象（L2ステータス異常）: %d 件", len(cancel_ids))
                for cid in cancel_ids:
                    logger.info("  キャンセル対象: ext_txn_id=%s", cid)
                cancelled_ids = cancel_np_transactions(cancel_ids)
                # NP側でキャンセルに成功した取引のみ settlement_status を更新
                # （失敗分は 'unsettled' のまま残し次回 cron で再試行）
                updated = mark_cancelled(cur, cancelled_ids)
                conn.commit()
                logger.info(
                    "settlement_status を 'cancelled' に更新（L2異常）: %d 件 (キャンセル失敗 %d 件)",
                    updated, len(cancel_ids) - len(cancelled_ids),
                )
            else:
                logger.info("取引キャンセル対象（L2ステータス異常）: なし")

    finally:
        conn.close()
        logger.debug("DB 接続クローズ")

    # 3. キャンセル対象: 事前登録のまま24時間経過（両当事者とも未報告）
    cancel_and_mark(get_stale_pre_registered_transactions(), "24時間経過・未完了")

    # 4. キャンセル対象: 当事者の一方が failed、または片方未報告のまま遡及期間を超過
    cancel_and_mark(get_unmatched_report_transactions(today, lookback_days), "当事者報告の不一致・未報告")

    logger.info("=== 請求確定/キャンセル処理 完了 ===")


def main():
    today = jst_today()
    logger.info("=== CronJob 開始 ===")
    logger.info("対象バケット: %s / プレフィックス: %s", bucket, prefix)
    logger.info("当日: %s (JST) / L2ログ遡及日数: %d", today, L2_LOG_LOOKBACK_DAYS)

    # 1. DB から未照合の tracking_id を取得（遡及期間内の pending 行すべて）
    tracking_ids, oldest_date = get_pending_tracking_ids(today, L2_LOG_LOOKBACK_DAYS)
    if not tracking_ids:
        logger.info("対象の tracking_id がありません。L2ログチェックをスキップします。")
    else:
        # 2. S3 からログを取得（最古の取引日〜当日）
        target_dates = build_target_dates(oldest_date, today)
        logger.info("対象日付: %s 〜 %s (%d 日分)", target_dates[0], target_dates[-1], len(target_dates))
        s3 = boto3.client("s3")
        log_lines = collect_logs_from_s3(s3, target_dates)

        # 3. tracking_id の照合（gatewayResponse から statusCode を抽出）
        found_map, not_found = check_tracking_ids_in_logs(tracking_ids, log_lines)

        # 4. DB の l2_http_status を更新（実際の statusCode を登録）
        update_l2_http_status(found_map, not_found)

        logger.info("  tracking_id 総数: %d", len(tracking_ids))
        logger.info("  ログに存在:       %d", len(found_map))
        logger.info("  ログに未検出:     %d", len(not_found))

    # 5. 遡及期間を超えて未検出の取引を 'timeout' に更新（後続でキャンセル）
    mark_timed_out_transactions(today, L2_LOG_LOOKBACK_DAYS)

    # 6. settlement_status を更新（確定条件を満たすレコードを 'settled' に）
    update_settlement_status()

    # 7. 請求確定/キャンセル処理
    process_billing_and_cancellation(today, L2_LOG_LOOKBACK_DAYS)


if __name__ == "__main__":
    main()
