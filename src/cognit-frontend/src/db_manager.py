import json
import sqlite3
import threading
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, Dict, Any, List
import cognit_conf as conf
from cognit_logger import get_logger

logger = get_logger(__name__)

class DBManager:
    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls, DB_PATH: str = None, DB_CLEANUP_DAYS: int = None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DBManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, DB_PATH: str = None, DB_CLEANUP_DAYS: int = None):
        with DBManager._lock:
            if DBManager._initialized:
                return
            
            self.DB_PATH = DB_PATH if DB_PATH is not None else conf.DB_PATH
            self.DB_CLEANUP_DAYS = DB_CLEANUP_DAYS if DB_CLEANUP_DAYS is not None else conf.DB_CLEANUP_DAYS
            self._write_lock = threading.Lock()
            
            # Ensure database directory exists
            db_dir = os.path.dirname(self.DB_PATH)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
            
            self.init_db()
            self.cleanup_old_records()
            DBManager._initialized = True

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.DB_PATH)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Initialize SQLite database and create device_cluster_assignment table if it doesn't exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check if table exists and has the correct composite primary key
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='device_cluster_assignment'
            """)
            table_exists = cursor.fetchone() is not None

            if table_exists:
                # Check if the table has composite primary key by examining the schema
                cursor.execute("PRAGMA table_info(device_cluster_assignment)")
                columns = cursor.fetchall()
                # Check if both device_id and flavour are part of primary key
                # In PRAGMA table_info, pk column is 0 for non-pk, 1+ for pk columns
                pk_columns = [col[1] for col in columns if col[5] > 0]  # col[5] is pk, col[1] is name
                
                if 'device_id' in pk_columns and 'flavour' in pk_columns and len(pk_columns) == 2:
                    # Table already has correct composite primary key
                    return
                else:
                    # Table exists but has wrong schema, drop and recreate
                    logger.info("Table exists with old schema, recreating with composite primary key")
                    cursor.execute("DROP TABLE device_cluster_assignment")

            # Create table with composite primary key
            cursor.execute('''
                CREATE TABLE device_cluster_assignment (
                    device_id TEXT NOT NULL,
                    cluster_id INTEGER NOT NULL,
                    flavour TEXT NOT NULL,
                    last_seen TIMESTAMP NOT NULL,
                    app_req_id INTEGER NOT NULL,
                    app_req_json TEXT NOT NULL,
                    estimated_load REAL DEFAULT 1.0,
                    PRIMARY KEY (device_id, flavour)
                )
            ''')


    def cleanup_old_records(self) -> None:
        """Delete records older than configured days on initialization."""
        with self._write_lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM device_cluster_assignment "
                    "WHERE last_seen <= datetime('now', '-' || ? || ' days')",
                    (self.DB_CLEANUP_DAYS,)
                )
                deleted_count = cursor.rowcount
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} old device assignments (>{self.DB_CLEANUP_DAYS} days)")

    def get_device_assignment(self, device_id: str, flavour: str) -> Optional[Dict[str, Any]]:
        """Retrieve device cluster assignment from database.

        Args:
            device_id: The device identifier
            flavour: The device flavour

        Returns:
            Dictionary with assignment data or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT device_id, cluster_id, flavour, last_seen, app_req_id, app_req_json, estimated_load '
                'FROM device_cluster_assignment WHERE device_id = ? AND flavour = ?',
                (device_id, flavour)
            )
            row = cursor.fetchone()

            if row:
                return {
                    'device_id': row[0],
                    'cluster_id': row[1],
                    'flavour': row[2],
                    'last_seen': row[3],
                    'app_req_id': row[4],
                    'app_req_json': json.loads(row[5]) if row[5] else {},
                    'estimated_load': row[6]
                }
            return None


    def insert_device_assignment(
        self,
        device_id: str,
        cluster_id: int,
        flavour: str,
        app_req_id: int,
        app_req_json: Dict[str, Any]
    ) -> None:
        """Insert new device cluster assignment into database.

        Scales the OneFlow FaaS role for the target cluster by +1 before
        writing, while holding the write lock to serialise concurrent
        scale requests.

        Args:
            device_id: The device identifier
            cluster_id: The assigned cluster identifier
            flavour: The device flavour
            app_req_id: The application requirement identifier
            app_req_json: Application requirements as JSON
        """
        with self._write_lock:
            import oneflow_scaler
            if oneflow_scaler.scale_for_new_device(cluster_id):
                logger.info(f"Scaled OneFlow service for cluster {cluster_id}")
            else:
                logger.error(f"Failed to scale OneFlow service for cluster {cluster_id}")
                

            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                app_req_json_str = json.dumps(app_req_json)
                cursor.execute(
                    'INSERT INTO device_cluster_assignment '
                    '(device_id, cluster_id, flavour, last_seen, app_req_id, app_req_json)'
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (device_id, cluster_id, flavour, now, app_req_id, app_req_json_str)
                )


    def update_device_assignment(
        self,
        device_id: str,
        cluster_id: int,
        flavour: str,
        app_req_id: int,
        app_req_json: Dict[str, Any]
    ) -> None:
        """Update existing device cluster assignment.

        Args:
            device_id: The device identifier
            cluster_id: The assigned cluster identifier
            flavour: The device flavour
            app_req_id: The application requirement identifier
            app_req_json: Application requirements as JSON
        """
        with self._write_lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                app_req_json_str = json.dumps(app_req_json)

                cursor.execute(
                    'UPDATE device_cluster_assignment '
                    'SET cluster_id = ?, flavour = ?, last_seen = ?, app_req_id = ?, app_req_json = ?'
                    'WHERE device_id = ? AND flavour = ?',
                    (cluster_id, flavour, now, app_req_id, app_req_json_str, device_id, flavour)
                )


    def update_last_seen(self, device_id: str, flavour: str) -> None:
        """Update last_seen timestamp for a device assignment.
        
        Args:
            device_id: The device identifier
            flavour: The device flavour
        """
        with self._write_lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()

                cursor.execute(
                    'UPDATE device_cluster_assignment SET last_seen = ? WHERE device_id = ? AND flavour = ?',
                    (now, device_id, flavour)
                )

    def update_estimated_load(self, device_id: str, flavour: str, estimated_load: float) -> None:
        """Update only estimated_load for a device assignment.
        
        Args:
            device_id: The device identifier
            flavour: The device flavour
            estimated_load: New estimated load value
        """
        with self._write_lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'UPDATE device_cluster_assignment SET estimated_load = ? WHERE device_id = ? AND flavour = ?',
                    (estimated_load, device_id, flavour)
                )

    def get_distinct_device_count(self) -> int:
        """Get count of distinct device_ids in the database.
        
        Returns:
            Number of unique devices registered in the system
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(DISTINCT device_id) FROM device_cluster_assignment')
            result = cursor.fetchone()
            return result[0] if result else 0

    def get_all_device_ids(self) -> List[str]:
        """Get all device_ids from the database.
        
        Returns:
            List of all device_id values (empty list if no devices)
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT DISTINCT device_id FROM device_cluster_assignment')
            rows = cursor.fetchall()
            return [row[0] for row in rows] if rows else []

    def get_all_device_assignments(self) -> List[Dict[str, Any]]:
        """Get all device assignments from the database.
        
        Returns:
            List of all device assignment dictionaries (empty list if no assignments)
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT device_id, cluster_id, flavour, last_seen, app_req_id, app_req_json, estimated_load '
                'FROM device_cluster_assignment'
            )
            rows = cursor.fetchall()
            return [
                {
                    'device_id': row[0],
                    'cluster_id': row[1],
                    'flavour': row[2],
                    'last_seen': row[3],
                    'app_req_id': row[4],
                    'app_req_json': json.loads(row[5]) if row[5] else {},
                    'estimated_load': row[6]
                }
                for row in rows
            ] if rows else []
