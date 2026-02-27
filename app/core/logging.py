"""
ログ設定

特徴:
- 環境変数で完全制御
- Dockerコンテナに最適化（stdout/stderr中心）
- JSON形式サポート（ログ集約ツール対応）
- 構造化ログ
- リクエストIDでトレース可能
- パフォーマンス最適化
"""

import functools
import json
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Any, MutableMapping
from contextvars import ContextVar
from pathlib import Path

# エクスポート一覧
__all__ = [
    'setup_logging',
    'get_logger',
    'set_request_id',
    'get_request_id',
    'request_id_var',
    'log_execution_time',
    'ExtraAdapter',
    'LogConfig',
]

# リクエストIDを格納するContextVar（スレッドセーフ）
request_id_var: ContextVar[Optional[str]] = ContextVar('request_id', default=None)


# ============================================================================
# 環境変数による設定
# ============================================================================

class LogConfig:
    """
    ログ設定を環境変数から取得

    プロパティとして実装することで、環境変数の変更を動的に反映可能。
    テスト時に環境変数を変更しても正しく動作する。
    """

    @classmethod
    def _get_bool(cls, key: str, default: str = 'false') -> bool:
        """環境変数をboolとして取得"""
        return os.getenv(key, default).lower() in ('true', '1', 'yes')

    @classmethod
    def _get_int(cls, key: str, default: int) -> int:
        """環境変数をintとして取得"""
        try:
            return int(os.getenv(key, str(default)))
        except ValueError:
            return default

    @property
    def log_level(self) -> str:
        """ログレベル（デフォルト: INFO）"""
        return os.getenv('LOG_LEVEL', 'INFO').upper()

    @property
    def log_json_format(self) -> bool:
        """JSON形式で出力するか（デフォルト: True for production）"""
        return self._get_bool('LOG_JSON_FORMAT', 'true')

    @property
    def log_file_enabled(self) -> bool:
        """ファイル出力を有効にするか（デフォルト: False for Docker）"""
        return self._get_bool('LOG_FILE_ENABLED', 'false')

    @property
    def log_file_path(self) -> str:
        """ログファイルのパス"""
        return os.getenv('LOG_FILE_PATH', '/var/log/app/app.log')

    @property
    def error_log_file_path(self) -> str:
        """エラーログファイルのパス"""
        return os.getenv('ERROR_LOG_FILE_PATH', '/var/log/app/error.log')

    @property
    def log_file_max_bytes(self) -> int:
        """ファイルローテーション: 最大バイト数（デフォルト: 10MB）"""
        return self._get_int('LOG_FILE_MAX_BYTES', 10 * 1024 * 1024)

    @property
    def log_file_backup_count(self) -> int:
        """ファイルローテーション: バックアップ数"""
        return self._get_int('LOG_FILE_BACKUP_COUNT', 5)

    @property
    def app_name(self) -> str:
        """アプリケーション名（ログに含める）"""
        return os.getenv('APP_NAME', 'payment-api')

    @property
    def environment(self) -> str:
        """環境名（dev/staging/prod）"""
        return os.getenv('ENVIRONMENT', 'development')

    @property
    def sqlalchemy_log_level(self) -> str:
        """SQLAlchemyのログレベル"""
        return os.getenv('SQLALCHEMY_LOG_LEVEL', 'WARNING').upper()

    @property
    def sqlalchemy_pool_log_level(self) -> str:
        """SQLAlchemy Poolのログレベル"""
        return os.getenv('SQLALCHEMY_POOL_LOG_LEVEL', 'WARNING').upper()

    @property
    def uvicorn_log_level(self) -> str:
        """Uvicornのログレベル"""
        return os.getenv('UVICORN_LOG_LEVEL', 'INFO').upper()

    @property
    def uvicorn_access_log_level(self) -> str:
        """Uvicorn Accessログのログレベル"""
        return os.getenv('UVICORN_ACCESS_LOG_LEVEL', 'WARNING').upper()

    @property
    def urllib3_log_level(self) -> str:
        """urllib3のログレベル"""
        return os.getenv('URLLIB3_LOG_LEVEL', 'WARNING').upper()

    @property
    def asyncio_log_level(self) -> str:
        """asyncioのログレベル"""
        return os.getenv('ASYNCIO_LOG_LEVEL', 'WARNING').upper()

    @property
    def log_colorize(self) -> bool:
        """カラーログを有効にするか（開発環境用）"""
        return self._get_bool('LOG_COLORIZE', 'true')


