import sys
from modules.config import (
    DOCUMENT_KEY, TEMPLATE_KEY, CLUSTER_POOL_KEY, CLUSTER_KEY, ID_KEY,
    ONE_XMLRPC_ENDPOINT, ONE_AUTH_USER, ONE_AUTH_PASSWORD
)
from typing import Any

def get_cluster_pool() -> tuple[list, dict[int, dict[str, Any]]]:
    """Retrieve cluster pool from OpenNebula and return both Cluster objects and raw cluster info lookup."""
    from device_alloc import create_cluster_pool, OnedServerProxy

    session = f"{ONE_AUTH_USER}:{ONE_AUTH_PASSWORD}"
    with OnedServerProxy(uri=ONE_XMLRPC_ENDPOINT, session=session) as oned_client:
        clusters = create_cluster_pool(oned_client)
        
        # Also fetch raw cluster info for template access
        cluster_info = oned_client('one.clusterpool.info')
        lookup = {}
        
        if CLUSTER_POOL_KEY in cluster_info and CLUSTER_KEY in cluster_info[CLUSTER_POOL_KEY]:
            clusters_data = cluster_info[CLUSTER_POOL_KEY][CLUSTER_KEY]
            clusters_list = clusters_data if isinstance(clusters_data, list) else [clusters_data]
            for c in clusters_list:
                cluster_id = int(c[ID_KEY])
                lookup[cluster_id] = c.get(TEMPLATE_KEY, {})
        
        return clusters, lookup

def get_app_requirement(app_req_id: int) -> dict:
    """Retrieve app requirement from OpenNebula by ID using OnedServerProxy."""
    from device_alloc import OnedServerProxy
    from device_alloc.xmlrpc_client import OneXMLRPCExistenceError
    from modules.logger import get_logger
    
    logger = get_logger(__name__)

    try:
        session = f"{ONE_AUTH_USER}:{ONE_AUTH_PASSWORD}"
        with OnedServerProxy(uri=ONE_XMLRPC_ENDPOINT, session=session) as client:
            # Get document info
            result = client('one.document.info', app_req_id)
            if result and DOCUMENT_KEY in result:
                # Parse the template which contains the app requirements
                template = result[DOCUMENT_KEY].get(TEMPLATE_KEY, {})
                return template
    except OneXMLRPCExistenceError as e:
        logger.debug(
            f"App requirement {app_req_id} not found in OpenNebula database. "
            f"This app_req_id was most probably deleted when device_runtime stopped."
        )
        return {}
    except Exception as e:
        logger.error(f"Failed to fetch app requirement {app_req_id}: {e}")
        return {}

def get_feasible_clusters_for_device(app_req_id: int) -> list[int]:
    """Get feasible cluster IDs for a device based on its app requirements using clusters_ids_get."""
    # Get fresh app requirements from OpenNebula
    app_req = get_app_requirement(app_req_id)
    if not app_req:
        return []

    # Extract filtering parameters
    geolocation = app_req.get('GEOLOCATION')
    flavour = app_req.get('FLAVOUR')
    is_confidential = app_req.get('IS_CONFIDENTIAL')
    providers = app_req.get('PROVIDERS')
    
    # Use clusters_ids_get from cognit-frontend
    from modules.config import COGNIT_FRONTEND_SRC
    
    # Clean up sys.path to avoid conflicts with /usr/lib/one/python
    # TODO: Temporary fix to avoid conflicts with /usr/lib/one/python
    original_path = sys.path.copy()
    sys.path = [p for p in sys.path if '/usr/lib/one/python' not in p]
    sys.path.insert(0, COGNIT_FRONTEND_SRC)
    
    try:
        import opennebula as on
        import pyone

        # Create pyone client using credentials from config
        session = f"{ONE_AUTH_USER}:{ONE_AUTH_PASSWORD}"
        one = pyone.OneServer(ONE_XMLRPC_ENDPOINT, session=session)
        
        feasible_cluster_ids = on.clusters_ids_get(
            one=one,
            geolocation=geolocation,
            flavour=flavour,
            is_confidential=is_confidential,
            providers=providers
        )

        return feasible_cluster_ids
    finally:
        sys.path = original_path
