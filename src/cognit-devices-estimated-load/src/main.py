#!/usr/bin/env python3
"""Standalone daemon for updating estimated load for all devices."""

import sys
import argparse
import time
from estimated_load_daemon import update_all_devices_estimated_load
from cognit_logger import setup_logging, get_logger
import cognit_conf as conf

logger = get_logger(__name__)

def run_update_cycle():
    """Run single update cycle."""
    update_all_devices_estimated_load()

def main() -> int:
    """Main entry point for the standalone daemon."""
    setup_logging(conf.LOG_LEVEL)
    
    parser = argparse.ArgumentParser(description='Cognit Devices Estimated Load Daemon')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon (continuous loop)')
    parser.add_argument('--interval', type=int, help='Update interval in seconds')
    args = parser.parse_args()
    
    if args.daemon:
        interval = args.interval or conf.ESTIMATED_LOAD_UPDATE_INTERVAL_SECONDS
        logger.info(f"Starting daemon mode (interval: {interval}s, press Ctrl+C to stop)")
        try:
            while True:
                run_update_cycle()
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Daemon stopped by user")
            return 0
    else:
        logger.info("Running single update cycle")
        run_update_cycle()
        return 0

if __name__ == "__main__":
    sys.exit(main())
