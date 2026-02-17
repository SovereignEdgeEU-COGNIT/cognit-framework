import yaml
import os
import sys

# Try /etc/one/cognit-optimizer.conf first (package default), then /etc/cognit-optimizer.conf (backwards compat)
PATH = "/etc/one/cognit-optimizer.conf"
PATH_FALLBACK = "/etc/cognit-optimizer.conf"
DEFAULT = {
    'db_path': '/root/devices_local_database/device_cluster_assignment.db',
    'db_cleanup_days': 30,
    'cognit_frontend_src': '/usr/lib/one/cognit-frontend/src',
    'one_xmlrpc': 'https://cognit-lab.sovereignedge.eu/RPC2',
    'one_auth_user': 'oneadmin',
    'one_auth_password': 'CHANGE_ME',
    'optimizer_update_interval_seconds': 300,
    'optimizer_enabled': True,
    'log_level': 'info',
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

# Database configuration
DB_PATH = config['db_path']
DB_CLEANUP_DAYS = config['db_cleanup_days']

# Cognit frontend paths
COGNIT_FRONTEND_SRC = config['cognit_frontend_src']

# OpenNebula configuration
ONE_XMLRPC_ENDPOINT = config['one_xmlrpc']
ONE_AUTH_USER = config['one_auth_user']
ONE_AUTH_PASSWORD = config['one_auth_password']

# OpenNebula API response keys
CLUSTER_POOL_KEY = 'CLUSTER_POOL'
CLUSTER_KEY = 'CLUSTER'
TEMPLATE_KEY = 'TEMPLATE'
DOCUMENT_KEY = 'DOCUMENT'
ID_KEY = 'ID'

# Optimizer configuration
OPTIMIZER_ENABLED = config['optimizer_enabled']
OPTIMIZER_UPDATE_INTERVAL_SECONDS = config['optimizer_update_interval_seconds']
LOG_LEVEL = config['log_level']
