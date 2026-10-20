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

# 多重起動防止用の advisory lock キー（本 CronJob 固有の固定値）
CRON_ADVISORY_LOCK_KEY = 7_320_001


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


def acquire_run_lock():
    """CronJob の多重起動を防ぐ PostgreSQL advisory lock を取得する

    同時に実行されると NP 請求確定依頼・キャンセル依頼が重複するため、
    CronJob の concurrencyPolicy: Forbid に加えて DB でも排他する。
    ロックはセッション単位のため、返した接続を閉じるまで保持される。

    Returns:
        ロックを保持した接続。他の実行がロック中の場合は None。
    """
    conn = get_db_connection()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (CRON_ADVISORY_LOCK_KEY,))
        acquired = cur.fetchone()[0]
    if not acquired:
        conn.close()
        return None
    return conn


def get_pending_tracking_ids(today, lookback_days):
    """L2 ログ突合が必要な tracking_id を取得する

    対象: l2_http_status='pending' かつ tracking_id あり かつ 未精算（unsettled） かつ
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
                  -- キャンセル済みの行は照合不要（S3 ログの読込範囲を広げないため除く）
                  AND settlement_status = 'unsettled'
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
                  AND settlement_status = 'unsettled'
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
    """S3 ログを読む日付の一覧（最古の取引日の前日〜当日）

    L2 のゲートウェイログはデータ交換の処理時に出力されるため、完了日時より前の日付のファイルに
    入ることがある（23:59 に処理し翌 0:10 に完了した場合など）。取引日の前日のファイルから読む。
    """
    start = (oldest_date if oldest_date and oldest_date < today else today) - timedelta(days=1)
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
    """S3 から対象日付のログを取得し、展開しながら 1 行ずつ返す（ジェネレータ）

    遡及期間分のログをメモリに載せないよう、S3 のストリームを展開しながら行を返し、
    次のファイルは前のファイルの行が処理されてから取得する。
    """
    total_files = 0
    total_lines = 0

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

                file_lines = 0
                body = None
                try:
                    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
                    with gzip.GzipFile(fileobj=body) as gz, io.TextIOWrapper(gz, encoding="utf-8") as f:
                        for line in f:
                            file_lines += 1
                            yield line.rstrip("\r\n")

                    logger.info("[ファイル %d] 処理完了: %s (%d 行)", day_file_count, key, file_lines)

                except gzip.BadGzipFile:
                    logger.error("gzip 展開失敗 (不正なフォーマット): %s", key)
                except Exception:
                    # 途中で壊れているファイルは、それまでに読めた行だけを照合に使う
                    logger.exception("予期しないエラー: %s", key)
                finally:
                    if body is not None:
                        body.close()
                total_lines += file_lines

            if resp.get("IsTruncated"):
                continuation_token = resp["NextContinuationToken"]
            else:
                break

        logger.info("日付 %s 完了: %d ファイル処理", date_str, day_file_count)

    logger.info("S3 ログ取得完了: 合計 %d ファイル / %d 行", total_files, total_lines)


def parse_gateway_responses(log_lines, tracking_ids=None):
    """ログ行から gatewayResponse の trackingId → statusCode マッピングを構築

    ログ形式:
      各行は JSON オブジェクト。textPayload 内に埋め込み JSON があり、
      logType="gatewayResponse" のエントリから trackingId と statusCode を抽出する。

    Args:
        tracking_ids: 指定した場合、これらの trackingId のエントリだけを残す
            （遡及期間分のログ全体のマッピングをメモリに保持しないため）

    Returns:
        dict: {trackingId（小文字）: statusCode} のマッピング
    """
    wanted = None if tracking_ids is None else {str(t).lower() for t in tracking_ids}
    # textPayload 内の埋め込み JSON を抽出する正規表現
    # ログプレフィックス（日時 [スレッド] INFO クラス名 - [MDC:] ）の後に JSON がある
    json_pattern = re.compile(r'\{[^{]*"logType"[^}]*\}')

    tracking_status_map = {}
    parse_errors = 0

    for line in log_lines:
        try:
            # 外側の JSON をパース
            outer = json.loads(line)
            if not isinstance(outer, dict):
                # null・配列・数値など JSON オブジェクト以外の行は不正行として読み飛ばす
                parse_errors += 1
                continue
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
                # UUID の大文字小文字の違いで照合漏れにならないよう小文字にそろえる
                # （DB の tracking_id は小文字の正規形）
                tracking_id = str(tracking_id).lower()
                if wanted is not None and tracking_id not in wanted:
                    continue
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

    # ログから gatewayResponse の trackingId → statusCode マッピングを構築（照合対象の分だけ）
    tracking_status_map = parse_gateway_responses(log_lines, tracking_ids)

    found_map = {}
    not_found = set()

    for tid in tracking_ids:
        status_code = tracking_status_map.get(str(tid).lower())
        if status_code is not None:
            found_map[tid] = status_code
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


NP_OK = "ok"
NP_REJECTED = "rejected"   # 4xx または本文に errors（リクエスト内容が受け付けられなかった）
NP_ERROR = "error"         # 5xx・通信エラー（NP 側の障害。次回実行で再試行する）


def _post_np(path, payload, label):
    """NP掛け払いAPIを呼び出し、結果を NP_OK / NP_REJECTED / NP_ERROR で返す"""
    try:
        resp = requests.post(f"{NP_BASE_URL}{path}", headers=get_np_headers(), json=payload, timeout=30)
    except Exception:
        logger.exception("%sAPIエラー（通信）: payload=%s", label, payload)
        return NP_ERROR
    if resp.status_code >= 500:
        logger.error("%sAPIエラーレスポンス: status=%s, body=%s, payload=%s",
                     label, resp.status_code, resp.text, payload)
        return NP_ERROR
    if resp.status_code >= 400:
        logger.error("%sAPIエラーレスポンス: status=%s, body=%s, payload=%s",
                     label, resp.status_code, resp.text, payload)
        return NP_REJECTED
    try:
        body = resp.json()
    except ValueError:
        body = None
    if _np_response_has_errors(body):
        logger.error("%sAPIが errors を返却: body=%s, payload=%s", label, body, payload)
        return NP_REJECTED
    return NP_OK


def _call_np_isolating(path, items, make_payload, label):
    """NP掛け払いAPIを一括で呼び出し、受け付けられた項目のリストを返す

    一括リクエストが拒否された場合（本文に errors など）、どの項目が原因か特定できないため
    1件ずつ呼び直して切り分ける。問題のある項目が1件あるだけで他の項目まで毎回失敗し
    続けないようにするため。1件ずつでも拒否された項目は ERROR ログを出し（要確認）、
    次回実行で再試行する。5xx・通信エラーの場合は NP 側の障害とみなし、残りは次回実行で再試行する。
    """
    if not items:
        return []
    if not NP_BASE_URL:
        logger.warning("NP_KAKEBARAI_BASE_URL が未設定のため%sを実行しません: %d 件", label, len(items))
        return []

    result = _post_np(path, make_payload(items), label)
    if result == NP_OK:
        logger.info("%s完了: %d 件", label, len(items))
        return list(items)
    if result == NP_ERROR:
        return []
    if len(items) == 1:
        logger.error("%sが拒否された（要確認、次回実行で再試行）: %s", label, items[0])
        return []

    logger.warning("%sの一括依頼が拒否されたため1件ずつ依頼し直します: %d 件", label, len(items))
    accepted = []
    for item in items:
        result = _post_np(path, make_payload([item]), label)
        if result == NP_OK:
            accepted.append(item)
        elif result == NP_REJECTED:
            logger.error("%sが拒否された（要確認、次回実行で再試行）: %s", label, item)
        else:
            break
    logger.info("%s完了: %d 件 / 対象 %d 件", label, len(accepted), len(items))
    return accepted


def cancel_np_transactions(np_transaction_ids):
    """NP掛け払い取引キャンセルAPI呼び出し

    Returns:
        list: キャンセルに成功した npTransactionId のリスト。
              呼び出し側は成功した ID のみローカルを 'cancelled' に更新すること。
    """
    return _call_np_isolating(
        "/v1/transactions/cancel",
        list(np_transaction_ids),
        lambda ids: {"requests": ids},
        "NP取引キャンセル",
    )


def request_np_billing(np_transaction_records):
    """NP掛け払い請求確定依頼API呼び出し

    Args:
        np_transaction_records: list of (np_transaction_id, bill_issue_base_date) tuples
            bill_issue_base_date はデータ交換完了日（JST の日付。未設定の既存行は created_at）。

    Returns:
        list: 請求確定に成功した (np_transaction_id, bill_issue_base_date) のリスト。
    """
    return _call_np_isolating(
        "/v1/bills/request",
        list(np_transaction_records),
        lambda records: {
            "requests": [
                {"npTransactionId": np_txn_id, "billIssueBaseDate": bill_date.strftime("%Y-%m-%d")}
                for np_txn_id, bill_date in records
            ]
        },
        "NP請求確定依頼",
    )


# キャンセル対象の共通条件: NP 取引あり・未精算・未請求。
# 請求確定済み（request_date あり）の取引は NP 側で請求済みのためキャンセルしない（要確認として通知する）。
CANCELLABLE_SQL = """
    external_transaction_id IS NOT NULL
    AND settlement_status = 'unsettled'
    AND request_date IS NULL
