"""Database Session Management"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

from app.core.logging import get_logger
from app.core.config import settings

logger = get_logger(__name__)

# ============================================================================
# データベース設定
# ============================================================================
# DATABASE_URLはapp.core.config.settingsで一元管理


# ============================================================================
# エンジンとセッションの作成
# ============================================================================

def create_database_engine():
    """
    データベースエンジンを作成
    
    Returns:
        SQLAlchemyエンジン
    """

    # PostgreSQL等の場合はプール設定を有効化
    logger.info("Using PostgreSQL database with connection pool")

    engine = create_engine(
        settings.DATABASE_URL,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        connect_args={"connect_timeout": settings.DB_CONNECT_TIMEOUT},
    )
    
    logger.info(
        "Database engine created"
    )
    
    return engine


# エンジンの作成
engine = create_database_engine()

# セッションファクトリーの作成
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# ============================================================================
# セッション管理
# ============================================================================

def get_db() -> Generator[Session, None, None]:
    """
    データベースセッションの依存性注入用関数
    
    FastAPIのDependsで使用:
        @app.get("/items/")
        def read_items(db: Session = Depends(get_db)):
            ...
    
    Yields:
        データベースセッション
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """
    データベースセッションを取得（手動管理用）
    
    使用例:
        db = get_db_session()
        try:
            # 処理
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    
    Returns:
        データベースセッション
    """
    return SessionLocal()


# ============================================================================
# ヘルスチェック
# ============================================================================

def check_database_connection() -> bool:
    """
    データベース接続をチェック
    
    Returns:
        接続成功の場合True
    """
    try:
        db = SessionLocal()
        # 簡単なクエリを実行
        db.execute(text("SELECT 1"))
        db.close()
        logger.info("Database connection check: OK")
        return True
    except Exception as e:
        logger.error(
            "Database connection check failed",
            extra={"error": str(e)},
            exc_info=True
        )
        return False
    
# ============================================================================
# トランザクション管理ヘルパー
# ============================================================================

class TransactionManager:
    """
    トランザクション管理のコンテキストマネージャー
    
    使用例:
        with TransactionManager() as db:
            user = User(name="John")
            db.add(user)
            # commitは自動的に実行される
    """
    
    def __init__(self, db: Session = None):
        """
        Args:
            db: 既存のセッション（Noneの場合は新規作成）
        """
        self.db = db
        self.own_session = db is None
    
    def __enter__(self) -> Session:
        if self.own_session:
            self.db = SessionLocal()
        return self.db
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # 例外が発生した場合はロールバック
            if self.db:
                self.db.rollback()
                logger.error("Transaction rolled back due to exception")
        else:
            # 正常終了時はコミット
            if self.db:
                self.db.commit()
                logger.debug("Transaction committed")
        
        # 自分で作成したセッションの場合はクローズ
        if self.own_session and self.db:
            self.db.close()
