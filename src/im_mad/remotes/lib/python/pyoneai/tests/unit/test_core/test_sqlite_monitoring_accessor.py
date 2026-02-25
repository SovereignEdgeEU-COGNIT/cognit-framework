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
from pyoneai.core.sqlite_monitoring_accessor import SQLiteMonitoringAccessor
from pyoneai.core.tsnumpy.index import TimeIndex
from pyoneai.core.tsnumpy.timeseries import Timeseries


class TestSQLiteMonitoringAccessor:
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup common test fixtures."""
        self.config = MonitoringConfig.opennebula_sqlite(
            db_path="/tmp/test.db"
        )
        self.entity_uid = EntityUID(EntityType.HOST, 1)
        self.metric_attrs = MetricAttributes(
            name="cpu", type=MetricType.GAUGE, dtype=Float()
        )

    def test_init(self):
        """Test SQLite accessor initialization."""
        accessor = SQLiteMonitoringAccessor(self.config)

        assert accessor._db_path == "/tmp/test.db"
        assert accessor.config == self.config

    def test_type(self):
        """Test accessor type is OBSERVATION."""
        accessor = SQLiteMonitoringAccessor(self.config)
        assert accessor.type == AccessorType.OBSERVATION

    def test_fetch_raw_data_calls_timeseries_read(
        self, mocker: MockerFixture
    ):
        """Test that _fetch_raw_data delegates to Timeseries.read_from_database."""
        accessor = SQLiteMonitoringAccessor(self.config)

        # Create mock timeseries
        timestamp = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        time_idx = TimeIndex(np.array([timestamp], dtype="object"))

        mock_ts = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_attrs]),
            entity_uid_idx=np.array([self.entity_uid]),
            data=np.array([[[50.0]]]),
        )

        # Mock Timeseries.read_from_database
        mock_read = mocker.patch(
            "pyoneai.core.tsnumpy.timeseries.Timeseries.read_from_database",
            return_value=mock_ts,
        )

        # Create time range
        start = timestamp - timedelta(hours=1)
        end = timestamp
        time_range = Period(slice(start, end, timedelta(minutes=1)))

        # Call _fetch_raw_data
        result = accessor._fetch_raw_data(
            self.entity_uid, self.metric_attrs, time_range
        )

        # Verify Timeseries.read_from_database was called
        mock_read.assert_called_once_with(
            path="/tmp/test.db",
            metric_attrs=self.metric_attrs,
            entity_uid=self.entity_uid,
            time=time_range,
        )
        assert result == mock_ts

    def test_fetch_raw_data_returns_none_when_no_data(
        self, mocker: MockerFixture
    ):
        """Test that _fetch_raw_data returns None when no data found."""
        accessor = SQLiteMonitoringAccessor(self.config)

        # Mock Timeseries.read_from_database to return None
        mocker.patch(
            "pyoneai.core.tsnumpy.timeseries.Timeseries.read_from_database",
            return_value=None,
        )

        # Create time range
        timestamp = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        time_range = Period(
            slice(timestamp, timestamp + timedelta(hours=1), timedelta(minutes=1))
        )

        # Call _fetch_raw_data
        result = accessor._fetch_raw_data(
            self.entity_uid, self.metric_attrs, time_range
        )

        assert result is None

    def test_get_timeseries_integration(self, mocker: MockerFixture):
        """Test full get_timeseries flow."""
        accessor = SQLiteMonitoringAccessor(self.config)

        # Create mock timeseries
        timestamps = [
            datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2025, 1, 1, 10, 1, 0, tzinfo=timezone.utc),
        ]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        mock_ts = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_attrs]),
            entity_uid_idx=np.array([self.entity_uid]),
            data=np.array([[[50.0]], [[60.0]]]),
        )

        # Mock the read method
        mocker.patch(
            "pyoneai.core.tsnumpy.timeseries.Timeseries.read_from_database",
            return_value=mock_ts,
        )

        # Create time range
        period = Period(
            slice(timestamps[0], timestamps[-1], timedelta(minutes=1))
        )

        # Call get_timeseries (which will call _fetch_raw_data and post-process)
        result = accessor.get_timeseries(
            self.entity_uid, self.metric_attrs, period
        )

        # Verify result
        assert result is not None
        # Note: post-processing may modify the result

    def test_custom_db_path(self):
        """Test accessor with custom database path."""
        custom_config = MonitoringConfig.opennebula_sqlite(
            db_path="/custom/path/to/db.sqlite"
        )
        accessor = SQLiteMonitoringAccessor(custom_config)

        assert accessor._db_path == "/custom/path/to/db.sqlite"

    def test_inherits_base_behavior(self):
        """Test that SQLite accessor inherits base class behavior."""
        accessor = SQLiteMonitoringAccessor(self.config)

        # Should have inherited behavior parameters
        assert hasattr(accessor, "_monitor_interval")
        assert hasattr(accessor, "_window_size")
        assert hasattr(accessor, "_fill_gaps_tol")

        # Check default values
        assert accessor._monitor_interval == 60
        assert accessor._window_size == 5
