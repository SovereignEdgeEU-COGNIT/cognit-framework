import sys
from modules.mock_pyoneai import setup_mock
from modules.config import DB_PATH, DB_CLEANUP_DAYS, COGNIT_FRONTEND_SRC

# Mock pyoneai before importing cognit_conf (required by db_manager)
setup_mock()

# Temporarily add cognit-frontend/src to import db_manager
original_path = sys.path.copy()
sys.path.insert(0, COGNIT_FRONTEND_SRC)

try:
    import db_manager
finally:
    sys.path = original_path

# Initialize DBManager once (singleton pattern ensures single instance)
_db = db_manager.DBManager(DB_PATH=DB_PATH, DB_CLEANUP_DAYS=DB_CLEANUP_DAYS)

def get_device_assignments() -> list[dict]:
    """Retrieve all device assignments from database."""
    return _db.get_all_device_assignments()

def cleanup_old_records() -> int:
    """Trigger cleanup of old records in the database.
    
    Returns:
        Number of records deleted
    """
    # Access the cleanup method through the singleton instance

    from modules.logger import get_logger
    logger = get_logger(__name__)
    
    try:
        # Get count before cleanup
        before_count = len(_db.get_all_device_assignments())
        
        # Trigger cleanup
        _db.cleanup_old_records()
        
        # Get count after cleanup
        after_count = len(_db.get_all_device_assignments())
        
        deleted_count = before_count - after_count

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old device assignments")
        
        return deleted_count
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        return 0

def update_device_cluster_assignments(allocations: dict[str, int]) -> int:
    """Update device cluster assignments in database only when changed.
    
    Args:
        allocations: Dict mapping composite_id (device_id:::flavour) to cluster_id
    
    Returns:
        Number of updated assignments
    """
    updated_count = 0
    # Get all assignments to verify updates
    all_assignments = _db.get_all_device_assignments()
    assignment_lookup = {f"{a['device_id']}:::{a['flavour']}": a for a in all_assignments}
    
    for composite_id, new_cluster_id in allocations.items():
        # Parse composite_id (format: device_id:::flavour)
        if ':::' not in composite_id:
            continue  # Skip invalid composite IDs
        
        device_id, flavour = composite_id.split(':::', 1)
        
        # Get current assignment
        current = assignment_lookup.get(composite_id)
        
        if current and current['cluster_id'] != new_cluster_id:
            _db.update_device_assignment(
                device_id=device_id,
                cluster_id=new_cluster_id,
                flavour=flavour,
                app_req_id=current['app_req_id'],
                app_req_json=current['app_req_json']
            )
            updated_count += 1

    return updated_count
