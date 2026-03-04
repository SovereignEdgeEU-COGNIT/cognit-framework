# Copyright 2002-2024, OpenNebula Project, OpenNebula Systems
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

__all__ = ["OpenNebulaDBMonitoringAccessor"]

import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import numpy as np

from .monitoring_accessor import BaseMonitoringAccessor
from .tsnumpy.index import TimeIndex
from .tsnumpy.timeseries import Timeseries

if TYPE_CHECKING:
    from .entity_uid import EntityUID
    from .metric_types import MetricAttributes
    from .monitoring_config import MonitoringConfig
    from .time import Period


class OpenNebulaDBMonitoringAccessor(BaseMonitoringAccessor):
    """
    OpenNebula database monitoring accessor.

    Reads monitoring data directly from OpenNebula's database
    where monitoring information is stored as XML in the body column.
    Supports both SQLite (development/evaluation) and MySQL/MariaDB (production).

    Database Schema:
    ----------------
    - host_monitoring: (hid, last_mon_time, body)
    - vm_monitoring: (vmid, last_poll, body)

    The body column contains XML with nested metric values in CDATA sections.

    Parameters
    ----------
    config : MonitoringConfig
        Monitoring configuration with OpenNebula DB backend settings.

    Connection Examples
    -------------------
    SQLite:
    {
        "backend": "opennebula_db",
        "connection": {
            "type": "sqlite",
            "db_path": "/var/lib/one/one.db"
        }
    }

    MySQL/MariaDB:
    {
        "backend": "opennebula_db",
        "connection": {
            "type": "mysql",
            "host": "localhost",
            "port": 3306,
            "database": "opennebula",
            "user": "oneadmin",
            "password": "secret"
        }
    }

    Schema Configuration Example
    ----------------------------
    {
        "table_mapping": {
            "host": "host_monitoring",
            "virtualmachine": "vm_monitoring"
        },
        "id_column_mapping": {
            "host": "hid",
            "virtualmachine": "vmid"
        },
        "timestamp_column_mapping": {
            "host": "last_mon_time",
            "virtualmachine": "last_poll"
        },
        "metric_xpath_mapping": {
            # Host metrics
            "used_cpu": "CAPACITY/USED_CPU",
            "free_cpu": "CAPACITY/FREE_CPU",
            "used_memory": "CAPACITY/USED_MEMORY",
            "free_memory": "CAPACITY/FREE_MEMORY",
            "netrx": "SYSTEM/NETRX",
            "nettx": "SYSTEM/NETTX",
            "power": "SYSTEM/POWER",
            # VM metrics
            "cpu": "CPU",
            "memory": "MEMORY",
            "netrx": "NETRX",
            "nettx": "NETTX",
            "diskrdbytes": "DISKRDBYTES",
            "diskwrbytes": "DISKWRBYTES"
        }
    }
    """

    def __init__(self, config: MonitoringConfig):
        super().__init__(config)

        # Determine database type
        self._db_type = self.connection_params.get("type", "sqlite")

        # Initialize connection based on type
        if self._db_type == "sqlite":
            self._db_path = self.connection_params["db_path"]
            self._connection = None  # SQLite uses per-query connections
        elif self._db_type == "mysql":
            # MySQL/MariaDB requires pymysql or mysqlclient
            try:
                import pymysql
                self._mysql_module = pymysql
            except ImportError:
                raise ImportError(
                    "pymysql library is required for MySQL/MariaDB support. "
                    "Install it with: pip install pymysql"
                )
            
            self._mysql_config = {
                "host": self.connection_params.get("host", "localhost"),
                "port": self.connection_params.get("port", 3306),
                "database": self.connection_params.get("database", "opennebula"),
                "user": self.connection_params.get("user", "oneadmin"),
                "password": self.connection_params.get("password", ""),
                "charset": "utf8mb4",
            }
        else:
            raise ValueError(
                f"Unsupported database type: {self._db_type}. "
                "Supported types: sqlite, mysql"
            )

        # Get schema mappings with OpenNebula defaults
        self._table_mapping = self.schema.get("table_mapping", {
            "host": "host_monitoring",
            "virtualmachine": "vm_monitoring",
        })
        self._id_column_mapping = self.schema.get("id_column_mapping", {
            "host": "hid",
            "virtualmachine": "vmid",
        })
        self._timestamp_column_mapping = self.schema.get(
            "timestamp_column_mapping", {
                "host": "last_mon_time",
                "virtualmachine": "last_poll",
            }
        )
        self._metric_xpath_mapping = self.schema.get(
            "metric_xpath_mapping", {}
        )

    def _execute_query(
        self, query: str, params: list[Any]
    ) -> list[tuple]:
        """
        Execute query on appropriate database backend.

        Parameters
        ----------
        query : str
            SQL query string.
        params : list
            Query parameters.

        Returns
        -------
        list[tuple]
            Query results.
        """
        if self._db_type == "sqlite":
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.cursor()
                return cursor.execute(query, params).fetchall()
        elif self._db_type == "mysql":
            conn = self._mysql_module.connect(**self._mysql_config)
            try:
                cursor = conn.cursor()
                cursor.execute(query, params)
                return cursor.fetchall()
            finally:
                conn.close()

    def _parse_xml_metric_value(
        self, xml_body: str, xpath: str
    ) -> float | None:
        """
        Parse metric value from XML body using XPath.

        Handles CDATA sections commonly used in OpenNebula XML.

        Parameters
        ----------
        xml_body : str
            XML content from body column.
        xpath : str
            XPath to the metric in the XML (e.g., "CAPACITY/USED_CPU").

        Returns
        -------
        float or None
            Parsed metric value or None if not found/parseable.

        Examples
        --------
        >>> xml = '<MONITORING><CAPACITY><USED_CPU><![CDATA[24]]></USED_CPU></CAPACITY></MONITORING>'
        >>> _parse_xml_metric_value(xml, "CAPACITY/USED_CPU")
        24.0
        """
        try:
            root = ET.fromstring(xml_body)
            
            # Navigate through the XPath
            element = root.find(xpath)
            if element is not None:
                # Handle CDATA content (text attribute contains CDATA value)
                value_text = element.text
                if value_text:
                    # Strip whitespace and parse
                    return float(value_text.strip())
            return None
        except (ET.ParseError, ValueError, TypeError):
            return None

    def _get_metric_xpath(self, metric_name: str) -> str:
        """
        Get XPath for a metric from schema mapping.

        Parameters
        ----------
        metric_name : str
            Name of the metric.

        Returns
        -------
        str
            XPath to the metric in OpenNebula XML.

        Raises
        ------
        ValueError
            If metric is not mapped in schema configuration.
        """
        xpath = self._metric_xpath_mapping.get(metric_name)
        if not xpath:
            raise ValueError(
                f"Metric '{metric_name}' not mapped in schema. "
                f"Available metrics: {list(self._metric_xpath_mapping.keys())}"
            )
        return xpath

    def _fetch_raw_data(
        self,
        entity_uid: EntityUID,
        metric_attrs: MetricAttributes,
        time_range: Period,
    ) -> Timeseries | None:
        """
        Fetch raw data from OpenNebula database.

        Queries the appropriate monitoring table based on entity type,
        parses XML bodies, and extracts metric values.

        Parameters
        ----------
        entity_uid : EntityUID
            Entity identifier (host or virtualmachine).
        metric_attrs : MetricAttributes
            Metric attributes containing name and type.
        time_range : Period
            Time range to fetch (already expanded with tolerance).

        Returns
        -------
        Timeseries or None
            Raw timeseries data, or None if no data found.

        Raises
        ------
        ValueError
            If entity type is not supported or metric not mapped.
        """
        # Get entity type (host or virtualmachine)
        entity_type = entity_uid.type.value

        # Get table and column names from schema mapping
        table_name = self._table_mapping.get(entity_type)
        if not table_name:
            raise ValueError(
                f"Entity type '{entity_type}' not supported. "
                f"Available types: {list(self._table_mapping.keys())}"
            )

        id_column = self._id_column_mapping[entity_type]
        timestamp_column = self._timestamp_column_mapping[entity_type]

        # Get XPath for the metric
        xpath = self._get_metric_xpath(metric_attrs.name)

        # Build query (use ? for both SQLite and MySQL with pymysql)
        start_ts = int(time_range.start.timestamp())
        end_ts = int(time_range.end.timestamp())

        # MySQL uses %s, SQLite uses ?
        placeholder = "%s" if self._db_type == "mysql" else "?"

        query = f"""
            SELECT {timestamp_column}, body
            FROM {table_name}
            WHERE {id_column} = {placeholder}
              AND {timestamp_column} BETWEEN {placeholder} AND {placeholder}
            ORDER BY {timestamp_column} ASC
        """

        # Execute query
        rows = self._execute_query(query, [entity_uid.id, start_ts, end_ts])

        if not rows:
            return None

        # Parse XML data
        timestamps = []
        values = []

        for timestamp, xml_body in rows:
            value = self._parse_xml_metric_value(xml_body, xpath)
            if value is not None:
                timestamps.append(
                    datetime.fromtimestamp(timestamp, timezone.utc)
                )
                values.append(value)

        if not timestamps:
            return None

        values_array = np.array(values)

        return Timeseries(
            time_idx=TimeIndex(np.array(timestamps, dtype="object")),
            metric_idx=np.array([metric_attrs]),
            entity_uid_idx=np.array([entity_uid]),
            data=values_array.reshape((len(timestamps), 1, 1)),
        )
