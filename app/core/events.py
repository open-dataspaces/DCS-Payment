"""Application Events"""
from typing import Callable
from fastapi import FastAPI

from app.core.logging import setup_logging, get_logger
logger = get_logger(__name__)


def create_start_app_handler(app: FastAPI) -> Callable:
    """Create startup event handler"""
    async def start_app() -> None:
        logger.info("Starting application...")
        setup_logging()
        logger.info("Application started successfully")
    
    return start_app


def create_stop_app_handler(app: FastAPI) -> Callable:
    """Create shutdown event handler"""
    async def stop_app() -> None:
        logger.info("Stopping application...")
        logger.info("Application stopped")
    
    return stop_app