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

__all__ = ["MonitoringConfig"]

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class MonitoringConfig:
    """
    Complete specification for connecting to and reading metrics from
    a monitoring system.

    Parameters
    ----------
    backend : str
        Type of monitoring backend (sqlite, postgresql, prometheus, influxdb, custom).
    connection : dict
        Backend-specific connection parameters.
        Examples:
            - SQLite: {"db_path": "/path/to/db.sqlite"}
            - PostgreSQL: {"host": "localhost", "port": 5432, "database": "metrics", ...}
            - Prometheus: {"url": "http://localhost:9090", "timeout": 30}
    schema : dict
        Schema mapping configuration (how to map EntityUID + MetricAttributes to queries).
        Examples:
            - SQLite: {
                "table_pattern": "{entity_uid}_{metric_name}_monitoring",
                "timestamp_column": "TIMESTAMP",
                "value_column": "VALUE",
                "timestamp_format": "unix_epoch"
              }
            - Prometheus: {
                "metric_name_template": "{metric_name}",
                "label_mappings": {"entity_type": "job", "entity_id": "instance"}
              }
    behavior : dict
        Behavior parameters (monitor_interval, window_size, etc.).
    """

    backend: str
    connection: dict[str, Any]
    schema: dict[str, Any]
    behavior: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config: dict) -> MonitoringConfig:
        """
        Factory method for creating MonitoringConfig from a dictionary.

        Parameters
        ----------
        config : dict
            Configuration dictionary.

        Returns
        -------
        MonitoringConfig
            Monitoring configuration instance.
        """
        return cls(**config)

    @classmethod
    def opennebula_sqlite(
        cls, db_path: str, **kwargs
    ) -> MonitoringConfig:
        """
        Preset for OpenNebula SQLite monitoring.

        Parameters
        ----------
        db_path : str
            Path to SQLite database file.
        **kwargs
            Additional behavior parameters.

        Returns
        -------
        MonitoringConfig
            Configured monitoring instance for OpenNebula SQLite.
        """
        return cls(
            backend="sqlite",
            connection={"db_path": db_path},
            schema={
                "table_pattern": "{entity_uid}_{metric_name}_monitoring",
                "timestamp_column": "TIMESTAMP",
                "value_column": "VALUE",
                "timestamp_format": "unix_epoch",
            },
            behavior={
                "monitor_interval": 60,
                "window_size": 5,
                "fill_gaps_multiplier": 2.0,
                **kwargs,
            },
        )

    @classmethod
    def opennebula_prometheus(cls, url: str, **kwargs) -> MonitoringConfig:
        """
        Preset for OpenNebula Prometheus monitoring.

        Parameters
        ----------
        url : str
            Prometheus server URL.
        **kwargs
            Additional behavior parameters.

        Returns
        -------
        MonitoringConfig
            Configured monitoring instance for OpenNebula Prometheus.
        """
        return cls(
            backend="prometheus",
            connection={"url": url, "timeout": 30},
            schema={
                "metric_name_template": "opennebula_{metric_name}",
                "label_mappings": {
                    "entity_type": "entity_type",
                    "entity_id": "entity_id",
                },
            },
            behavior={
                "monitor_interval": 60,
                "step": "60s",
                **kwargs,
            },
        )

    @classmethod
    def opennebula_db_sqlite(
        cls, db_path: str, **kwargs
    ) -> MonitoringConfig:
        """
        Preset for OpenNebula SQLite database monitoring.

        Parameters
        ----------
        db_path : str
            Path to OpenNebula one.db file.
        **kwargs
            Additional behavior parameters or metric mappings.

        Returns
        -------
        MonitoringConfig
            Configured monitoring instance for OpenNebula DB (SQLite).
        """
        # Extract metric_xpath_mapping from kwargs if provided
        metric_xpath_mapping = kwargs.pop("metric_xpath_mapping", {
            # Host metrics
            "used_cpu": "CAPACITY/USED_CPU",
            "free_cpu": "CAPACITY/FREE_CPU",
            "used_memory": "CAPACITY/USED_MEMORY",
            "free_memory": "CAPACITY/FREE_MEMORY",
            "netrx": "SYSTEM/NETRX",
            "nettx": "SYSTEM/NETTX",
            # VM metrics
            "cpu": "CPU",
            "memory": "MEMORY",
            "vm_netrx": "NETRX",
            "vm_nettx": "NETTX",
            "diskrdbytes": "DISKRDBYTES",
            "diskwrbytes": "DISKWRBYTES",
        })

        return cls(
            backend="opennebula_db",
            connection={"type": "sqlite", "db_path": db_path},
            schema={
                "table_mapping": {
                    "host": "host_monitoring",
                    "virtualmachine": "vm_monitoring",
                },
                "id_column_mapping": {
                    "host": "hid",
                    "virtualmachine": "vmid",
                },
                "timestamp_column_mapping": {
                    "host": "last_mon_time",
                    "virtualmachine": "last_poll",
                },
                "metric_xpath_mapping": metric_xpath_mapping,
            },
            behavior={
                "monitor_interval": 60,
                **kwargs,
            },
        )

    @classmethod
    def opennebula_db_mysql(
        cls,
        host: str = "localhost",
        port: int = 3306,
        database: str = "opennebula",
        user: str = "oneadmin",
        password: str = "",
        **kwargs
    ) -> MonitoringConfig:
        """
        Preset for OpenNebula MySQL/MariaDB database monitoring.

        Parameters
        ----------
        host : str
            MySQL server host.
        port : int
            MySQL server port.
        database : str
            Database name.
        user : str
            Database user.
        password : str
            Database password.
        **kwargs
            Additional behavior parameters or metric mappings.

        Returns
        -------
        MonitoringConfig
            Configured monitoring instance for OpenNebula DB (MySQL).
        """
        # Extract metric_xpath_mapping from kwargs if provided
        metric_xpath_mapping = kwargs.pop("metric_xpath_mapping", {
            # Host metrics
            "used_cpu": "CAPACITY/USED_CPU",
            "free_cpu": "CAPACITY/FREE_CPU",
            "used_memory": "CAPACITY/USED_MEMORY",
            "free_memory": "CAPACITY/FREE_MEMORY",
            "netrx": "SYSTEM/NETRX",
            "nettx": "SYSTEM/NETTX",
            # VM metrics
            "cpu": "CPU",
            "memory": "MEMORY",
            "vm_netrx": "NETRX",
            "vm_nettx": "NETTX",
            "diskrdbytes": "DISKRDBYTES",
            "diskwrbytes": "DISKWRBYTES",
        })

        return cls(
            backend="opennebula_db",
            connection={
                "type": "mysql",
                "host": host,
                "port": port,
                "database": database,
                "user": user,
                "password": password,
            },
            schema={
                "table_mapping": {
                    "host": "host_monitoring",
                    "virtualmachine": "vm_monitoring",
                },
                "id_column_mapping": {
                    "host": "hid",
                    "virtualmachine": "vmid",
                },
                "timestamp_column_mapping": {
                    "host": "last_mon_time",
                    "virtualmachine": "last_poll",
                },
                "metric_xpath_mapping": metric_xpath_mapping,
            },
            behavior={
                "monitor_interval": 60,
                **kwargs,
            },
        )

    def to_legacy_dict(self) -> dict:
        """
        Convert to legacy monitoring dict format for backward compatibility.

        Returns
        -------
        dict
            Legacy monitoring configuration dictionary.
        """
        if self.backend == "sqlite":
            return {
                "db_path": self.connection.get("db_path"),
                "timestamp_col": self.schema.get(
                    "timestamp_column", "TIMESTAMP"
                ),
                "value_col": self.schema.get("value_column", "VALUE"),
                "monitor_interval": self.behavior.get("monitor_interval", 60),
                "window_size": self.behavior.get("window_size", 5),
                "table_name_template": self.schema.get(
                    "table_pattern", "{entityUID}_{metric_name}_monitoring"
                ),
            }
        else:
            # For non-SQLite backends, return the full config
            return {
                "backend": self.backend,
                "connection": self.connection,
                "schema": self.schema,
                "behavior": self.behavior,
            }
