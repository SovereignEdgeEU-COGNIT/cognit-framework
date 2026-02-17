from typing import Any
from modules.logger import get_logger
from modules.cluster_scaler import scale_clusters_and_update_db

logger = get_logger(__name__)


def _format_device_requirements(device_id: str, app_req: dict[str, Any]) -> str:
    """Format device requirements for logging."""
    return (f"{device_id}: FLAVOUR={app_req.get('FLAVOUR')}, "
            f"IS_CONFIDENTIAL={app_req.get('IS_CONFIDENTIAL')}, "
            f"PROVIDERS={app_req.get('PROVIDERS')}, "
            f"GEOLOCATION={app_req.get('GEOLOCATION')}")


def _format_cluster_attributes(cluster_id: int, template: dict[str, Any]) -> str:
    """Format cluster attributes for logging."""
    return (f"Cluster {cluster_id}: FLAVOURS={template.get('FLAVOURS')}, "
            f"IS_CONFIDENTIAL={template.get('IS_CONFIDENTIAL')}, "
            f"PROVIDERS={template.get('PROVIDERS')}, "
            f"GEOLOCATION={template.get('GEOLOCATION')}, "
            f"CARBON_INTENSITY={template.get('CARBON_INTENSITY')}")

def create_devices_from_assignments(assignments: list[dict]) -> list:
    """Create Device objects for the optimization algorithm from database assignments with per-device feasible clusters.
    
    Uses composite identifier (device_id:::flavour) to uniquely identify each device-flavour combination.
    """
    from device_alloc import Device
    from modules.opennebula_adapter import get_feasible_clusters_for_device

    devices = []
    for assignment in assignments:
        device_id = assignment['device_id']
        flavour = assignment['flavour']
        load = assignment['estimated_load']
        app_req_id = assignment['app_req_id']

        # Create composite identifier: device_id:::flavour
        # Using ::: as separator (unlikely to appear in device_id or flavour)
        composite_id = f"{device_id}:::{flavour}"

        # Get feasible clusters for this device based on app requirements
        feasible_cluster_ids = get_feasible_clusters_for_device(app_req_id)

        device = Device(
            id=composite_id,
            load=load,
            capacity_load=load,
            cluster_ids=feasible_cluster_ids
        )
        devices.append(device)

    return devices

def optimize_device_assignments(devices: list, clusters: list) -> tuple:
    """Run optimization algorithm on devices and clusters."""
    from device_alloc import optimize_contention
    
    # Here we can call the optimizer method with multiple iterations and develop a logic to select the best result
    # For now, we just call the optimize_contention method with one (or max 2) iteration
    return optimize_contention(devices=devices, clusters=clusters)

def run_optimization_with_db_updates() -> tuple | None:
    """Run complete optimization cycle with devices database updates."""
    from modules.db_adapter import get_device_assignments, cleanup_old_records
    from modules.opennebula_adapter import get_cluster_pool, get_app_requirement

    try:
        # Clean up old records (older than DB_CLEANUP_DAYS)
        cleanup_old_records()
        
        assignments = get_device_assignments()
        
        logger.info("=== DEVICE REQUIREMENTS ===")
        valid_assignments = []
        for assignment in assignments:
            device_id = assignment['device_id']
            app_req_id = assignment['app_req_id']
            app_req = get_app_requirement(app_req_id)
            if not app_req:
                logger.info(f"Device {device_id}: Skipping device - app requirement {app_req_id} not found in OpenNebula")
                continue
            logger.info(_format_device_requirements(device_id, app_req))
            valid_assignments.append(assignment)
        
        if not valid_assignments:
            logger.warning("No devices with valid app requirements found. Skipping optimization.")
            return None
        
        devices = create_devices_from_assignments(valid_assignments)

        # Filter cluster pool to only include clusters that are feasible for at least one device
        all_feasible_cluster_ids = {cid for device in devices for cid in device.cluster_ids}
        
        logger.info(f"=== FEASIBILITY CHECK ===")
        logger.info(f"Total devices: {len(devices)}")
        logger.info(f"All feasible cluster IDs across all devices: {sorted(all_feasible_cluster_ids)}")

        clusters, cluster_lookup = get_cluster_pool()
        filtered_clusters = [c for c in clusters if c.id in all_feasible_cluster_ids]

        logger.info("=== CLUSTER OPTIMIZER ATTRIBUTES ===")
        for cluster in filtered_clusters:
            logger.info(str(cluster))

        logger.info("=== CLUSTER OPENNEBULA ATTRIBUTES ===")
        if not cluster_lookup:
            logger.warning("Could not fetch cluster information")
        else:
            for cluster in clusters:
                if cluster.id in cluster_lookup:
                    logger.info(_format_cluster_attributes(cluster.id, cluster_lookup[cluster.id]))
                else:
                    logger.warning(f"Cluster {cluster.id} not found in OpenNebula cluster pool")

        # Run optimization on filtered clusters
        result = optimize_device_assignments(devices, filtered_clusters)

        if result:
            allocs, n_vms, objective = result
            logger.info("=== OPTIMIZATION RESULT ===")
            for composite_id, cluster_id in allocs.items():
                # Parse composite_id (format: device_id:::flavour)
                device_id = composite_id.split(':::')[0] if ':::' in composite_id else composite_id
                flavour = composite_id.split(':::')[1] if ':::' in composite_id else None
                logger.info(f"Device {device_id} - Flavour {flavour} --> Cluster {cluster_id}")
            
            scale_clusters_and_update_db(n_vms, allocs)

        return result
    
    except Exception as e:
        logger.error(f"Optimization cycle failed: {e}", exc_info=True)
        return None