# シングルトンインスタンス
log_config = LogConfig()


# ============================================================================
# カスタムフォーマッター
# ============================================================================

class JSONFormatter(logging.Formatter):
    """
    JSON形式のログフォーマッター

    ログ集約ツール（Fluentd, Logstash, CloudWatch等）に最適
    """

    def format(self, record: logging.LogRecord) -> str:
        """ログレコードをJSON形式に変換"""

        # 基本情報
        log_data: dict[str, Any] = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'thread': record.thread,
            'thread_name': record.threadName,
        }

        # アプリケーション情報
        log_data['app'] = {
            'name': log_config.app_name,
            'environment': log_config.environment,
        }

        # リクエストID（存在する場合）
        request_id = request_id_var.get()
        if request_id:
            log_data['request_id'] = request_id

        # extraフィールドを追加
        standard_keys = logging.LogRecord('', 0, '', 0, '', (), None, None).__dict__.keys()

        for key, value in record.__dict__.items():
            if key not in standard_keys:
                # JSONに変換可能な型のみを対象とする
                if isinstance(value, (str, int, float, bool, list, dict, type(None))):
                    log_data[key] = value

        # 例外情報を追加（exc_infoが有効な例外情報を持つ場合のみ）
        if record.exc_info and record.exc_info[0] is not None:
            log_data['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]) if record.exc_info[1] else None,
                'traceback': self.formatException(record.exc_info)
            }

        return json.dumps(log_data, ensure_ascii=False, default=str)


class StructuredFormatter(logging.Formatter):
    """
    人間が読みやすい構造化ログフォーマッター

    開発環境やコンソール出力に最適
    """

    # ログレベルに対応する色（ANSI エスケープコード）
    COLORS = {
        'DEBUG': '\033[36m',        # Cyan
        'INFO': '\033[32m',         # Green
        'WARNING': '\033[33m',      # Yellow
        'ERROR': '\033[31m',        # Red
        'CRITICAL': '\033[35m',     # Magenta
        'RESET': '\033[0m'          # Reset
    }

    def __init__(self, colorize: bool = False):
        super().__init__()
        self.colorize = colorize

    def format(self, record: logging.LogRecord) -> str:
        """ログレコードを構造化テキスト形式に変換"""

        # タイムスタンプ（ミリ秒まで含む）
        now = datetime.now(timezone.utc)
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S.') + f"{now.microsecond // 1000:03d}"

        # ログレベル（色付き）
        if self.colorize:
            color = self.COLORS.get(record.levelname, '')
            reset = self.COLORS['RESET']
            level = f"{color}{record.levelname:8}{reset}"
        else:
            level = f"{record.levelname:8}"

        # リクエストID
        request_id = request_id_var.get()
        request_id_str = f"[{request_id[:8]}]" if request_id else "[-]"

        # ロガー名（短縮）
        logger_name = record.name
        if len(logger_name) > 30:
            logger_name = '...' + logger_name[-27:]

        # 基本メッセージ
        message = record.getMessage()

        # extraフィールドを追加
        extra_str = ""
        standard_keys = logging.LogRecord('', 0, '', 0, '', (), None, None).__dict__.keys()

        custom_attrs = {}
        for key, value in record.__dict__.items():
            if key not in standard_keys:
                custom_attrs[key] = value

        if custom_attrs:
            extra_items = [f"{k}={v}" for k, v in custom_attrs.items()]
            if extra_items:
                extra_str = f" | {', '.join(extra_items)}"

        # 例外情報を追加（exc_infoが有効な例外情報を持つ場合のみ）
        exception_str = ""
        if record.exc_info and record.exc_info[0] is not None:
            exception_str = f"\n{self.formatException(record.exc_info)}"

        # フォーマット
        log_line = (
            f"{timestamp} | "
            f"{level} | "
            f"{request_id_str} | "
            f"{logger_name:30} | "
            f"{message}"
            f"{extra_str}"
            f"{exception_str}"
        )

        return log_line


