"""Centralized logging configuration for cognit-devices-estimated-load."""

import logging
import logging.handlers
import os
from pathlib import Path


LOG_DIR = "/var/log/cognit-devices-estimated-load"
LOG_FILE = os.path.join(LOG_DIR, "estimated-load.log")
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5  # Keep 5 backup files


def setup_logging(log_level: str = "INFO") -> None:
    """Configure centralized logging for the application.
    
    Creates log directory if needed, sets up rotating file handler,
    and configures log format with date, filename, and other metadata.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    try:
        Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    except PermissionError:
        import warnings
        warnings.warn(f"Could not create log directory {LOG_DIR}. Logging may fail.")
    
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    root_logger.handlers.clear()
    
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(numeric_level)
    
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(filename)s:%(funcName)s:%(lineno)d - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    
    root_logger.addHandler(file_handler)
    
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
