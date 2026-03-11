"""OneFlow FaaS role scaler for automatic scale-up on new device assignment.

Provides functions to look up the OneFlow service associated with an
OpenNebula cluster and scale the FaaS role cardinality by +1, waiting
until the service returns to RUNNING state before proceeding.
"""

import json
import subprocess
import time
from typing import Optional

import pyone

import cognit_conf as conf
from cognit_logger import get_logger

logger = get_logger(__name__)

ONEFLOW_STATE_RUNNING = 2
SCALE_POLL_INTERVAL = 5
SCALE_TIMEOUT = 30


def get_cluster_name(cluster_id: int) -> Optional[str]:
    """Resolve an OpenNebula cluster ID to its name via pyone."""
    try:
        session = f"{conf.ONE_API_USER}:{conf.ONE_API_PASSWORD}"
        one = pyone.OneServer(conf.ONE_XMLRPC, session=session)
        cluster = one.cluster.info(cluster_id)
        return cluster.NAME
    except Exception as e:
        logger.error(f"Failed to get cluster name for cluster_id={cluster_id}: {e}")
        return None


def list_oneflow_services() -> list:
    """Return the list of OneFlow service dicts via the CLI."""
    try:
        result = subprocess.run(
            ["oneflow", "list", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"oneflow list failed (rc={result.returncode}): {result.stderr}")
            return []
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        logger.error("oneflow list timed out")
        return []
    except Exception as e:
        logger.error(f"Error running oneflow list: {e}")
        return []


def get_oneflow_service(service_id: str) -> Optional[dict]:
    """Fetch a single OneFlow service by ID via the CLI."""
    try:
        result = subprocess.run(
            ["oneflow", "show", str(service_id), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"oneflow show {service_id} failed (rc={result.returncode}): {result.stderr}")
            return None
        data = json.loads(result.stdout)
        return data.get("DOCUMENT", data)
    except subprocess.TimeoutExpired:
        logger.error(f"oneflow show {service_id} timed out")
        return None
    except Exception as e:
        logger.error(f"Error running oneflow show {service_id}: {e}")
        return None


def find_service_for_cluster(cluster_name: str) -> Optional[dict]:
    """Find the OneFlow service whose name contains *cluster_name* (case-insensitive)."""
    services = list_oneflow_services()
    target = cluster_name.lower()
    for svc in services:
        svc_name = svc.get("NAME", "")
        if target in svc_name.lower():
            return svc
    return None


def get_faas_cardinality(service: dict) -> Optional[int]:
    """Extract the current FaaS role cardinality from a service dict.

    Handles both the compact format from ``oneflow list`` and the full
    format from ``oneflow show``.
    """
    try:
        body = service.get("TEMPLATE", {}).get("BODY", {})
        if isinstance(body, str):
            body = json.loads(body)
        for role in body.get("roles", []):
            if role.get("name") == "FaaS":
                return int(role["cardinality"])
    except Exception as e:
        logger.error(f"Failed to extract FaaS cardinality: {e}")
    return None


def scale_faas(service_id: str, new_cardinality: int) -> bool:
    """Issue ``oneflow scale <id> FaaS <cardinality>``."""
    cmd = ["oneflow", "scale", str(service_id), "FaaS", str(new_cardinality)]
    logger.info(f"Scaling OneFlow service {service_id} FaaS to {new_cardinality}: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.error(f"oneflow scale failed (rc={result.returncode}): {result.stderr}")
            return False
        logger.info(f"oneflow scale command accepted: {result.stdout.strip()}")
        return True
    except subprocess.TimeoutExpired:
        logger.error("oneflow scale command timed out")
        return False
    except Exception as e:
        logger.error(f"Error running oneflow scale: {e}")
        return False


def wait_for_ready(service_id: str, poll_interval: int = SCALE_POLL_INTERVAL,
                   timeout: int = SCALE_TIMEOUT) -> bool:
    """Poll until the OneFlow service state returns to RUNNING (state 2)."""
    deadline = time.time() + timeout
    logger.info(f"Waiting for OneFlow service {service_id} to reach RUNNING state (timeout={timeout}s)")
    while time.time() < deadline:
        svc = get_oneflow_service(service_id)
        if svc is not None:
            body = svc.get("TEMPLATE", {}).get("BODY", {})
            if isinstance(body, str):
                body = json.loads(body)
            state = body.get("state")
            if state == ONEFLOW_STATE_RUNNING:
                logger.info(f"OneFlow service {service_id} is RUNNING")
                return True
            logger.debug(f"OneFlow service {service_id} state={state}, waiting...")
        time.sleep(poll_interval)
    logger.warning(f"Timed out waiting for OneFlow service {service_id} to reach RUNNING state")
    return False


def scale_for_new_device(cluster_id: int) -> bool:
    """Orchestrate a +1 FaaS scale-up for the cluster before a new device is inserted.

    Returns True on success, False on any failure.
    """
    try:
        cluster_name = get_cluster_name(cluster_id)
        if not cluster_name:
            logger.warning(f"Could not resolve cluster name for cluster_id={cluster_id}, skipping scaling")
            return False

        service = find_service_for_cluster(cluster_name)
        if service is None:
            logger.warning(f"No OneFlow service found for cluster '{cluster_name}', skipping scaling")
            return False

        service_id = str(service["ID"])
        current_cardinality = get_faas_cardinality(service)
        if current_cardinality is None:
            logger.warning(f"Could not determine FaaS cardinality for service {service_id}, skipping scaling")
            return False

        new_cardinality = current_cardinality + 1
        logger.info(
            f"Scaling FaaS for cluster '{cluster_name}' (service {service_id}): "
            f"{current_cardinality} -> {new_cardinality}"
        )

        if not scale_faas(service_id, new_cardinality):
            return False

        return wait_for_ready(service_id)

    except Exception as e:
        logger.error(f"Unexpected error during OneFlow scaling for cluster_id={cluster_id}: {e}")
        return False
