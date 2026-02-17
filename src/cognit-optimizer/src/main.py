#!/usr/bin/env python3
"""Main entrypoint for the Cognit Optimizer Daemon."""

import sys
import argparse
import time
from modules.config import OPTIMIZER_UPDATE_INTERVAL_SECONDS, LOG_LEVEL
from modules.logger import setup_logging, get_logger

logger = get_logger(__name__)

def run_optimization_cycle() -> bool:
    """Run single optimization cycle with database updates."""
    from modules.optimizer_adapter import run_optimization_with_db_updates
    result = run_optimization_with_db_updates()
    return result is not None

def main() -> int:
    """Main entry point for optimizer."""
    setup_logging(log_level=LOG_LEVEL)

    parser = argparse.ArgumentParser(description='Cognit Optimizer Daemon')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon (continuous loop)')
    parser.add_argument('--interval', type=int, help='Update interval in seconds')
    args = parser.parse_args()

    if args.daemon:
        interval = args.interval or OPTIMIZER_UPDATE_INTERVAL_SECONDS
        logger.info(f"Starting daemon mode (interval: {interval}s, press Ctrl+C to stop)")
        try:
            while True:
                run_optimization_cycle()
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Daemon stopped by user")
            return 0
    else:
        return 0 if run_optimization_cycle() else 1

if __name__ == "__main__":
    sys.exit(main())
