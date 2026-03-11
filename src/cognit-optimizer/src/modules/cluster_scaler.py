"""Module for scaling clusters based on optimizer output."""

import requests
from enum import Enum
from urllib3.exceptions import InsecureRequestWarning
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from modules.logger import get_logger
from modules.config import (
    ONE_XMLRPC_ENDPOINT, ONE_AUTH_USER, ONE_AUTH_PASSWORD,
    SCALE_UP_TIMEOUT_SECONDS, SCALE_DOWN_TIMEOUT_SECONDS,
)
from modules.db_adapter import update_device_cluster_assignments


class ScaleResult(Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    FAILED = "failed"


_pending_scaledowns: dict[tuple[int, str], int] = {}

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


def call_scale_endpoint(endpoint: str, timeout: int = 30) -> ScaleResult:
    """Call the scaling endpoint with POST request.

    Args:
        endpoint: Full URL for the scale API call.
        timeout: HTTP request timeout in seconds.

    Returns:
        ScaleResult indicating success, timeout (504 or client-side), or failure.
    """
    try:
        response = requests.post(endpoint, verify=False, timeout=timeout)
        if response.status_code in (200, 201, 202):
            logger.info(f"Successfully scaled cluster via {endpoint} (status: {response.status_code})")
            return ScaleResult.SUCCESS
        if response.status_code == 504:
            logger.warning(f"Scale request timed out (504) via {endpoint}: {response.text[:200]}")
            return ScaleResult.TIMEOUT
        logger.warning(f"Failed to scale cluster via {endpoint} (status: {response.status_code}, response: {response.text[:200]})")
        return ScaleResult.FAILED
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        logger.warning(f"Scale request timed out for {endpoint}: {e}")
        return ScaleResult.TIMEOUT
    except Exception as e:
        logger.error(f"Error calling scale endpoint {endpoint}: {e}")
        return ScaleResult.FAILED


def scale_cluster(cluster_id: int, target_cardinality: int, flavour: str,
                   timeout: int = 30) -> ScaleResult:
    """Scale a single cluster to target cardinality.

    Args:
        cluster_id: The cluster ID to scale
        target_cardinality: Target number of VMs for the cluster
        flavour: Flavour to use for scaling
        timeout: HTTP request timeout in seconds

    Returns:
        ScaleResult indicating success, timeout, or failure.
    """
    logger.info(f"Scaling cluster {cluster_id} with flavour {flavour} to target cardinality {target_cardinality}")
    cluster_template = get_cluster_template(cluster_id)
    if not cluster_template:
        return ScaleResult.FAILED

    endpoint = construct_endpoint(cluster_template, flavour, target_cardinality)
    if not endpoint:
        logger.warning(f"Could not construct endpoint for cluster {cluster_id} (EDGE_CLUSTER_FRONTEND missing)")
        return ScaleResult.FAILED

    return call_scale_endpoint(endpoint, timeout=timeout)


def _execute_scale_phase(
    targets: dict[tuple[int, str], int],
    timeout: int,
) -> dict[tuple[int, str], ScaleResult]:
    """Execute a batch of scale operations in parallel.

    Args:
        targets: Mapping of (cluster_id, flavour) to target_cardinality.
        timeout: HTTP timeout in seconds for each request.

    Returns:
        Mapping of (cluster_id, flavour) to ScaleResult.
    """
    if not targets:
        return {}

    results: dict[tuple[int, str], ScaleResult] = {}
    max_workers = max(1, len(targets))

    def _do_scale(cid: int, flavour: str, card: int) -> tuple[int, str, ScaleResult]:
        return cid, flavour, scale_cluster(cid, card, flavour, timeout=timeout)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_do_scale, cid, flavour, card): (cid, flavour)
            for (cid, flavour), card in targets.items()
        }
        for future in as_completed(future_map):
            cid, flavour, result = future.result()
            results[(cid, flavour)] = result

    return results


