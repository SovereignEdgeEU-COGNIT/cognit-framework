"""Module for scaling clusters based on optimizer output."""

import requests
from urllib3.exceptions import InsecureRequestWarning
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from modules.logger import get_logger
from modules.config import ONE_XMLRPC_ENDPOINT, ONE_AUTH_USER, ONE_AUTH_PASSWORD
from modules.db_adapter import update_device_cluster_assignments

# Suppress SSL warnings for self-signed certificates
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

logger = get_logger(__name__)


def get_cluster_template(cluster_id: int) -> Optional[dict]:
    """Get cluster template from OpenNebula."""
    if not ONE_XMLRPC_ENDPOINT or not ONE_AUTH_USER or not ONE_AUTH_PASSWORD:
        logger.error("OpenNebula credentials not configured")
        return None
    
    try:
        import pyone
        session = f"{ONE_AUTH_USER}:{ONE_AUTH_PASSWORD}"
        one = pyone.OneServer(ONE_XMLRPC_ENDPOINT, session=session)
        cluster = one.cluster.info(cluster_id)
        return dict(cluster.TEMPLATE)
    except Exception as e:
        logger.error(f"Failed to get cluster template for cluster {cluster_id}: {e}")
        return None


def get_flavour_from_template(cluster_template: dict) -> Optional[str]:
    """Get first flavour from cluster template FLAVOURS key."""
    flavours_str = cluster_template.get('FLAVOURS', '')
    if not flavours_str:
        return None
    # Get first flavour without creating a list
    comma_idx = flavours_str.find(',')
    first_flavour = flavours_str[:comma_idx] if comma_idx != -1 else flavours_str
    return first_flavour.strip() or None


def construct_endpoint(cluster_template: dict, flavour: str, target_cardinality: int) -> Optional[str]:
    """Construct the scaling endpoint URL.
    
    Format: {EDGE_CLUSTER_FRONTEND}/{flavour}/v1/scale?target_cardinality={target_cardinality}
    """
    edge_cluster_frontend = cluster_template.get('EDGE_CLUSTER_FRONTEND')
    if not edge_cluster_frontend:
        return None
    
    base_url = edge_cluster_frontend.rstrip('/')
    return f"{base_url}/{flavour}/v1/scale?target_cardinality={target_cardinality}"


def call_scale_endpoint(endpoint: str) -> bool:
    """Call the scaling endpoint with POST request."""
    try:
        response = requests.post(endpoint, verify=False)
        success = response.status_code in [200, 201, 202]
        if success:
            logger.info(f"Successfully scaled cluster via {endpoint} (status: {response.status_code})")
        else:
            logger.warning(f"Failed to scale cluster via {endpoint} (status: {response.status_code}, response: {response.text[:200]})")
        return success
    except Exception as e:
        logger.error(f"Error calling scale endpoint {endpoint}: {e}")
        return False


def scale_cluster(cluster_id: int, target_cardinality: int, flavour: str) -> bool:
    """Scale a single cluster to target cardinality.
    
    Args:
        cluster_id: The cluster ID to scale
        target_cardinality: Target number of VMs for the cluster
        flavour: Flavour to use for scaling (if None, extracts from template)
        
    Returns:
        True if scaling was successful, False otherwise
    """
    
    logger.info(f"Scaling cluster {cluster_id} with flavour {flavour} to target cardinality {target_cardinality}")
    cluster_template = get_cluster_template(cluster_id)
    if not cluster_template:
        return False
    
    endpoint = construct_endpoint(cluster_template, flavour, target_cardinality)
    if not endpoint:
        logger.warning(f"Could not construct endpoint for cluster {cluster_id} (EDGE_CLUSTER_FRONTEND missing)")
        return False
    
    return call_scale_endpoint(endpoint)


def scale_clusters_and_update_db(n_vms: dict[int, int], allocs: dict) -> int:
    """
    Scale clusters in parallel and update DB for each successfully scaled cluster.
    Each flavour within a cluster is scaled separately.
    
    Args:
        n_vms: Cluster ID to target cardinality mapping
        allocs: Composite ID (device_id:::flavour) to cluster ID mapping
        
    Returns:
        Total number of devices updated in database
    """
    logger.info("=== CLUSTER SCALING ===")
    
    if not n_vms:
        logger.info("No clusters to scale")
        return 0
    
    # Group devices by (cluster_id, flavour) and use optimizer's n_vms for target cardinality
    cluster_flavour_targets = {}
    for composite_id, cluster_id in allocs.items():
        if cluster_id in n_vms and ':::' in composite_id:
            flavour = composite_id.split(':::', 1)[1]
            key = (cluster_id, flavour)
            # Use optimizer's calculated VM count, ensure minimum of 1
            if key not in cluster_flavour_targets:
                target_cardinality = max(1, n_vms[cluster_id])
                cluster_flavour_targets[key] = target_cardinality
                logger.info(f"Cluster {cluster_id} (flavour {flavour}): optimizer n_vms={n_vms[cluster_id]}, target_cardinality={target_cardinality}")
    
    if not cluster_flavour_targets:
        logger.info("No device assignments found for clusters to scale")
        return 0
    
    logger.info(f"Scaling {len(cluster_flavour_targets)} cluster-flavour combinations in parallel")
    
    total_updated = 0
    max_workers = max(1, len(cluster_flavour_targets))
    
    def scale_with_flavour(cid: int, flavour: str, card: int) -> tuple[int, str, bool]:
        """Scale a single cluster-flavour combination and return the result."""
        return cid, flavour, scale_cluster(cid, card, flavour)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(scale_with_flavour, cid, flavour, target_cardinality): (cid, flavour)
            for (cid, flavour), target_cardinality in cluster_flavour_targets.items()
        }
        scaled_cluster_flavours = set()  # (cluster_id, flavour)
        for future in as_completed(future_map):
            cid, flavour, ok = future.result()
            if ok:
                scaled_cluster_flavours.add((cid, flavour))
    
    # Update DB only for successfully scaled (cluster_id, flavour) pairs
    for cid, flavour in scaled_cluster_flavours:
        cluster_allocs = {
            dev_id: cluster_id for dev_id, cluster_id in allocs.items()
            if cluster_id == cid and dev_id.split(':::', 1)[1] == flavour
        }
        updated = update_device_cluster_assignments(cluster_allocs)
        total_updated += updated
        if updated > 0:
            logger.info(f"Cluster {cid} ({flavour}) scaled successfully: {updated} devices updated")
    
    logger.info(f"Cluster scaling completed: {total_updated} total devices updated")
    return total_updated

