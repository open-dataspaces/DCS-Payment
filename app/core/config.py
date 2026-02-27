"""Application Configuration"""
from typing import List, Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
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
    # ===========================================
    L2_HTTP_STATUS_FILTER_ENABLED: bool = False

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
    ALLOWED_HOSTS: List[str] = []

    @field_validator('SECRET_KEY')
    @classmethod
    def validate_secret_key(cls, v: str, info) -> str:
        """本番環境ではSECRET_KEYが必須"""
        # 開発環境では空でも許容（他のバリデータで環境をチェック）
        return v

    @field_validator('ALLOWED_HOSTS', mode='before')
    @classmethod
    def parse_allowed_hosts(cls, v):
        """CORS_ORIGINSをパース（カンマ区切り文字列またはリスト）"""
        if isinstance(v, str):
            if not v:
                return []
            return [host.strip() for host in v.split(',')]
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
            return (
                f"postgresql://{self.DEV_POSTGRES_USER}:{self.DEV_POSTGRES_PASSWORD}"
                f"@{self.DEV_POSTGRES_HOST}:{self.DEV_POSTGRES_PORT}/{self.DEV_POSTGRES_DB}"
            )
        else:
            # 本番環境ではSSL接続を推奨
            return (
                f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
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