"""

# 事前登録のまま24時間経過（両当事者ともデータ交換を報告していない）
STALE_PRE_REGISTERED_SQL = """
    consumer_exchange_status = 'pending'
    AND provider_exchange_status = 'pending'
    AND created_at < NOW() - INTERVAL '24 hours'
"""

# L2 ログが 2xx 以外（timeout 含む）
L2_FAILED_SQL = f"l2_http_status != 'pending' AND NOT {L2_SUCCESS_SQL}"

# 当事者の報告が揃わず確定できない取引
#   - 一方の当事者でも failed を報告した（failed → completed には戻せないため確定不能）
#   - 一方が completed、他方が pending のまま遡及期間を超えた
# L2 ログが 2xx 以外のものは L2_FAILED_SQL で扱う
UNMATCHED_REPORT_SQL = f"""
    (l2_http_status = 'pending' OR {L2_SUCCESS_SQL})
    AND (
          consumer_exchange_status = 'failed'
       OR provider_exchange_status = 'failed'
       OR (NOT (consumer_exchange_status = 'pending' AND provider_exchange_status = 'pending')
           AND NOT (consumer_exchange_status = 'completed' AND provider_exchange_status = 'completed')
           AND COALESCE(completed_at, created_at) < %s)
    )
"""


def cancel_and_mark(condition_sql, params, reason):
    """条件に当たる取引の NP 取引をキャンセルし、成功した取引のみ 'cancelled' に更新する

    対象行を SELECT ... FOR UPDATE SKIP LOCKED でロックしたまま NP キャンセルと更新を行う。
    判定から更新までの間に状態登録・更新 API が同じ行を変更しないようにするため
    （API 側は行ロックを待ち、キャンセル後の状態で判定する）。API が処理中でロックされている
    行は今回は対象外とし、次回実行で再判定する。
    失敗分は 'unsettled' のまま残し次回 cron で再試行する。
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT transaction_id, external_transaction_id
                FROM transactions
                WHERE {CANCELLABLE_SQL}
                  AND ({condition_sql})
                ORDER BY transaction_id
                FOR UPDATE SKIP LOCKED
                """,
                params,
            )
            rows = cur.fetchall()
            np_transaction_ids = sorted({row[1] for row in rows})
            if not np_transaction_ids:
                logger.info("取引キャンセル対象（%s）: なし", reason)
                conn.rollback()
                return
            logger.info("取引キャンセル対象（%s）: %d 件", reason, len(np_transaction_ids))
            for nid in np_transaction_ids:
                logger.info("  キャンセル対象（%s）: ext_txn_id=%s", reason, nid)

            cancelled_ids = set(cancel_np_transactions(np_transaction_ids))
            updated = mark_cancelled(cur, [row[0] for row in rows if row[1] in cancelled_ids])
        conn.commit()
        logger.info(
            "settlement_status を 'cancelled' に更新（%s）: %d 件 (キャンセル失敗 %d 件)",
            reason, updated, len(np_transaction_ids) - len(cancelled_ids),
        )
    except Exception:
        conn.rollback()
        logger.exception("取引キャンセル処理中にエラーが発生（%s）", reason)
        raise
    finally:
        conn.close()
        logger.debug("DB 接続クローズ")


def mark_cancelled(cur, transaction_ids):
    """NP側でキャンセル済みの取引の settlement_status を 'cancelled' に更新する"""
    if not transaction_ids:
        return 0
    cur.execute(
        """
        UPDATE transactions
        SET settlement_status = 'cancelled', updated_at = NOW()
        WHERE transaction_id = ANY(%s::uuid[])
          AND settlement_status = 'unsettled'
        """,
        ([str(t) for t in transaction_ids],),
    )
    return cur.rowcount


def warn_billed_but_unconfirmed():
    """請求確定済みなのに L2 ログで成功を確認できない取引を通知する（自動キャンセルしない）

    利用料モデルなしの取引金額確定 API は L2 ログ突合の前に請求確定するため、後から
    L2 ログが 2xx 以外（timeout 含む）となる場合がある。NP 側で請求済みのため手動で確認する。
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT external_transaction_id, tracking_id, l2_http_status
                FROM transactions
                WHERE external_transaction_id IS NOT NULL
                  AND settlement_status = 'unsettled'
                  AND request_date IS NOT NULL
                  AND {L2_FAILED_SQL}
                """,
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    for ext_id, tracking_id, l2 in rows:
        logger.error(
            "請求確定済みだが L2 ログで成功を確認できない取引（要確認・manual recovery required）: "
            "ext_txn_id=%s, tracking_id=%s, l2=%s",
            ext_id, tracking_id, l2,
        )
    return len(rows)


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
            #    請求書発行基準日はデータ交換完了日（JST の日付）
            #    対象行は行ロックを取ったまま請求確定依頼と request_date の更新を行う。その間に
            #    状態更新 API が failed などへ変更すると、NP では請求済みなのに交換失敗となるため
            #    （API はロックを待ち、請求確定後の状態で判定する）。API が処理中でロックされている
            #    行は今回は対象外とし、次回実行で処理する。
            cur.execute(
                f"""
                SELECT transaction_id, external_transaction_id,
                       (COALESCE(completed_at, created_at) AT TIME ZONE 'Asia/Tokyo')::date AS bill_date
                FROM transactions
                WHERE {L2_SUCCESS_SQL}
                  AND consumer_exchange_status = 'completed'
                  AND provider_exchange_status = 'completed'
                  AND external_transaction_id IS NOT NULL
                  AND request_date IS NULL
                  AND settlement_status != 'cancelled'
                ORDER BY transaction_id
                FOR UPDATE SKIP LOCKED
                """,
            )
            rows = cur.fetchall()
            # 依頼順（1 件ずつの切り分け・ログの順序）を実行ごとに変えないよう ID 順にする。
            # 空文字の NP 取引ID は依頼しない（NP に一括依頼ごと拒否される）
            bill_records = sorted({(row[1], row[2]) for row in rows if row[1]})

            if bill_records:
                logger.info("請求確定依頼対象: %d 件", len(bill_records))
                for ext_id, bill_date in bill_records:
                    logger.info("  請求確定対象: ext_txn_id=%s, bill_issue_base_date=%s", ext_id, bill_date)
                billed = request_np_billing(bill_records)
                if billed:
                    # 請求確定に成功した取引のみ request_date を更新して再請求を防止
                    billed_ids = {rec[0] for rec in billed}
                    cur.execute(
                        """
                        UPDATE transactions
                        SET request_date = NOW(), updated_at = NOW()
                        WHERE transaction_id = ANY(%s::uuid[])
                          AND request_date IS NULL
                        """,
                        ([str(row[0]) for row in rows if row[1] in billed_ids],),
                    )
                    logger.info("request_date 更新完了: %d 件", cur.rowcount)
            else:
                logger.info("請求確定依頼対象: なし")
            # 行ロックを解放する
            conn.commit()

    finally:
        conn.close()
        logger.debug("DB 接続クローズ")

    # 2. キャンセル対象: L2 ログが 2xx 以外（timeout 含む）
    cancel_and_mark(L2_FAILED_SQL, None, "L2ステータス異常")

    # 3. キャンセル対象: 事前登録のまま24時間経過（両当事者とも未報告）
    cancel_and_mark(STALE_PRE_REGISTERED_SQL, None, "24時間経過・未完了")

    # 4. キャンセル対象: 当事者の一方が failed、または片方未報告のまま遡及期間を超過
    lookback_start = datetime.combine(today - timedelta(days=lookback_days), datetime.min.time(), tzinfo=JST)
    cancel_and_mark(UNMATCHED_REPORT_SQL, (lookback_start,), "当事者報告の不一致・未報告")

    # 5. 請求確定済みだが L2 ログで成功を確認できない取引を通知
    warn_billed_but_unconfirmed()

    logger.info("=== 請求確定/キャンセル処理 完了 ===")


def main():
    lock_conn = acquire_run_lock()
    if lock_conn is None:
        logger.warning("別の CronJob が実行中のため、今回の実行はスキップします")
        return
    try:
        run(jst_today())
    finally:
        lock_conn.close()


def run(today):
    logger.info("=== CronJob 開始 ===")
    logger.info("対象バケット: %s / プレフィックス: %s", bucket, prefix)
    logger.info("当日: %s (JST) / L2ログ遡及日数: %d", today, L2_LOG_LOOKBACK_DAYS)

    # 1. DB から未照合の tracking_id を取得（遡及期間内の pending 行すべて）
    tracking_ids, oldest_date = get_pending_tracking_ids(today, L2_LOG_LOOKBACK_DAYS)
    if not tracking_ids:
        logger.info("対象の tracking_id がありません。L2ログチェックをスキップします。")
    else:
        # 2. S3 からログを取得（最古の取引日の前日〜当日）
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