class ExtraAdapter(logging.LoggerAdapter):
    """
    extraフィールドを簡単に追加できるアダプター

    カスタムキーワード引数（kwargs）をすべて extra 辞書に変換し、
    標準ロガーに渡す前に kwargs をクリアすることで、
    Logger._log() の TypeError を回避する。
    """

    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        """
        カスタムキーワード引数を extra フィールドにマージし、kwargsをクリーンアップする
        """

        # 1. ログ呼び出しに渡されたカスタムキーワード引数を抽出
        standard_kwargs = {'exc_info', 'extra', 'stack_info', 'stacklevel'}
        custom_kwargs = {k: v for k, v in kwargs.items() if k not in standard_kwargs}

        # 2. 抽出したカスタム引数を kwargs から削除
        for k in custom_kwargs:
            del kwargs[k]

        # 3. 既存の extra フィールドとカスタム引数をマージ
        if 'extra' not in kwargs:
            kwargs['extra'] = {}

        # self.extra をマージ
        if self.extra:
            kwargs['extra'].update(self.extra)

        # ログ呼び出し時に渡されたカスタム引数を extra にマージ
        kwargs['extra'].update(custom_kwargs)

        # 4. stacklevelを調整（ExtraAdapterを経由するため+1）
        if 'stacklevel' not in kwargs:
            kwargs['stacklevel'] = 2
        else:
            kwargs['stacklevel'] += 1

        return msg, kwargs


# ============================================================================
# ログ設定関数
# ============================================================================

def setup_logging(
    log_level: Optional[str] = None,
    json_format: Optional[bool] = None,
    file_enabled: Optional[bool] = None,
    colorize: Optional[bool] = None
) -> logging.Logger:
    """
    アプリケーション全体のログ設定

    Args:
        log_level: ログレベル（環境変数より優先）
        json_format: JSON形式で出力するか（環境変数より優先）
        file_enabled: ファイル出力を有効にするか（環境変数より優先）
        colorize: カラーログを有効にするか（環境変数より優先）

    Returns:
        設定されたルートロガー
    """

    # パラメータまたは環境変数から設定を取得
    _log_level = log_level or log_config.log_level
    _json_format = json_format if json_format is not None else log_config.log_json_format
    _file_enabled = file_enabled if file_enabled is not None else log_config.log_file_enabled
    _colorize = colorize if colorize is not None else log_config.log_colorize

    # ルートロガーの設定
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, _log_level))

    # 既存のハンドラーをクリア
    if root_logger.handlers:
        root_logger.handlers.clear()

    # フォーマッターの選択
    if _json_format:
        formatter = JSONFormatter()
    else:
        formatter = StructuredFormatter(colorize=_colorize)

    # ============================================================================
    # 1. コンソールハンドラー（stdout）- ERROR未満
    # ============================================================================
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    # ERRORレベル未満をstdoutに出力
    console_handler.addFilter(lambda record: record.levelno < logging.ERROR)
    root_logger.addHandler(console_handler)

    # ============================================================================
    # 2. エラーコンソールハンドラー（stderr）- ERROR以上
    # ============================================================================
    error_console_handler = logging.StreamHandler(sys.stderr)
    error_console_handler.setLevel(logging.ERROR)
    error_console_handler.setFormatter(formatter)
    root_logger.addHandler(error_console_handler)

    # ============================================================================
    # 3. ファイルハンドラー（オプション）
    # ============================================================================
    if _file_enabled:
        _setup_file_handlers(root_logger, formatter)

    # ============================================================================
    # 外部ライブラリのログレベル調整（環境変数で制御可能）
    # ============================================================================
    _configure_external_loggers()

    # ============================================================================
    # 起動時のログ出力
    # ============================================================================
    root_logger.info(
        "Logging system initialized",
        extra={
            'log_level': _log_level,
            'json_format': _json_format,
            'file_enabled': _file_enabled,
            'app_name': log_config.app_name,
            'environment': log_config.environment
        }
    )

    return root_logger


