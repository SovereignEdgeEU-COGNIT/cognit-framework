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

__all__ = ["PrometheusMonitoringAccessor"]

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import numpy as np

from .monitoring_accessor import BaseMonitoringAccessor
from .tsnumpy.index import TimeIndex
from .tsnumpy.timeseries import Timeseries

if TYPE_CHECKING:
    from .entity_uid import EntityUID
    from .metric_types import MetricAttributes
    from .monitoring_config import MonitoringConfig
    from .time import Period


class PrometheusMonitoringAccessor(BaseMonitoringAccessor):
    """
    Prometheus HTTP API monitoring accessor.

    Connects to Prometheus server and fetches metrics using
    the HTTP API (query and query_range endpoints).

    Parameters
    ----------
    config : MonitoringConfig
        Monitoring configuration with Prometheus backend settings.
    """

    def __init__(self, config: MonitoringConfig):
        super().__init__(config)

        # Import requests here to make it optional dependency
        try:
            import requests
        except ImportError:
            raise ImportError(
                "requests library is required for Prometheus accessor. "
                "Install it with: pip install requests"
            )

        self.requests = requests
        self.session = requests.Session()
        self.base_url = self.connection_params["url"].rstrip("/")
        self.timeout = self.connection_params.get("timeout", 30)

        # Handle authentication if provided
        auth = self.connection_params.get("auth")
        if auth:
            if auth.get("type") == "basic":
                self.session.auth = (auth["username"], auth["password"])
            elif auth.get("type") == "bearer":
                self.session.headers["Authorization"] = (
                    f"Bearer {auth['token']}"
                )

    def _build_prometheus_query(
        self, entity_uid: EntityUID, metric_attrs: MetricAttributes
    ) -> str:
        """
        Build Prometheus query string from entity and metric.

        Parameters
        ----------
        entity_uid : EntityUID
            Entity identifier.
        metric_attrs : MetricAttributes
            Metric attributes.

        Returns
        -------
        str
            Prometheus query string.
        """
        # Build metric name from template
        metric_template = self.schema.get(
            "metric_name_template", "{metric_name}"
        )
        metric_name = metric_template.format(
            metric_name=metric_attrs.name,
            entity_type=entity_uid.type,
        )

        # Build label selectors
        label_mappings = self.schema.get("label_mappings", {})
        labels = []

        if "entity_type" in label_mappings:
            label_key = label_mappings["entity_type"]
            labels.append(f'{label_key}="{entity_uid.type}"')

        if "entity_id" in label_mappings:
            label_key = label_mappings["entity_id"]
            labels.append(f'{label_key}="{entity_uid.id}"')

        # Add any custom labels from schema
        custom_labels = self.schema.get("custom_labels", {})
        for key, value in custom_labels.items():
            labels.append(f'{key}="{value}"')

        if labels:
            label_selector = ",".join(labels)
            query = f"{metric_name}{{{label_selector}}}"
        else:
            query = metric_name

        return query

    def _fetch_raw_data(
        self,
        entity_uid: EntityUID,
        metric_attrs: MetricAttributes,
        time_range: Period,
    ) -> Timeseries | None:
        """
        Fetch raw data from Prometheus.

        Parameters
        ----------
        entity_uid : EntityUID
            Entity identifier.
        metric_attrs : MetricAttributes
            Metric attributes.
        time_range : Period
            Time range to fetch.

        Returns
        -------
        Timeseries or None
            Raw timeseries data, or None if no data found.
        """
        query = self._build_prometheus_query(entity_uid, metric_attrs)

        # Execute range query
        params = {
            "query": query,
            "start": time_range.start.timestamp(),
            "end": time_range.end.timestamp(),
            "step": self.behavior.get("step", "60s"),
        }

        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/query_range",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except self.requests.exceptions.RequestException as e:
            raise ConnectionError(
                f"Failed to fetch data from Prometheus: {e}"
            )

        # Parse response
        return self._parse_prometheus_response(
            data, entity_uid, metric_attrs
        )

    def _parse_prometheus_response(
        self,
        data: dict,
        entity_uid: EntityUID,
        metric_attrs: MetricAttributes,
    ) -> Timeseries | None:
        """
        Parse Prometheus JSON response into Timeseries.

        Parameters
        ----------
        data : dict
            Prometheus JSON response.
        entity_uid : EntityUID
            Entity identifier.
        metric_attrs : MetricAttributes
            Metric attributes.

        Returns
        -------
        Timeseries or None
            Parsed timeseries, or None if no data.
        """
        if data.get("status") != "success":
            error = data.get("error", "Unknown error")
            raise ValueError(f"Prometheus query failed: {error}")

        result = data.get("data", {}).get("result", [])
        if not result:
            return None

        # Extract first result (should be single series for our query)
        series = result[0]
        values_data = series.get("values", [])

        if not values_data:
            return None

        # Parse timestamps and values
        timestamps = [
            datetime.fromtimestamp(float(row[0]), timezone.utc)
            for row in values_data
        ]
        values = []
        for row in values_data:
            try:
                val = float(row[1])
                # Handle Prometheus special values
                if val == float("inf") or val == float("-inf"):
                    val = np.nan
                values.append(val)
            except (ValueError, TypeError):
                values.append(np.nan)

        values = np.array(values)

        return Timeseries(
            time_idx=TimeIndex(np.array(timestamps, dtype="object")),
            metric_idx=np.array([metric_attrs]),
            entity_uid_idx=np.array([entity_uid]),
            data=values.reshape((len(timestamps), 1, 1)),
        )
