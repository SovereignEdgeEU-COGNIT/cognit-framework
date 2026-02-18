import yaml
import os
import socket
import sys
from urllib.parse import urlparse
from pathlib import Path

# Try /etc/one/ first (package default), then /etc/ (backwards compat)
PATH = "/etc/one/cognit-devices-estimated-load.conf"
PATH_FALLBACK = "/etc/cognit-devices-estimated-load.conf"
DEFAULT = {
    'log_level': 'info',
    'one_xmlrpc': 'http://localhost:2633/RPC2',
    'db_path': '/root/devices_local_database/device_cluster_assignment.db',
    'db_cleanup_days': 30,
    # OpenNebula MySQL database (for monitoring data)
    'one_db_host': '127.0.0.1',
    'one_db_port': 3306,
    'one_db_database': 'opennebula',
    'one_db_user': 'oneadmin',
    'one_db_password': '',
    # OpenNebula API credentials (for OneFlow REST API)
    'one_api_user': 'oneadmin',
    'one_api_password': '',
    # Daemon configuration
    'estimated_load_update_interval_seconds': 30,
    # CPU time series storage
    'cpu_timeseries_db_dir': '/root/oneflow_cpu_forecast_databases/',
    'forecast_horizon_seconds': 60,
}

FALLBACK_MSG = 'Using default configuration'

config = DEFAULT.copy()
user_config = {}

config_path = PATH if os.path.exists(PATH) else (PATH_FALLBACK if os.path.exists(PATH_FALLBACK) else None)

if config_path:
    with open(config_path, 'r') as file:
        try:
            user_config = yaml.safe_load(file)
            if not isinstance(user_config, dict):
                user_config = {}
        except yaml.YAMLError as e:
            print(f"{e}\n{FALLBACK_MSG}")
            user_config = {}
else:
    print(f"{PATH} not found. {FALLBACK_MSG}.")

if user_config:
    config.update(user_config)

ONE_XMLRPC = config['one_xmlrpc']

one = urlparse(ONE_XMLRPC)
port = one.port

if one.port is None:
    if one.scheme == 'https':
        port = 443
    elif one.scheme == 'http':
        port = 80

try:
    socket.create_connection((one.hostname, port), timeout=5)
except socket.error as e:
    print(f"Warning: Unable to connect to OpenNebula at {ONE_XMLRPC}: {str(e)}")
    print("The daemon will attempt to connect when it runs.")

# Configuration variables
LOG_LEVEL = config['log_level']
DB_PATH = config['db_path']
DB_CLEANUP_DAYS = config['db_cleanup_days']

# OpenNebula MySQL database (for monitoring data)
ONE_DB_HOST = config['one_db_host']
ONE_DB_PORT = config['one_db_port']
ONE_DB_DATABASE = config['one_db_database']
ONE_DB_USER = config['one_db_user']
ONE_DB_PASSWORD = config['one_db_password']

# OpenNebula API credentials
ONE_API_USER = config['one_api_user']
ONE_API_PASSWORD = config['one_api_password']

# Daemon configuration
ESTIMATED_LOAD_UPDATE_INTERVAL_SECONDS = config['estimated_load_update_interval_seconds']

# CPU time series storage
CPU_TIMESERIES_DB_DIR = config['cpu_timeseries_db_dir']
FORECAST_HORIZON_SECONDS = config['forecast_horizon_seconds']

# Database configuration dictionary for one-aiops SDK
DB_CONFIG = {
    'host': ONE_DB_HOST,
    'port': ONE_DB_PORT,
    'database': ONE_DB_DATABASE,
    'user': ONE_DB_USER,
    'password': ONE_DB_PASSWORD,
}
