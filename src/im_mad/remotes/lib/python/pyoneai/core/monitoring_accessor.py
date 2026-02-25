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

__all__ = ["BaseMonitoringAccessor"]

import abc
from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np

from .base_accessor import AccessorType, BaseAccessor
from .metric_types import MetricType
from .time import Instant, Period
from .tsnumpy.index import TimeIndex
from .tsnumpy.timeseries import Timeseries

if TYPE_CHECKING:
    from .entity_uid import EntityUID
    from .metric_types import MetricAttributes
    from .monitoring_config import MonitoringConfig


class BaseMonitoringAccessor(BaseAccessor, abc.ABC):
    """
    Base class for all monitoring system accessors.

    Provides common functionality for time window expansion,
    post-processing (gap filling, resampling, interpolation),
    and metric type handling.

    Parameters
    ----------
    config : MonitoringConfig
        Monitoring configuration containing backend, connection,
        schema, and behavior parameters.
    """

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self.connection_params = config.connection
        self.schema = config.schema
        self.behavior = config.behavior

        # Extract common behavior parameters
        self._monitor_interval = self.behavior.get("monitor_interval", 60)
        self._window_size = self.behavior.get("window_size", 5)
        self._fill_gaps_tol = self.behavior.get(
            "fill_gaps_multiplier", 2.0
        ) * self._monitor_interval

    @property
    def type(self) -> AccessorType:
        return AccessorType.OBSERVATION

    def _expand_time_window(self, time: Instant | Period) -> Period:
        """
        Expand time window with tolerance to ensure data capture.

        Parameters
        ----------
        time : Instant or Period
            Original time specification.

        Returns
        -------
        Period
            Expanded time period with tolerance.
        """
        tolerance = timedelta(seconds=self._monitor_interval // 2)

        if isinstance(time, Period):
            return Period(
                slice(
                    time.start - tolerance,
                    time.end + tolerance,
                    time.resolution,
                )
            )
        elif isinstance(time, Instant):
            return Period(
                slice(
                    time.value - tolerance,
                    time.value + tolerance,
                    tolerance,
                )
            )

    def _postprocess_timeseries(
        self,
        ts: Timeseries | None,
        metric_attrs: MetricAttributes,
        entity_uid: EntityUID,
        time: Instant | Period,
    ) -> Timeseries:
        """
        Apply post-processing to raw timeseries data.

        Handles counter restoration, gap filling, resampling,
        interpolation, and rate calculation.

        Parameters
        ----------
        ts : Timeseries or None
            Raw timeseries data.
        metric_attrs : MetricAttributes
            Metric attributes.
        entity_uid : EntityUID
            Entity identifier.
        time : Instant or Period
            Requested time range.

        Returns
        -------
        Timeseries
            Post-processed timeseries.
        """
        # Handle empty result
        if ts is None:
            return Timeseries(
                time_idx=time,
                metric_idx=np.array([metric_attrs]),
                entity_uid_idx=np.array([entity_uid]),
                data=np.full((len(time), 1, 1), np.nan),
            )

        # Restore counter if metric is counter type
        if metric_attrs.type == MetricType.COUNTER:
            ts = ts.restore_counter()

        # Fill gaps for Period queries
        if isinstance(time, Period):
            if np.any(np.isnan(ts.values)) or np.any(
                np.diff(ts._time_idx.values)
                > self._fill_gaps_tol * timedelta(seconds=self._monitor_interval)
            ):
                ts = ts.fill_gaps(ts._time_idx.frequency.total_seconds())

        # Resample/interpolate if needed
        if TimeIndex(time.values).frequency > timedelta(
            seconds=self._monitor_interval
        ):
            ts = ts.resample(TimeIndex(time.values).frequency)
            ts = ts.interpolate(TimeIndex(time.values))
        else:
            if len(ts._time_idx) >= 2:
                ts_p = Period(
                    slice(
                        ts._time_idx.values[0],
                        ts._time_idx.values[-1],
                        ts._time_idx.frequency,
                    )
                )
                ts = ts.interpolate(TimeIndex(ts_p.values), kind="nearest")

        # Handle instant queries
        if isinstance(time, Instant):
            return ts[time]

        # Apply rate operator if needed
        if metric_attrs.operator == "rate":
            ts = ts.rate()

        return ts.clip()

    @abc.abstractmethod
    def _fetch_raw_data(
        self,
        entity_uid: EntityUID,
        metric_attrs: MetricAttributes,
        time_range: Period,
    ) -> Timeseries | None:
        """
        Fetch raw data from the monitoring backend.

        This method must be implemented by concrete accessor classes.

        Parameters
        ----------
        entity_uid : EntityUID
            Entity identifier.
        metric_attrs : MetricAttributes
            Metric attributes.
        time_range : Period
            Time range to fetch (already expanded with tolerance).

        Returns
        -------
        Timeseries or None
            Raw timeseries data, or None if no data found.
        """
        raise NotImplementedError()

    def get_timeseries(
        self,
        entity_uid: EntityUID,
        metric_attrs: MetricAttributes,
        time: Instant | Period,
    ) -> Timeseries:
        """
        Retrieve timeseries data from the monitoring system.

        Parameters
        ----------
        entity_uid : EntityUID
            Entity identifier.
        metric_attrs : MetricAttributes
            Metric attributes.
        time : Instant or Period
            Time specification.

        Returns
        -------
        Timeseries
            Timeseries data for the given time period.
        """
        # 1. Expand time window
        query_time = self._expand_time_window(time)

        # 2. Fetch raw data (backend-specific)
        ts = self._fetch_raw_data(entity_uid, metric_attrs, query_time)

        # 3. Post-process
        return self._postprocess_timeseries(
            ts, metric_attrs, entity_uid, time
        )
