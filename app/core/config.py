"""Application Configuration"""
import json
from typing import Annotated, List, Literal
from urllib.parse import quote

from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from pydantic import computed_field, field_validator


class Settings(BaseSettings):
    """Application settings"""

    # ===========================================
    # 環境設定 (PRODUCTION / DEVELOP)
    # ===========================================
    ENVIRONMENT: Literal["PRODUCTION", "DEVELOP"] = "PRODUCTION"

    # L3 ACCESS (環境変数から取得 - デフォルト値なし)
    L3_BASE_URL: str = ""
    L3_INTROSPECT_ENDPOINT: str = "/auth/token/introspect"
    L3_API_KEY: str = ""
    L3_CLIENT_ID: str = ""
    L3_CLIENT_SECRET: str = ""

    # ===========================================
    # Database ACCESS (Production) - 環境変数から取得必須
    # ===========================================
    POSTGRES_HOST: str = ""
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = ""
    POSTGRES_USER: str = ""
    POSTGRES_PASSWORD: str = ""

    # ===========================================
    # Database Connection Pool Settings
    # ===========================================
    DB_CONNECT_TIMEOUT: int = 10  # DB接続確立のタイムアウト（秒）
    DB_POOL_TIMEOUT: int = 10     # プールから接続取得のタイムアウト（秒）
    DB_LOCK_TIMEOUT: int = 3      # 取引の行ロック待ちのタイムアウト（秒）。超過時は 503（Retry-After 付き）

    # ===========================================
    # Database ACCESS (Develop) - ローカルDocker用
    # ===========================================
    DEV_POSTGRES_HOST: str = "payment-db"
    DEV_POSTGRES_PORT: int = 5432
    DEV_POSTGRES_DB: str = "fastapi_db"
    DEV_POSTGRES_USER: str = "postgres"
    DEV_POSTGRES_PASSWORD: str = "postgres"

    # ===========================================
    # From L2 access (環境変数から取得必須)
    # ===========================================
    # Payment API-Key
    X_PAYMENT_API_KEY: str = ""

    # ===========================================
    # オペレーターID検証設定
    # ===========================================
    OPERATOR_ID_VERIFICATION_ENABLED: bool = True

    # ===========================================
    # 認可サービス設定
    # ===========================================
    AUTHZ_ENABLED: bool = False
    AUTHZ_BASE_URL: str = ""
    AUTHZ_OPENFGA_STORE_ID: str = ""
    AUTHZ_OPENFGA_MODEL_ID: str = ""

    # ===========================================
    # L2 HTTPステータスフィルタ設定
    # True: 支払/請求予定額に l2_http_status が 2xx（L2ログ突合で成功確認済み）の取引のみ含める
    # False: L2ログ突合 CronJob を運用しない環境向け（L2 未確定の取引も含める）
    # いずれの場合も settlement_status='cancelled' の取引は含めない
    # ===========================================
    L2_HTTP_STATUS_FILTER_ENABLED: bool = True

    # ===========================================
    # 外部決済サービス設定
    # ===========================================
    EXTERNAL_PAYMENT_ENABLED: bool = True

    # ===========================================
    # NP掛け払いサービス設定
    # ===========================================
    NP_KAKEBARAI_BASE_URL: str = ""
    NP_KAKEBARAI_SHOP_CODE: str = ""
    NP_KAKEBARAI_SP_CODE: str = ""
    NP_KAKEBARAI_TERMINAL_ID: str = ""
    # DBのpayment_services.payment_service_idと照合するUUID
    NP_KAKEBARAI_SERVICE_ID: str = ""

    # ===========================================
    # Application
    # ===========================================
    PROJECT_NAME: str = "精算決済API"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # ===========================================
    # Security (環境変数から取得必須)
    # ===========================================
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ===========================================
    # CORS (本番環境では具体的なオリジンを指定すること)
    # ===========================================
    # 環境変数ではカンマ区切り（a,b）または JSON 配列（["a","b"]）で指定する
    # （NoDecode: pydantic-settings に JSON として解釈させず、parse_allowed_hosts で解釈する）
    ALLOWED_HOSTS: Annotated[List[str], NoDecode] = []

    @field_validator('DB_LOCK_TIMEOUT')
    @classmethod
    def validate_db_lock_timeout(cls, v: int) -> int:
        """PostgreSQL の lock_timeout は 0 で無制限になるため、1 秒以上に限る"""
        if v < 1:
            raise ValueError('DB_LOCK_TIMEOUT must be 1 or greater')
        return v

    @field_validator('SECRET_KEY')
    @classmethod
    def validate_secret_key(cls, v: str, info) -> str:
        """本番環境ではSECRET_KEYが必須"""
        # 開発環境では空でも許容（他のバリデータで環境をチェック）
        return v

    @field_validator('ALLOWED_HOSTS', mode='before')
    @classmethod
    def parse_allowed_hosts(cls, v):
        """ALLOWED_HOSTS（CORS の許可オリジン）をパース（カンマ区切り文字列・JSON 配列・リスト）"""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith('['):
                return json.loads(v)
            return [host.strip() for host in v.split(',') if host.strip()]
        return v
    
    # ===========================================
    # Logging
    # ===========================================
    LOG_LEVEL: str = "INFO"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True
    )

    @computed_field
    @property
    def L3_TOKEN_INTROSPECT_ENDPOINT(self) -> str:
        """L3トークンイントロスペクションエンドポイント"""
        return f"{self.L3_BASE_URL}{self.L3_INTROSPECT_ENDPOINT}"

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        """環境に応じたデータベースURLを返す"""
        if self.ENVIRONMENT == "DEVELOP":
            # ユーザー名・パスワードは URL エンコードする（'@' や '%' を含む場合に URL が壊れないように）
            return (
                f"postgresql://{quote(self.DEV_POSTGRES_USER, safe='')}:{quote(self.DEV_POSTGRES_PASSWORD, safe='')}"
                f"@{self.DEV_POSTGRES_HOST}:{self.DEV_POSTGRES_PORT}/{self.DEV_POSTGRES_DB}"
            )
        else:
            # 本番環境ではSSL接続を推奨
            return (
                f"postgresql://{quote(self.POSTGRES_USER, safe='')}:{quote(self.POSTGRES_PASSWORD, safe='')}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )

    @computed_field
    @property
    def IS_PRODUCTION(self) -> bool:
        """本番環境かどうか"""
        return self.ENVIRONMENT == "PRODUCTION"

    @computed_field
    @property
    def IS_DEVELOP(self) -> bool:
        """開発環境かどうか"""
        return self.ENVIRONMENT == "DEVELOP"

settings = Settings()