def scale_clusters_and_update_db(
    n_vms: dict[int, int],
    allocs: dict,
    all_feasible_cluster_ids: set[int],
    cluster_lookup: dict[int, dict],
    current_cardinality: dict[int, int],
) -> int:
    """Scale clusters and update DB, executing all scale-UPs before scale-DOWNs.

    Clusters present in the optimizer solution are scaled to their optimizer n_vms.
    Clusters that are feasible but absent from the solution are scaled to 0.
    Scale-UP operations execute first (in parallel), and only after all complete
    do scale-DOWN operations execute (in parallel with a longer timeout).
    A scale-down that receives HTTP 504 is re-queued for retry at the next cycle.

    Args:
        n_vms: Cluster ID to target cardinality mapping (from optimizer)
        allocs: Composite ID (device_id:::flavour) to cluster ID mapping
        all_feasible_cluster_ids: All cluster IDs that were feasible for at least one device
        cluster_lookup: Cluster ID to OpenNebula template mapping
        current_cardinality: Cluster ID to current VM count mapping

    Returns:
        Total number of devices updated in database
    """
    global _pending_scaledowns

    logger.info("=== CLUSTER SCALING ===")

    if not allocs:
        logger.info("No device allocations, nothing to scale")
        return 0

    # Build scaling targets: clusters in the solution get their optimizer n_vms,
    # feasible clusters not in the solution get scaled to 0
    cluster_flavour_targets: dict[tuple[int, str], int] = {}

    for composite_id, cluster_id in allocs.items():
        if cluster_id in n_vms and ':::' in composite_id:
            flavour = composite_id.split(':::', 1)[1]
            key = (cluster_id, flavour)
            if key not in cluster_flavour_targets:
                target_cardinality = n_vms[cluster_id]
                cluster_flavour_targets[key] = target_cardinality
                logger.info(f"Cluster {cluster_id} (flavour {flavour}): optimizer n_vms={n_vms[cluster_id]}, target_cardinality={target_cardinality}")

    clusters_in_solution = {cid for _, cid in allocs.items()}

    for cluster_id in all_feasible_cluster_ids:
        if cluster_id not in clusters_in_solution:
            template = cluster_lookup.get(cluster_id, {})
            flavour = get_flavour_from_template(template)
            if flavour:
                key = (cluster_id, flavour)
                if key not in cluster_flavour_targets:
                    cluster_flavour_targets[key] = 0
                    logger.info(f"Cluster {cluster_id} (flavour {flavour}): not in solution, target_cardinality=0")
            else:
                logger.warning(f"Cluster {cluster_id}: not in solution but no FLAVOURS in template, skipping scale-down")

    # Merge pending scale-down retries (fresh optimizer decisions take priority)
    pending_snapshot = dict(_pending_scaledowns)
    _pending_scaledowns.clear()
    for key, target in pending_snapshot.items():
        if key not in cluster_flavour_targets:
            cluster_flavour_targets[key] = target
            cid, flavour = key
            logger.info(f"Cluster {cid} (flavour {flavour}): re-queued pending scale-down, target_cardinality={target}")

    if not cluster_flavour_targets:
        logger.info("No cluster-flavour combinations to scale")
        return 0

    # Split into scale-up and scale-down groups based on current cardinality
    scale_ups: dict[tuple[int, str], int] = {}
    scale_downs: dict[tuple[int, str], int] = {}
    for (cid, flavour), target in cluster_flavour_targets.items():
        current = current_cardinality.get(cid, 0)
        if target > current:
            scale_ups[(cid, flavour)] = target
        elif target < current:
            scale_downs[(cid, flavour)] = target

    logger.info(f"Scale operations: {len(scale_ups)} scale-UP, {len(scale_downs)} scale-DOWN")

    scaled_cluster_flavours: set[tuple[int, str]] = set()

    # Phase 1: execute all scale-UPs
    if scale_ups:
        logger.info(f"Phase 1: executing {len(scale_ups)} scale-UP operations")
        up_results = _execute_scale_phase(scale_ups, timeout=SCALE_UP_TIMEOUT_SECONDS)
        for key, result in up_results.items():
            if result == ScaleResult.SUCCESS:
                scaled_cluster_flavours.add(key)
            else:
                cid, flavour = key
                logger.warning(f"Scale-UP failed for cluster {cid} (flavour {flavour}): {result.value}")

    # Phase 2: execute all scale-DOWNs (only after all scale-UPs complete)
    if scale_downs:
        logger.info(f"Phase 2: executing {len(scale_downs)} scale-DOWN operations")
        down_results = _execute_scale_phase(scale_downs, timeout=SCALE_DOWN_TIMEOUT_SECONDS)
        for key, result in down_results.items():
            cid, flavour = key
            if result == ScaleResult.SUCCESS:
                scaled_cluster_flavours.add(key)
            elif result == ScaleResult.TIMEOUT:
                _pending_scaledowns[key] = scale_downs[key]
                logger.warning(
                    f"Scale-DOWN timed out for cluster {cid} (flavour {flavour}), "
                    f"re-queued for next cycle"
                )
            else:
                logger.warning(f"Scale-DOWN failed for cluster {cid} (flavour {flavour}): {result.value}")

    # Update DB only for successfully scaled (cluster_id, flavour) pairs that are in the solution
    total_updated = 0
    for cid, flavour in scaled_cluster_flavours:
        cluster_allocs = {
            dev_id: cluster_id for dev_id, cluster_id in allocs.items()
            if cluster_id == cid and dev_id.split(':::', 1)[1] == flavour
        }
        if cluster_allocs:
            updated = update_device_cluster_assignments(cluster_allocs)
            total_updated += updated
            if updated > 0:
                logger.info(f"Cluster {cid} ({flavour}) scaled successfully: {updated} devices updated")

    logger.info(f"Cluster scaling completed: {total_updated} total devices updated")
    if _pending_scaledowns:
        logger.info(f"Pending scale-down retries for next cycle: {len(_pending_scaledowns)}")
    return total_updated