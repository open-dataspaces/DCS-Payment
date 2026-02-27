"""Database Initialization"""
from app.core.logging import get_logger
from app.db.session import engine
from app.models.base import Base

# Import all models here for Alembic
from app.models.payment_model import (
    PaymentService,
    FeeModel,
    FeeModelHistory,
    PaymentServiceUserRegistration,
    Transaction,
)

logger = get_logger(__name__)


def create_tables():
    """Create all tables"""
    logger.info("Attempting to create tables")

    recognized_tables = list(Base.metadata.tables.keys())
    logger.info(
        "SQLAlchemy recognized tables",
        extra={"table_count": len(recognized_tables)}
    )

    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Tables created successfully")

        for table_name in recognized_tables:
            logger.debug("Table created", extra={"table_name": table_name})

    except Exception as e:
        logger.error(
            "An error occurred during table creation",
            extra={"error": str(e)},
            exc_info=True
        )
        raise


def init_db() -> None:
    """Initialize database"""
    try:
        logger.info("Database initialization started")
        create_tables()
        logger.info("Database initialization completed")

    except Exception as e:
        logger.error(
            "Database initialization failed",
            extra={"error": str(e)},
            exc_info=True
        )
        raise
