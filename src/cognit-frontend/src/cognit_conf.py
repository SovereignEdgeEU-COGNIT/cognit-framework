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
    'log_level': 'info'
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
