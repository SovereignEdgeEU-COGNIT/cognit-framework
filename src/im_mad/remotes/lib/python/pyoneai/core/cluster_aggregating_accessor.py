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

__all__ = ["ClusterAggregatingAccessor"]

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


class ClusterAggregatingAccessor(BaseMonitoringAccessor):
    """
    Monitoring accessor for OpenNebula clusters that aggregates host metrics.

    This accessor provides cluster-level metrics by dynamically querying and
    aggregating metrics from the hosts that compose each cluster (e.g. average
    CPU usage across hosts).

    The aggregation strategy depends on the metric type:
    - COUNTER: sum (total across hosts)
    - GAUGE: avg (average across hosts)
    - RATE: sum (total throughput)
    - Custom: can be specified via metric_attrs.aggregation_fn

    Configuration Schema
    --------------------
    {
        "backend": "cluster_aggregating",
        "connection": {
            "host_monitoring_config": <MonitoringConfig for host data>
        },
        "schema": {
            "cluster_topology": {
                <cluster_id>: [<host_id1>, <host_id2>, ...],
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
        Monitoring configuration with cluster aggregating backend.

    Examples
    --------
    >>> from pyoneai.core import MonitoringConfig, EntityUID, EntityType
    >>> # Configure host monitoring backend
    >>> host_config = MonitoringConfig.opennebula_db_sqlite(
    ...     db_path="/var/lib/one/one.db"
    ... )
    >>> # Configure cluster aggregating accessor
    >>> cluster_config = MonitoringConfig(
    ...     backend="cluster_aggregating",
    ...     connection={"host_monitoring_config": host_config},
    ...     schema={
    ...         "cluster_topology": {
    ...             1: [10, 11, 12],  # cluster 1 -> host IDs
    ...             2: [13, 14]      # cluster 2 -> host IDs
    ...         }
    ...     },
    ...     behavior={"monitor_interval": 60}
    ... )
    >>> accessor = ClusterAggregatingAccessor(cluster_config)
    """

    def __init__(self, config: MonitoringConfig):
        super().__init__(config)

        # Extract host monitoring config
        host_config = self.connection_params.get("host_monitoring_config")
        if not host_config:
            raise ValueError(
                "ClusterAggregatingAccessor requires 'host_monitoring_config' "
                "in connection parameters"
            )

        # Create underlying host accessor
        # Import here to avoid circular dependency
        from .monitoring_accessor_registry import MonitoringAccessorRegistry
        self._host_accessor = MonitoringAccessorRegistry.create(host_config)

        # Extract cluster topology: {cluster_id: [host_id, ...]}
        self._cluster_topology = self.schema.get("cluster_topology", {})

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

    def _get_hosts_for_entity(self, entity_uid: EntityUID) -> list[int]:
        """
        Get list of host IDs for a given cluster entity.

        Parameters
        ----------
        entity_uid : EntityUID
            Cluster entity identifier.

        Returns
        -------
        list[int]
            List of host IDs.

        Raises
        ------
        ValueError
            If entity type is not CLUSTER, or if cluster is not found in
            topology.
        """
        if entity_uid.type != EntityType.CLUSTER:
            raise ValueError(
                f"ClusterAggregatingAccessor only supports CLUSTER entities, "
                f"got: {entity_uid.type}"
            )

        cluster_id = entity_uid.id
        if cluster_id not in self._cluster_topology:
            raise ValueError(
                f"Cluster {cluster_id} not found in topology. "
                f"Available clusters: {list(self._cluster_topology.keys())}"
            )

        return self._cluster_topology[cluster_id]

    def _aggregate_timeseries(
        self,
        timeseries_list: list[Timeseries],
        aggregation_fn: str,
        target_entity_uid: EntityUID,
        metric_attrs: MetricAttributes,
    ) -> Timeseries | None:
        """
        Aggregate multiple host timeseries into a single timeseries.

        Parameters
        ----------
        timeseries_list : list[Timeseries]
            List of timeseries from different hosts.
        aggregation_fn : str
            Aggregation function name.
        target_entity_uid : EntityUID
            Target cluster entity UID for the result.
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

        # Combine all host timeseries into one multi-entity timeseries
        # Each host becomes an entity in the entity dimension
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
        Fetch and aggregate host metrics for cluster entities.

        Parameters
        ----------
        entity_uid : EntityUID
            Cluster entity identifier.
        metric_attrs : MetricAttributes
            Metric attributes.
        time_range : Period
            Time range to fetch (already expanded with tolerance).

        Returns
        -------
        Timeseries or None
            Aggregated timeseries data, or None if no data found.
        """
        # Get host IDs for this cluster
        host_ids = self._get_hosts_for_entity(entity_uid)

        if not host_ids:
            return None

        # Determine aggregation function
        agg_fn = self._get_aggregation_function(metric_attrs)

        # Fetch timeseries for each host
        host_timeseries = []
        for host_id in host_ids:
            host_uid = EntityUID(type=EntityType.HOST, id=host_id)
            try:
                # Use underlying host accessor to fetch raw data
                ts = self._host_accessor._fetch_raw_data(
                    host_uid, metric_attrs, time_range
                )
                if ts is not None:
                    host_timeseries.append(ts)
            except Exception as e:
                # Log warning but continue with other hosts
                # In production, use proper logging
                print(
                    f"Warning: Failed to fetch data for host {host_id}: {e}"
                )
                continue

        # Aggregate all host timeseries
        return self._aggregate_timeseries(
            host_timeseries, agg_fn, entity_uid, metric_attrs
        )

    def update_cluster_topology(
        self,
        cluster_id: int,
        host_ids: list[int],
    ) -> None:
        """
        Update or add cluster topology configuration.

        This allows dynamic updates to the cluster composition without
        recreating the accessor.

        Parameters
        ----------
        cluster_id : int
            Cluster identifier.
        host_ids : list[int]
            List of host IDs in the cluster.

        Examples
        --------
        >>> accessor.update_cluster_topology(
        ...     cluster_id=1,
        ...     host_ids=[10, 11, 12, 13]
        ... )
        """
        self._cluster_topology[cluster_id] = host_ids

    def get_cluster_topology(
        self, cluster_id: int | None = None
    ) -> dict | list[int]:
        """
        Get current cluster topology configuration.

        Parameters
        ----------
        cluster_id : int or None, optional
            Specific cluster ID, or None to return all clusters.

        Returns
        -------
        dict or list[int]
            If cluster_id is None, full topology {cluster_id: [host_ids]}.
            Else list of host IDs for that cluster, or [] if not found.
        """
        if cluster_id is None:
            return self._cluster_topology
        return self._cluster_topology.get(cluster_id, [])
