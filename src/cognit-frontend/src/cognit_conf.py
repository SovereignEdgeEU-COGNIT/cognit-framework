import yaml
import os
import socket
import sys
from urllib.parse import urlparse

# Try /etc/one/cognit-frontend.conf first (package default), then /etc/cognit-frontend.conf (backwards compat)
PATH = "/etc/one/cognit-frontend.conf"
PATH_FALLBACK = "/etc/cognit-frontend.conf"
DEFAULT = {
    'host': '0.0.0.0',
    'port': 1338,
    'one_xmlrpc': 'http://localhost:2633/RPC2',
    'ai_orchestrator_endpoint': 'http://localhost:4567',
    'default_cluster': 0,
    'log_level': 'info',
    'db_path': '/root/devices_local_database/device_cluster_assignment.db',
    'db_cleanup_days': 30,
    # Database configuration for one-aiops monitoring
    'one_db_host': '127.0.0.1',
    'one_db_port': 3306,
    'one_db_database': 'opennebula',
    'one_db_user': 'oneadmin',
    'one_db_password': 'opennebula',
    # Estimated load daemon configuration
    'estimated_load_update_interval_seconds': 30,
}

FALLBACK_MSG = 'Using default configuration'

config = DEFAULT.copy()
user_config = {}

# Try primary path first, then fallback
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
    print(f"Error: Unable to connect to OpenNebula at {ONE_XMLRPC}. {str(e)}")
    sys.exit(1)

HOST = config['host']
PORT = config['port']
LOG_LEVEL = config['log_level']
AI_ORCHESTRATOR_ENDPOINT = config['ai_orchestrator_endpoint']
DEFAULT_CLUSTER = config['default_cluster']
DB_PATH = config['db_path']
DB_CLEANUP_DAYS = config['db_cleanup_days']

# Database configuration for one-aiops monitoring
ONE_DB_HOST = config['one_db_host']
ONE_DB_PORT = config['one_db_port']
ONE_DB_DATABASE = config['one_db_database']
ONE_DB_USER = config['one_db_user']
ONE_DB_PASSWORD = config['one_db_password']

# Estimated load daemon configuration
ESTIMATED_LOAD_UPDATE_INTERVAL_SECONDS = config['estimated_load_update_interval_seconds']

# Database configuration dictionary for one-aiops SDK
DB_CONFIG = {
    'host': ONE_DB_HOST,
    'port': ONE_DB_PORT,
    'database': ONE_DB_DATABASE,
    'user': ONE_DB_USER,
    'password': ONE_DB_PASSWORD,
}

# FaaS metrics to collect (using MetricAttributes format)
from pyoneai.core import Float, MetricAttributes, MetricType

FAAS_METRICS = {
    'cpu': MetricAttributes(
        name='cpu',
        type=MetricType.GAUGE,
        dtype=Float(),
    ),
}

# XPath mappings for custom metrics (if needed)
METRIC_XPATH_MAPPING = {
    'cpu': 'CPU',
}