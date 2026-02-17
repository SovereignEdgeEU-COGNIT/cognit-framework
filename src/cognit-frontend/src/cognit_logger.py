"""Centralized logging configuration for cognit-frontend."""

import logging
import logging.handlers
import os
from pathlib import Path


LOG_DIR = "/var/log/cognit-frontend"
LOG_FILE = os.path.join(LOG_DIR, "cognit-frontend.log")
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5  # Keep 5 backup files


def setup_logging(log_level: str = "INFO") -> None:
    """Configure centralized logging for the application.
    
    Creates log directory if needed, sets up rotating file handler,
    and configures log format with date, filename, and other metadata.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Create log directory if it doesn't exist
    try:
        Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # Log directory creation failed, will try to write to file anyway
        # (may fail later, but allows application to start)
        import warnings
        warnings.warn(f"Could not create log directory {LOG_DIR}. Logging may fail.")
    
    # Convert string level to logging constant
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()
    
    # Create rotating file handler
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(numeric_level)
    
    # Create formatter with date, filename, function name, line number, level, and message
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(filename)s:%(funcName)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    
    # Add handler to root logger
    root_logger.addHandler(file_handler)
    
    # Also add console handler for development (optional, can be removed in production)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a module.
    
    Args:
        name: Logger name (typically __name__ of the calling module)
    
    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)

