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

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS device_cluster_assignment (
                    device_id TEXT PRIMARY KEY,
                    cluster_id INTEGER NOT NULL,
                    flavour TEXT NOT NULL,
                    last_seen TIMESTAMP NOT NULL,
                    app_req_id INTEGER NOT NULL,
                    app_req_json TEXT NOT NULL,
                    estimated_load REAL DEFAULT 1.0
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

    def get_device_assignment(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve device cluster assignment from database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT device_id, cluster_id, flavour, last_seen, app_req_id, app_req_json, estimated_load '
                'FROM device_cluster_assignment WHERE device_id = ?',
                (device_id,)
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
        app_req_json: Dict[str, Any],
        estimated_load: float = 1.0
    ) -> None:
        """Insert new device cluster assignment into database."""
        with self._write_lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                app_req_json_str = json.dumps(app_req_json)

                cursor.execute(
                    'INSERT INTO device_cluster_assignment '
                    '(device_id, cluster_id, flavour, last_seen, app_req_id, app_req_json, estimated_load) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (device_id, cluster_id, flavour, now, app_req_id, app_req_json_str, estimated_load)
                )


    def update_device_assignment(
        self,
        device_id: str,
        cluster_id: int,
        flavour: str,
        app_req_id: int,
        app_req_json: Dict[str, Any]
    ) -> None:
        """Update existing device cluster assignment."""
        with self._write_lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                app_req_json_str = json.dumps(app_req_json)

                cursor.execute(
                    'UPDATE device_cluster_assignment '
                    'SET cluster_id = ?, flavour = ?, last_seen = ?, app_req_id = ?, app_req_json = ?'
                    'WHERE device_id = ?',
                    (cluster_id, flavour, now, app_req_id, app_req_json_str, device_id)
                )


    def update_last_seen(self, device_id: str) -> None:
        """Update last_seen timestamp for a device assignment."""
        with self._write_lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()

                cursor.execute(
                    'UPDATE device_cluster_assignment SET last_seen = ? WHERE device_id = ?',
                    (now, device_id)
                )

    def update_estimated_load(self, device_id: str, estimated_load: float) -> None:
        """Update only estimated_load for a device assignment."""
        with self._write_lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'UPDATE device_cluster_assignment SET estimated_load = ? WHERE device_id = ?',
                    (estimated_load, device_id)
                )

    def get_distinct_device_count(self) -> int:
        """Get count of distinct device_ids in the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(DISTINCT device_id) FROM device_cluster_assignment')
            result = cursor.fetchone()
            return result[0] if result else 0

    def get_all_device_ids(self) -> List[str]:
        """Get all device_ids from the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT DISTINCT device_id FROM device_cluster_assignment')
            rows = cursor.fetchall()
            return [row[0] for row in rows] if rows else []

    def get_device_count_by_flavour(self, flavour: str) -> int:
        """Get count of devices with a specific flavour (case-insensitive)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COUNT(*) FROM device_cluster_assignment WHERE LOWER(flavour) = LOWER(?)',
                (flavour,)
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    def get_device_ids_by_flavour(self, flavour: str) -> List[str]:
        """Get all device_ids with a specific flavour (case-insensitive)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT device_id FROM device_cluster_assignment WHERE LOWER(flavour) = LOWER(?)',
                (flavour,)
            )
            rows = cursor.fetchall()
            return [row[0] for row in rows] if rows else []
