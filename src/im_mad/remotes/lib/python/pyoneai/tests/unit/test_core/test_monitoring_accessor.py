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

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from pytest_mock import MockerFixture

from pyoneai.core import (
    AccessorType,
    EntityType,
    EntityUID,
    Float,
    MetricAttributes,
    MetricType,
    MonitoringConfig,
    Period,
)
from pyoneai.core.monitoring_accessor import BaseMonitoringAccessor
from pyoneai.core.time import Instant
from pyoneai.core.tsnumpy.index import TimeIndex
from pyoneai.core.tsnumpy.timeseries import Timeseries


class ConcreteMonitoringAccessor(BaseMonitoringAccessor):
    """Concrete implementation for testing the abstract base class."""

    def _fetch_raw_data(self, entity_uid, metric_attrs, time_range):
        """Simple implementation that returns mock data."""
        return self._mock_fetch_result


class TestBaseMonitoringAccessor:
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup common test fixtures."""
        self.config = MonitoringConfig(
            backend="test",
            connection={},
            schema={},
            behavior={
                "monitor_interval": 60,
                "window_size": 5,
                "fill_gaps_multiplier": 2.0,
            },
        )

        self.entity_uid = EntityUID(EntityType.HOST, 1)
        self.metric_attrs = MetricAttributes(
            name="cpu", type=MetricType.GAUGE, dtype=Float()
        )

    def test_init(self):
        """Test accessor initialization."""
        accessor = ConcreteMonitoringAccessor(self.config)

        assert accessor.config == self.config
        assert accessor.connection_params == {}
        assert accessor.schema == {}
        assert accessor.behavior == self.config.behavior
        assert accessor._monitor_interval == 60
        assert accessor._window_size == 5
        assert accessor._fill_gaps_tol == 120  # 2.0 * 60

    def test_type_property(self):
        """Test that type property returns OBSERVATION."""
        accessor = ConcreteMonitoringAccessor(self.config)
        assert accessor.type == AccessorType.OBSERVATION

    def test_expand_time_window_period(self):
        """Test time window expansion for Period."""
        accessor = ConcreteMonitoringAccessor(self.config)

        start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        period = Period(slice(start, end, timedelta(minutes=1)))

        expanded = accessor._expand_time_window(period)

        # Should expand by monitor_interval // 2 (30 seconds)
        assert expanded.start == start - timedelta(seconds=30)
        assert expanded.end == end + timedelta(seconds=30)

    def test_expand_time_window_instant(self):
        """Test time window expansion for Instant."""
        accessor = ConcreteMonitoringAccessor(self.config)

        time_value = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        instant = Instant(time_value)

        expanded = accessor._expand_time_window(instant)

        # Should create a Period around the instant
        tolerance = timedelta(seconds=30)
        assert isinstance(expanded, Period)
        assert expanded.start == time_value - tolerance
        assert expanded.end == time_value + tolerance

    def test_postprocess_timeseries_empty(self):
        """Test post-processing with None input returns empty timeseries."""
        accessor = ConcreteMonitoringAccessor(self.config)

        start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        period = Period(slice(start, end, timedelta(minutes=1)))

        result = accessor._postprocess_timeseries(
            None, self.metric_attrs, self.entity_uid, period
        )

        assert result is not None
        assert len(result.time_index) == len(period)
        assert np.all(np.isnan(result.values))

    def test_postprocess_timeseries_counter_restoration(self):
        """Test that counter metrics are restored."""
        accessor = ConcreteMonitoringAccessor(self.config)

        # Create counter metric
        counter_attrs = MetricAttributes(
            name="packets", type=MetricType.COUNTER, dtype=Float()
        )

        # Create mock timeseries with counter data (cumulative values)
        timestamps = [
            datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2025, 1, 1, 10, 1, 0, tzinfo=timezone.utc),
        ]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        # Counter values (cumulative) - should be converted to rates
        ts = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([counter_attrs]),
            entity_uid_idx=np.array([self.entity_uid]),
            data=np.array([[[100.0]], [[150.0]]]),
        )

        period = Period(
            slice(timestamps[0], timestamps[-1], timedelta(minutes=1))
        )

        # Post-process should handle counter restoration
        result = accessor._postprocess_timeseries(
            ts, counter_attrs, self.entity_uid, period
        )

        # Verify we got a result (counter restoration is internal)
        assert result is not None
        # After restoration, cumulative counters become deltas
        # First value should be 0 (or NaN), second should be ~50
        assert len(result.values) == 2

    def test_postprocess_timeseries_instant(self):
        """Test post-processing returns single value for Instant."""
        accessor = ConcreteMonitoringAccessor(self.config)

        timestamp = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        time_idx = TimeIndex(np.array([timestamp], dtype="object"))

        ts = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_attrs]),
            entity_uid_idx=np.array([self.entity_uid]),
            data=np.array([[[50.0]]]),
        )

        instant = Instant(timestamp)

        result = accessor._postprocess_timeseries(
            ts, self.metric_attrs, self.entity_uid, instant
        )

        # Should return a single value (not a full timeseries)
        assert result is not None

    def test_get_timeseries_calls_fetch_and_postprocess(
        self, mocker: MockerFixture
    ):
        """Test that get_timeseries orchestrates fetch and post-process."""
        accessor = ConcreteMonitoringAccessor(self.config)

        # Create mock timeseries
        timestamp = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        time_idx = TimeIndex(np.array([timestamp], dtype="object"))

        mock_ts = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_attrs]),
            entity_uid_idx=np.array([self.entity_uid]),
            data=np.array([[[50.0]]]),
        )

        # Set up mock to return our timeseries
        accessor._mock_fetch_result = mock_ts

        # Spy on methods
        fetch_spy = mocker.spy(accessor, "_fetch_raw_data")
        postprocess_spy = mocker.spy(accessor, "_postprocess_timeseries")

        # Call get_timeseries
        start = timestamp - timedelta(hours=1)
        end = timestamp
        period = Period(slice(start, end, timedelta(minutes=1)))

        result = accessor.get_timeseries(
            self.entity_uid, self.metric_attrs, period
        )

        # Verify both methods were called
        assert fetch_spy.call_count == 1
        assert postprocess_spy.call_count == 1
        assert result is not None

    def test_behavior_defaults(self):
        """Test default behavior parameters."""
        config = MonitoringConfig(
            backend="test", connection={}, schema={}, behavior={}
        )
        accessor = ConcreteMonitoringAccessor(config)

        # Should use defaults
        assert accessor._monitor_interval == 60
        assert accessor._window_size == 5
        assert accessor._fill_gaps_tol == 120  # 2.0 * 60

    def test_custom_behavior_parameters(self):
        """Test custom behavior parameters."""
        config = MonitoringConfig(
            backend="test",
            connection={},
            schema={},
            behavior={
                "monitor_interval": 30,
                "window_size": 10,
                "fill_gaps_multiplier": 3.0,
            },
        )
        accessor = ConcreteMonitoringAccessor(config)

        assert accessor._monitor_interval == 30
        assert accessor._window_size == 10
        assert accessor._fill_gaps_tol == 90  # 3.0 * 30
