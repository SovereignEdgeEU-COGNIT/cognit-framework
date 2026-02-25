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
from pyoneai.core.cluster_aggregating_accessor import ClusterAggregatingAccessor
from pyoneai.core.tsnumpy.index import TimeIndex
from pyoneai.core.tsnumpy.timeseries import Timeseries


class TestClusterAggregatingAccessor:
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup common test fixtures."""
        # Create host monitoring config
        self.host_config = MonitoringConfig.opennebula_db_sqlite(
            db_path="/tmp/test.db"
        )

        # Define cluster topology
        self.cluster_topology = {
            1: [10, 11, 12],
            2: [13, 14],
            3: [15],
        }

        # Create cluster monitoring config
        self.cluster_config = MonitoringConfig(
            backend="cluster_aggregating",
            connection={"host_monitoring_config": self.host_config},
            schema={"cluster_topology": self.cluster_topology},
            behavior={"monitor_interval": 60},
        )

        # Entity UIDs
        self.cluster_uid_1 = EntityUID(EntityType.CLUSTER, 1)
        self.cluster_uid_2 = EntityUID(EntityType.CLUSTER, 2)
        self.host_uid_10 = EntityUID(EntityType.HOST, 10)

        # Metric attributes
        self.metric_cpu = MetricAttributes(
            name="used_cpu", type=MetricType.GAUGE, dtype=Float()
        )
        self.metric_memory = MetricAttributes(
            name="used_memory", type=MetricType.GAUGE, dtype=Float()
        )
        self.metric_counter = MetricAttributes(
            name="netrx", type=MetricType.COUNTER, dtype=Float()
        )
        self.metric_rate = MetricAttributes(
            name="throughput", type=MetricType.RATE, dtype=Float()
        )
        self.metric_custom_agg = MetricAttributes(
            name="used_cpu",
            type=MetricType.GAUGE,
            dtype=Float(),
            aggregation_fn="max"
        )

    # ==================== Initialization Tests ====================

    def test_init(self):
        """Test ClusterAggregatingAccessor initialization."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        assert accessor._cluster_topology == self.cluster_topology
        assert accessor._host_accessor is not None
        assert accessor.config == self.cluster_config
        assert len(accessor._agg_functions) == 6  # sum, avg, mean, min, max, median

    def test_init_missing_host_config(self):
        """Test initialization fails without host_monitoring_config."""
        bad_config = MonitoringConfig(
            backend="cluster_aggregating",
            connection={},
            schema={"cluster_topology": self.cluster_topology},
            behavior={},
        )

        with pytest.raises(ValueError, match="host_monitoring_config"):
            ClusterAggregatingAccessor(bad_config)

    def test_type(self):
        """Test accessor type is OBSERVATION."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)
        assert accessor.type == AccessorType.OBSERVATION

    # ==================== Aggregation Function Tests ====================

    def test_get_aggregation_function_gauge_default(self):
        """Test default aggregation for GAUGE is avg."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)
        agg_fn = accessor._get_aggregation_function(self.metric_cpu)
        assert agg_fn == "avg"

    def test_get_aggregation_function_counter_default(self):
        """Test default aggregation for COUNTER is sum."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)
        agg_fn = accessor._get_aggregation_function(self.metric_counter)
        assert agg_fn == "sum"

    def test_get_aggregation_function_rate_default(self):
        """Test default aggregation for RATE is sum."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)
        agg_fn = accessor._get_aggregation_function(self.metric_rate)
        assert agg_fn == "sum"

    def test_get_aggregation_function_custom(self):
        """Test custom aggregation function overrides default."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)
        agg_fn = accessor._get_aggregation_function(self.metric_custom_agg)
        assert agg_fn == "max"

    def test_get_aggregation_function_histogram(self):
        """Test default aggregation for HISTOGRAM is avg."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)
        metric_histogram = MetricAttributes(
            name="latency", type=MetricType.HISTOGRAM, dtype=Float()
        )
        agg_fn = accessor._get_aggregation_function(metric_histogram)
        assert agg_fn == "avg"

    # ==================== Host Lookup Tests ====================

    def test_get_hosts_for_cluster(self):
        """Test getting all hosts for a cluster."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)
        host_ids = accessor._get_hosts_for_entity(self.cluster_uid_1)

        assert host_ids == [10, 11, 12]

    def test_get_hosts_for_cluster_single_host(self):
        """Test getting hosts for cluster with single host."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)
        host_ids = accessor._get_hosts_for_entity(self.cluster_uid_2)

        assert host_ids == [13, 14]

    def test_get_hosts_cluster_not_found(self):
        """Test error when cluster not in topology."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)
        bad_cluster_uid = EntityUID(EntityType.CLUSTER, 999)

        with pytest.raises(ValueError, match="Cluster 999 not found"):
            accessor._get_hosts_for_entity(bad_cluster_uid)

    def test_get_hosts_invalid_entity_type(self):
        """Test error with invalid entity type."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        with pytest.raises(ValueError, match="only supports CLUSTER entities"):
            accessor._get_hosts_for_entity(self.host_uid_10)

    # ==================== Timeseries Aggregation Tests ====================

    def test_aggregate_timeseries_avg(self):
        """Test averaging multiple host timeseries."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        timestamps = [
            datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 10, 1, 0, tzinfo=timezone.utc),
        ]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        host_ts_list = []
        host_values = [50.0, 60.0, 70.0]

        for i, val in enumerate(host_values):
            host_uid = EntityUID(EntityType.HOST, 10 + i)
            ts = Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([self.metric_cpu]),
                entity_uid_idx=np.array([host_uid]),
                data=np.array([[[val]], [[val + 10]]]),
            )
            host_ts_list.append(ts)

        result = accessor._aggregate_timeseries(
            host_ts_list, "avg", self.cluster_uid_1, self.metric_cpu
        )

        assert result is not None
        assert result.shape == (2, 1, 1)

        expected_avg_t0 = np.mean([50.0, 60.0, 70.0])
        expected_avg_t1 = np.mean([60.0, 70.0, 80.0])

        assert np.isclose(result._data[0, 0, 0], expected_avg_t0)
        assert np.isclose(result._data[1, 0, 0], expected_avg_t1)
        assert result._entity_idx.values[0] == self.cluster_uid_1

    def test_aggregate_timeseries_sum(self):
        """Test summing multiple host timeseries."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        host_ts_list = []
        host_values = [100.0, 200.0, 300.0]

        for i, val in enumerate(host_values):
            host_uid = EntityUID(EntityType.HOST, 10 + i)
            ts = Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([self.metric_counter]),
                entity_uid_idx=np.array([host_uid]),
                data=np.array([[[val]]]),
            )
            host_ts_list.append(ts)

        result = accessor._aggregate_timeseries(
            host_ts_list, "sum", self.cluster_uid_1, self.metric_counter
        )

        assert result is not None
        assert np.isclose(result._data[0, 0, 0], 600.0)

    def test_aggregate_timeseries_max(self):
        """Test max aggregation across host timeseries."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        host_ts_list = []
        host_values = [50.0, 80.0, 60.0]

        for i, val in enumerate(host_values):
            host_uid = EntityUID(EntityType.HOST, 10 + i)
            ts = Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([self.metric_cpu]),
                entity_uid_idx=np.array([host_uid]),
                data=np.array([[[val]]]),
            )
            host_ts_list.append(ts)

        result = accessor._aggregate_timeseries(
            host_ts_list, "max", self.cluster_uid_1, self.metric_cpu
        )

        assert result is not None
        assert np.isclose(result._data[0, 0, 0], 80.0)

    def test_aggregate_timeseries_empty_list(self):
        """Test aggregation with empty timeseries list returns None."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        result = accessor._aggregate_timeseries(
            [], "avg", self.cluster_uid_1, self.metric_cpu
        )

        assert result is None

    def test_aggregate_timeseries_all_none(self):
        """Test aggregation with all None values returns None."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        result = accessor._aggregate_timeseries(
            [None, None, None], "avg", self.cluster_uid_1, self.metric_cpu
        )

        assert result is None

    def test_aggregate_timeseries_some_none(self):
        """Test aggregation filters out None values."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        host_uid_1 = EntityUID(EntityType.HOST, 10)
        ts1 = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_cpu]),
            entity_uid_idx=np.array([host_uid_1]),
            data=np.array([[[50.0]]]),
        )

        host_uid_2 = EntityUID(EntityType.HOST, 11)
        ts2 = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_cpu]),
            entity_uid_idx=np.array([host_uid_2]),
            data=np.array([[[70.0]]]),
        )

        result = accessor._aggregate_timeseries(
            [ts1, None, ts2], "avg", self.cluster_uid_1, self.metric_cpu
        )

        assert result is not None
        assert np.isclose(result._data[0, 0, 0], 60.0)

    def test_aggregate_timeseries_unsupported_function(self):
        """Test error with unsupported aggregation function."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        host_uid = EntityUID(EntityType.HOST, 10)
        ts = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_cpu]),
            entity_uid_idx=np.array([host_uid]),
            data=np.array([[[50.0]]]),
        )

        with pytest.raises(ValueError, match="Unsupported aggregation function"):
            accessor._aggregate_timeseries(
                [ts], "unsupported_fn", self.cluster_uid_1, self.metric_cpu
            )

    # ==================== Fetch Raw Data Tests ====================

    def test_fetch_raw_data_cluster(self, mocker: MockerFixture):
        """Test fetching and aggregating data for a cluster."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        def mock_fetch(host_uid, metric_attrs, time_range):
            return Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([metric_attrs]),
                entity_uid_idx=np.array([host_uid]),
                data=np.array([[[float(host_uid.id) * 10]]]),
            )

        mocker.patch.object(
            accessor._host_accessor,
            "_fetch_raw_data",
            side_effect=mock_fetch
        )

        time_range = Period(
            slice(timestamps[0], timestamps[0] + timedelta(hours=1), timedelta(minutes=1))
        )

        result = accessor._fetch_raw_data(
            self.cluster_uid_1, self.metric_cpu, time_range
        )

        assert result is not None
        # Hosts 10, 11, 12 with values 100, 110, 120 -> avg = 110
        expected_avg = np.mean([100.0, 110.0, 120.0])
        assert np.isclose(result._data[0, 0, 0], expected_avg)

    def test_fetch_raw_data_cluster_single_host(self, mocker: MockerFixture):
        """Test fetching data for cluster with single host."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)
        cluster_uid_3 = EntityUID(EntityType.CLUSTER, 3)

        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        def mock_fetch(host_uid, metric_attrs, time_range):
            return Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([metric_attrs]),
                entity_uid_idx=np.array([host_uid]),
                data=np.array([[[75.0]]]),
            )

        mocker.patch.object(
            accessor._host_accessor,
            "_fetch_raw_data",
            side_effect=mock_fetch
        )

        time_range = Period(
            slice(timestamps[0], timestamps[0] + timedelta(hours=1), timedelta(minutes=1))
        )

        result = accessor._fetch_raw_data(
            cluster_uid_3, self.metric_cpu, time_range
        )

        assert result is not None
        assert np.isclose(result._data[0, 0, 0], 75.0)

    def test_fetch_raw_data_no_hosts(self, mocker: MockerFixture):
        """Test fetching data for cluster with no hosts returns None."""
        empty_config = MonitoringConfig(
            backend="cluster_aggregating",
            connection={"host_monitoring_config": self.host_config},
            schema={"cluster_topology": {999: []}},
            behavior={},
        )
        accessor = ClusterAggregatingAccessor(empty_config)

        empty_cluster_uid = EntityUID(EntityType.CLUSTER, 999)
        time_range = Period(
            slice(
                datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
                timedelta(minutes=1)
            )
        )

        result = accessor._fetch_raw_data(
            empty_cluster_uid, self.metric_cpu, time_range
        )

        assert result is None

    def test_fetch_raw_data_host_failures_handled(self, mocker: MockerFixture):
        """Test that host fetch failures don't break aggregation."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        def mock_fetch_with_failures(host_uid, metric_attrs, time_range):
            if host_uid.id == 11:
                raise Exception("Host 11 connection failed")
            return Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([metric_attrs]),
                entity_uid_idx=np.array([host_uid]),
                data=np.array([[[float(host_uid.id) * 10]]]),
            )

        mock_print = mocker.patch("builtins.print")

        mocker.patch.object(
            accessor._host_accessor,
            "_fetch_raw_data",
            side_effect=mock_fetch_with_failures
        )

        time_range = Period(
            slice(timestamps[0], timestamps[0] + timedelta(hours=1), timedelta(minutes=1))
        )

        result = accessor._fetch_raw_data(
            self.cluster_uid_1, self.metric_cpu, time_range
        )

        assert result is not None
        # Hosts 10 and 12 only: (100 + 120) / 2 = 110
        expected_avg = np.mean([100.0, 120.0])
        assert np.isclose(result._data[0, 0, 0], expected_avg)
        mock_print.assert_called_once()
        assert "Warning" in str(mock_print.call_args)
        assert "host 11" in str(mock_print.call_args)

    # ==================== Topology Management Tests ====================

    def test_update_cluster_topology(self):
        """Test updating cluster topology."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        new_host_ids = [10, 11, 12, 20]
        accessor.update_cluster_topology(1, new_host_ids)

        topology = accessor.get_cluster_topology(1)
        assert topology == new_host_ids

    def test_update_cluster_topology_new_cluster(self):
        """Test adding a new cluster to topology."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        new_host_ids = [30, 31, 32]
        accessor.update_cluster_topology(100, new_host_ids)

        topology = accessor.get_cluster_topology(100)
        assert topology == new_host_ids

    def test_get_cluster_topology_specific(self):
        """Test getting topology for specific cluster."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        topology = accessor.get_cluster_topology(1)
        assert topology == [10, 11, 12]

    def test_get_cluster_topology_all(self):
        """Test getting all cluster topologies."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        all_topology = accessor.get_cluster_topology()
        assert 1 in all_topology
        assert 2 in all_topology
        assert 3 in all_topology
        assert all_topology[1] == [10, 11, 12]

    def test_get_cluster_topology_nonexistent(self):
        """Test getting topology for nonexistent cluster returns empty list."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        topology = accessor.get_cluster_topology(999)
        assert topology == []

    # ==================== Integration Tests ====================

    def test_get_timeseries_full_flow(self, mocker: MockerFixture):
        """Test complete get_timeseries flow with post-processing."""
        accessor = ClusterAggregatingAccessor(self.cluster_config)

        timestamps = [
            datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 10, 1, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 10, 2, 0, tzinfo=timezone.utc),
        ]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))

        def mock_fetch(host_uid, metric_attrs, time_range):
            base_value = float(host_uid.id) * 10
            data = np.array([
                [[base_value]],
                [[base_value + 5]],
                [[base_value + 10]]
            ])
            return Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([metric_attrs]),
                entity_uid_idx=np.array([host_uid]),
                data=data,
            )

        mocker.patch.object(
            accessor._host_accessor,
            "_fetch_raw_data",
            side_effect=mock_fetch
        )

        period = Period(slice(timestamps[0], timestamps[-1], timedelta(minutes=1)))

        result = accessor.get_timeseries(
            self.cluster_uid_1, self.metric_cpu, period
        )

        assert result is not None
        assert result.shape[0] >= 2
        assert result._entity_idx.values[0] == self.cluster_uid_1
