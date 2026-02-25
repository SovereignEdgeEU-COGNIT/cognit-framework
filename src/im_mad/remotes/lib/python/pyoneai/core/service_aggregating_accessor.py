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

__all__ = ["ServiceAggregatingAccessor"]

from typing import TYPE_CHECKING, Callable

import numpy as np

from .entity_uid import EntityType, EntityUID
from .metric_types import MetricType
from .monitoring_accessor import BaseMonitoringAccessor
from .tsnumpy.index import TimeIndex
from .tsnumpy.timeseries import Axis, Timeseries

if TYPE_CHECKING:
    from .metric_types import MetricAttributes
    from .monitoring_config import MonitoringConfig
    from .time import Period


class ServiceAggregatingAccessor(BaseMonitoringAccessor):
    """
    Monitoring accessor for OneFlow services that aggregates VM metrics.

    This accessor provides service-level and role-level metrics by dynamically
    querying and aggregating metrics from the VMs that compose each service role.

    The aggregation strategy depends on the metric type:
    - COUNTER: sum (total across VMs)
    - GAUGE: avg (average across VMs)
    - RATE: sum (total throughput)
    - Custom: can be specified via metric_attrs.aggregation_fn

    Configuration Schema
    --------------------
    {
        "backend": "service_aggregating",
        "connection": {
            "vm_monitoring_config": <MonitoringConfig for VM data>
        },
        "schema": {
            "service_topology": {
                <service_id>: {
                    <role_name>: [<vm_id1>, <vm_id2>, ...],
                    ...
                },
                ...
            }
        },
        "behavior": {
            "monitor_interval": 60,
            ...
        }
    }

    Parameters
    ----------
    config : MonitoringConfig
        Monitoring configuration with service aggregating backend.

    Examples
    --------
    >>> from pyoneai.core import MonitoringConfig, EntityUID, EntityType
    >>> # Configure VM monitoring backend
    >>> vm_config = MonitoringConfig.opennebula_db_sqlite(
    ...     db_path="/var/lib/one/one.db"
    ... )
    >>> # Configure service aggregating accessor
    >>> service_config = MonitoringConfig(
    ...     backend="service_aggregating",
    ...     connection={"vm_monitoring_config": vm_config},
    ...     schema={
    ...         "service_topology": {
    ...             123: {  # service_id
    ...                 "frontend": [1, 2, 3],  # VM IDs in frontend role
    ...                 "backend": [4, 5, 6]    # VM IDs in backend role
    ...             }
    ...         }
    ...     },
    ...     behavior={"monitor_interval": 60}
    ... )
    >>> accessor = ServiceAggregatingAccessor(service_config)
    """

    def __init__(self, config: MonitoringConfig):
        super().__init__(config)

        # Extract VM monitoring config
        vm_config = self.connection_params.get("vm_monitoring_config")
        if not vm_config:
            raise ValueError(
                "ServiceAggregatingAccessor requires 'vm_monitoring_config' "
                "in connection parameters"
            )

        # Create underlying VM accessor
        # Import here to avoid circular dependency
        from .monitoring_accessor_registry import MonitoringAccessorRegistry
        self._vm_accessor = MonitoringAccessorRegistry.create(vm_config)

        # Extract service topology: {service_id: {role_name: [vm_ids]}}
        self._service_topology = self.schema.get("service_topology", {})

        # Aggregation function mapping
        self._agg_functions: dict[str, Callable] = {
            "sum": lambda ts: ts.sum(axis=Axis.ENTITY, keepdims=True),
            "avg": lambda ts: ts.mean(axis=Axis.ENTITY, keepdims=True),
            "mean": lambda ts: ts.mean(axis=Axis.ENTITY, keepdims=True),
            "min": lambda ts: ts.min(axis=Axis.ENTITY, keepdims=True),
            "max": lambda ts: ts.max(axis=Axis.ENTITY, keepdims=True),
            "median": lambda ts: ts.median(axis=Axis.ENTITY, keepdims=True),
        }

    def _get_aggregation_function(
        self, metric_attrs: MetricAttributes
    ) -> str:
        """
        Determine aggregation function based on metric attributes.

        Parameters
        ----------
        metric_attrs : MetricAttributes
            Metric attributes containing type and optional aggregation_fn.

        Returns
        -------
        str
            Aggregation function name ('sum', 'avg', 'min', 'max', 'median').
        """
        # Use explicit aggregation if specified
        if metric_attrs.aggregation_fn:
            return metric_attrs.aggregation_fn

        # Default aggregation based on metric type
        type_defaults = {
            MetricType.COUNTER: "sum",
            MetricType.GAUGE: "avg",
            MetricType.RATE: "sum",
            MetricType.HISTOGRAM: "avg",
        }

        return type_defaults.get(metric_attrs.type, "avg")

    def _get_vms_for_entity(self, entity_uid: EntityUID) -> list[int]:
        """
        Get list of VM IDs for a given service or service role entity.

        Parameters
        ----------
        entity_uid : EntityUID
            Service or service role entity identifier.

        Returns
        -------
        list[int]
            List of VM IDs.

        Raises
        ------
        ValueError
            If entity type is not SERVICE or SERVICE_ROLE, or if entity
            is not found in topology.
        """
        if entity_uid.type == EntityType.SERVICE:
            # For service: aggregate all VMs from all roles
            service_id = entity_uid.id
            if service_id not in self._service_topology:
                raise ValueError(
                    f"Service {service_id} not found in topology. "
                    f"Available services: {list(self._service_topology.keys())}"
                )

            # Flatten all VM IDs from all roles
            vm_ids = []
            for role_vms in self._service_topology[service_id].values():
                vm_ids.extend(role_vms)
            return vm_ids

        elif entity_uid.type == EntityType.SERVICE_ROLE:
            # For service role: parse service_id and role_name from entity ID
            # Expected format: "service_id_role_name" or integer with separate mapping
            # For simplicity, we'll use string format: "123_frontend"
            entity_id_str = str(entity_uid.id)

            # Try to parse as "service_id_role_name"
            # Use rsplit with maxsplit=1 to handle role names with underscores
            parts = entity_id_str.rsplit("_", 1)
            if len(parts) < 2:
                raise ValueError(
                    f"Invalid service role entity ID format: {entity_id_str}. "
                    "Expected format: 'service_id_role_name'"
                )

            try:
                service_id = int(parts[0])
                role_name = parts[1]
            except ValueError:
                raise ValueError(
                    f"Cannot parse service_id from entity ID: {entity_id_str}"
                )

            if service_id not in self._service_topology:
                raise ValueError(
                    f"Service {service_id} not found in topology"
                )

            if role_name not in self._service_topology[service_id]:
                raise ValueError(
                    f"Role '{role_name}' not found in service {service_id}. "
                    f"Available roles: {list(self._service_topology[service_id].keys())}"
                )

            return self._service_topology[service_id][role_name]

        else:
            raise ValueError(
                f"ServiceAggregatingAccessor only supports SERVICE and "
                f"SERVICE_ROLE entities, got: {entity_uid.type}"
            )

    def _aggregate_timeseries(
        self,
        timeseries_list: list[Timeseries],
        aggregation_fn: str,
        target_entity_uid: EntityUID,
        metric_attrs: MetricAttributes,
    ) -> Timeseries | None:
        """
        Aggregate multiple VM timeseries into a single timeseries.

        Parameters
        ----------
        timeseries_list : list[Timeseries]
            List of timeseries from different VMs.
        aggregation_fn : str
            Aggregation function name.
        target_entity_uid : EntityUID
            Target service/role entity UID for the result.
        metric_attrs : MetricAttributes
            Metric attributes.

        Returns
        -------
        Timeseries or None
            Aggregated timeseries, or None if no data.
        """
        if not timeseries_list:
            return None

        # Filter out None values
        valid_ts = [ts for ts in timeseries_list if ts is not None]
        if not valid_ts:
            return None

        # Stack all timeseries along entity dimension
        # All should have same time and metric dimensions
        first_ts = valid_ts[0]

        # Combine all VM timeseries into one multi-entity timeseries
        # Each VM becomes an entity in the entity dimension
        all_entity_uids = []
        all_data = []

        for ts in valid_ts:
            # Each timeseries should have shape (time, 1, 1) for single metric/entity
            all_entity_uids.extend(ts._entity_idx.values)
            # Keep entity dimension, will aggregate later
            all_data.append(ts._data)

        # Concatenate along entity axis (axis=2)
        combined_data = np.concatenate(all_data, axis=2)

        # Create combined timeseries
        combined_ts = Timeseries(
            time_idx=first_ts._time_idx,
            metric_idx=first_ts._metric_idx.values,
            entity_uid_idx=np.array(all_entity_uids),
            data=combined_data,
        )

        # Apply aggregation function
        agg_func = self._agg_functions.get(aggregation_fn)
        if not agg_func:
            raise ValueError(
                f"Unsupported aggregation function: {aggregation_fn}. "
                f"Supported: {list(self._agg_functions.keys())}"
            )

        # Aggregate across entity dimension (axis=2)
        aggregated_ts = agg_func(combined_ts)

        # Create new timeseries with correct entity UID
        # The aggregation returns a Timeseries with shape (time, metric, 1)
        result = Timeseries(
            time_idx=aggregated_ts._time_idx,
            metric_idx=aggregated_ts._metric_idx.values,
            entity_uid_idx=np.array([target_entity_uid]),
            data=aggregated_ts._data,
        )

        return result

    def _fetch_raw_data(
        self,
        entity_uid: EntityUID,
        metric_attrs: MetricAttributes,
        time_range: Period,
    ) -> Timeseries | None:
        """
        Fetch and aggregate VM metrics for service/role entities.

        Parameters
        ----------
        entity_uid : EntityUID
            Service or service role entity identifier.
        metric_attrs : MetricAttributes
            Metric attributes.
        time_range : Period
            Time range to fetch (already expanded with tolerance).

        Returns
        -------
        Timeseries or None
            Aggregated timeseries data, or None if no data found.
        """
        # Get VM IDs for this service/role
        vm_ids = self._get_vms_for_entity(entity_uid)

        if not vm_ids:
            return None

        # Determine aggregation function
        agg_fn = self._get_aggregation_function(metric_attrs)

        # Fetch timeseries for each VM
        vm_timeseries = []
        for vm_id in vm_ids:
            vm_uid = EntityUID(type=EntityType.VIRTUAL_MACHINE, id=vm_id)
            try:
                # Use underlying VM accessor to fetch raw data
                ts = self._vm_accessor._fetch_raw_data(
                    vm_uid, metric_attrs, time_range
                )
                if ts is not None:
                    vm_timeseries.append(ts)
            except Exception as e:
                # Log warning but continue with other VMs
                # In production, use proper logging
                print(
                    f"Warning: Failed to fetch data for VM {vm_id}: {e}"
                )
                continue

        # Aggregate all VM timeseries
        return self._aggregate_timeseries(
            vm_timeseries, agg_fn, entity_uid, metric_attrs
        )

    def update_service_topology(
        self,
        service_id: int,
        roles: dict[str, list[int]],
    ) -> None:
        """
        Update or add service topology configuration.

        This allows dynamic updates to the service composition without
        recreating the accessor.

        Parameters
        ----------
        service_id : int
            Service identifier.
        roles : dict[str, list[int]]
            Mapping of role names to lists of VM IDs.

        Examples
        --------
        >>> accessor.update_service_topology(
        ...     service_id=123,
        ...     roles={
        ...         "frontend": [1, 2, 3, 7],  # Added VM 7
        ...         "backend": [4, 5, 6]
        ...     }
        ... )
        """
        self._service_topology[service_id] = roles

    def get_service_topology(
        self, service_id: int | None = None
    ) -> dict:
        """
        Get current service topology configuration.

        Parameters
        ----------
        service_id : int or None, optional
            Specific service ID, or None to return all services.

        Returns
        -------
        dict
            Service topology configuration.
        """
        if service_id is None:
            return self._service_topology
        return self._service_topology.get(service_id, {})