def _setup_file_handlers(root_logger: logging.Logger, formatter: logging.Formatter) -> None:
    """ファイルハンドラーのセットアップ"""

    # 通常ログファイル
    try:
        log_file_path = Path(log_config.log_file_path)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)

        # 書き込み権限チェック
        if log_file_path.exists() and not os.access(log_file_path, os.W_OK):
            raise PermissionError(f"No write permission for log file: {log_file_path}")
        if not log_file_path.exists() and not os.access(log_file_path.parent, os.W_OK):
            raise PermissionError(f"No write permission for log directory: {log_file_path.parent}")

        file_handler = logging.handlers.RotatingFileHandler(
            log_config.log_file_path,
            maxBytes=log_config.log_file_max_bytes,
            backupCount=log_config.log_file_backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    except (PermissionError, OSError) as e:
        # ファイルハンドラーのセットアップに失敗してもアプリケーションは継続
        root_logger.warning("Failed to setup file handler: %s", e)

    # エラーログファイル
    try:
        error_log_path = Path(log_config.error_log_file_path)
        error_log_path.parent.mkdir(parents=True, exist_ok=True)

        # 書き込み権限チェック
        if error_log_path.exists() and not os.access(error_log_path, os.W_OK):
            raise PermissionError(f"No write permission for error log file: {error_log_path}")
        if not error_log_path.exists() and not os.access(error_log_path.parent, os.W_OK):
            raise PermissionError(f"No write permission for error log directory: {error_log_path.parent}")

        error_file_handler = logging.handlers.TimedRotatingFileHandler(
            log_config.error_log_file_path,
            when='midnight',
            interval=1,
            backupCount=30,
            encoding='utf-8'
        )
        error_file_handler.setLevel(logging.ERROR)
        error_file_handler.setFormatter(formatter)
        root_logger.addHandler(error_file_handler)

    except (PermissionError, OSError) as e:
        root_logger.warning("Failed to setup error file handler: %s", e)


def _configure_external_loggers() -> None:
    """外部ライブラリのログレベルを環境変数から設定"""

    # SQLAlchemy
    logging.getLogger('sqlalchemy.engine').setLevel(
        getattr(logging, log_config.sqlalchemy_log_level)
    )
    logging.getLogger('sqlalchemy.pool').setLevel(
        getattr(logging, log_config.sqlalchemy_pool_log_level)
    )

    # Uvicorn
    logging.getLogger('uvicorn').setLevel(
        getattr(logging, log_config.uvicorn_log_level)
    )
    logging.getLogger('uvicorn.access').setLevel(
        getattr(logging, log_config.uvicorn_access_log_level)
    )

    # その他のライブラリ
    logging.getLogger('urllib3').setLevel(
        getattr(logging, log_config.urllib3_log_level)
    )
    logging.getLogger('asyncio').setLevel(
        getattr(logging, log_config.asyncio_log_level)
    )


# ============================================================================
# ヘルパー関数
# ============================================================================

def get_logger(name: str) -> ExtraAdapter:
    """
    名前付きロガーを取得し、ExtraAdapterでラップして返す

    Args:
        name: ロガー名（通常は__name__）

    Returns:
        ExtraAdapterでラップされたロガー
    """
    logger = logging.getLogger(name)
    return ExtraAdapter(logger, {})


def set_request_id(request_id: str) -> None:
    """現在のリクエストIDを設定"""
    request_id_var.set(request_id)


def get_request_id() -> Optional[str]:
    """現在のリクエストIDを取得"""
    return request_id_var.get()


# ============================================================================
# パフォーマンス最適化用デコレーター
# ============================================================================

def log_execution_time(logger: logging.Logger, log_level: str = 'debug'):
    """
    関数の実行時間をログ出力するデコレーター

    Args:
        logger: ロガー
        log_level: ログレベル
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                execution_time = time.perf_counter() - start_time

                log_method = getattr(logger, log_level)
                log_method(
                    f"Function '{func.__name__}' executed",
                    function=func.__name__,
                    execution_time_ms=f"{execution_time * 1000:.2f}"
                )
                return result
            except Exception as e:
                execution_time = time.perf_counter() - start_time
                logger.error(
                    f"Function '{func.__name__}' failed after {execution_time * 1000:.2f}ms",
                    function=func.__name__,
                    execution_time_ms=f"{execution_time * 1000:.2f}",
                    error=str(e),
                    exc_info=True
                )
                raise
        return wrapper
    return decorator


# ============================================================================
# 使用例
# ============================================================================

if __name__ == "__main__":
    os.environ['LOG_LEVEL'] = 'DEBUG'
    os.environ['LOG_JSON_FORMAT'] = 'false'
    os.environ['LOG_COLORIZE'] = 'true'

    setup_logging()
    logger = get_logger(__name__)

    logger.debug("This is a debug message")
    logger.info("Application started successfully")
    logger.warning("This is a warning")

    logger.info(
        "User logged in",
        user_id='user_123',
        ip_address='192.168.1.1',
        user_agent='Mozilla/5.0'
    )

    set_request_id('req_abc123')
    logger.info("Processing request")

    try:
        raise ValueError("Something went wrong")
    except Exception:
        logger.error("An error occurred", exc_info=True)
