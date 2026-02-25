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

__all__ = ["MonitoringAccessorRegistry"]

from typing import TYPE_CHECKING, Type

from .cluster_aggregating_accessor import ClusterAggregatingAccessor
from .monitoring_accessor import BaseMonitoringAccessor
from .opennebula_db_monitoring_accessor import OpenNebulaDBMonitoringAccessor
from .prometheus_monitoring_accessor import PrometheusMonitoringAccessor
from .service_aggregating_accessor import ServiceAggregatingAccessor
from .sqlite_monitoring_accessor import SQLiteMonitoringAccessor

if TYPE_CHECKING:
    from .monitoring_config import MonitoringConfig


class MonitoringAccessorRegistry:
    """
    Factory for creating monitoring accessors based on backend type.

    Maintains a registry of available accessor implementations and
    provides methods to create instances and register custom accessors.
    """

    _accessors: dict[str, Type[BaseMonitoringAccessor]] = {
        "cluster_aggregating": ClusterAggregatingAccessor,
        "sqlite": SQLiteMonitoringAccessor,
        "prometheus": PrometheusMonitoringAccessor,
        "opennebula_db": OpenNebulaDBMonitoringAccessor,
        "service_aggregating": ServiceAggregatingAccessor,
    }

    @classmethod
    def create(
        cls, config: MonitoringConfig
    ) -> BaseMonitoringAccessor:
        """
        Create appropriate accessor based on backend type.

        Parameters
        ----------
        config : MonitoringConfig
            Monitoring configuration.

        Returns
        -------
        BaseMonitoringAccessor
            Instantiated monitoring accessor.

        Raises
        ------
        ValueError
            If backend type is not supported.
        """
        accessor_class = cls._accessors.get(config.backend)
        if not accessor_class:
            raise ValueError(
                f"Unsupported backend: {config.backend}. "
                f"Available backends: {list(cls._accessors.keys())}"
            )
        return accessor_class(config)

    @classmethod
    def register(
        cls, backend: str, accessor_class: Type[BaseMonitoringAccessor]
    ) -> None:
        """
        Register custom accessor implementation.

        Parameters
        ----------
        backend : str
            Backend identifier.
        accessor_class : Type[BaseMonitoringAccessor]
            Accessor class to register.

        Examples
        --------
        >>> class MyCustomAccessor(BaseMonitoringAccessor):
        ...     def _fetch_raw_data(self, entity_uid, metric_attrs, time_range):
        ...         # Custom implementation
        ...         pass
        >>> MonitoringAccessorRegistry.register("mybackend", MyCustomAccessor)
        """
        cls._accessors[backend] = accessor_class

    @classmethod
    def list_backends(cls) -> list[str]:
        """
        List all registered backend types.

        Returns
        -------
        list[str]
            List of available backend identifiers.
        """
        return list(cls._accessors.keys())
