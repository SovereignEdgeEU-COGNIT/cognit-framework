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
from pyoneai.core.service_aggregating_accessor import ServiceAggregatingAccessor
from pyoneai.core.tsnumpy.index import TimeIndex
from pyoneai.core.tsnumpy.timeseries import Timeseries


class TestServiceAggregatingAccessor:
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup common test fixtures."""
        # Create VM monitoring config
        self.vm_config = MonitoringConfig.opennebula_db_sqlite(
            db_path="/tmp/test.db"
        )

        # Define service topology
        self.service_topology = {
            123: {  # service_id
                "frontend": [1, 2, 3],
                "backend": [4, 5, 6],
                "cache": [7, 8],
            },
            456: {  # another service
                "web": [10, 11],
            },
        }

        # Create service monitoring config
        self.service_config = MonitoringConfig(
            backend="service_aggregating",
            connection={"vm_monitoring_config": self.vm_config},
            schema={"service_topology": self.service_topology},
            behavior={"monitor_interval": 60},
        )

        # Entity UIDs
        self.service_uid = EntityUID(EntityType.SERVICE, 123)
        self.role_uid_frontend = EntityUID(EntityType.SERVICE_ROLE, "123_frontend")
        self.role_uid_backend = EntityUID(EntityType.SERVICE_ROLE, "123_backend")
        self.vm_uid_1 = EntityUID(EntityType.VIRTUAL_MACHINE, 1)

        # Metric attributes
        self.metric_cpu = MetricAttributes(
            name="cpu", type=MetricType.GAUGE, dtype=Float()
        )
        self.metric_memory = MetricAttributes(
            name="memory", type=MetricType.GAUGE, dtype=Float()
        )
        self.metric_counter = MetricAttributes(
            name="requests", type=MetricType.COUNTER, dtype=Float()
        )
        self.metric_rate = MetricAttributes(
            name="queries", type=MetricType.RATE, dtype=Float()
        )
        self.metric_custom_agg = MetricAttributes(
            name="cpu", 
            type=MetricType.GAUGE, 
            dtype=Float(),
            aggregation_fn="max"
        )

    # ==================== Initialization Tests ====================

    def test_init(self):
        """Test ServiceAggregatingAccessor initialization."""
        accessor = ServiceAggregatingAccessor(self.service_config)

        assert accessor._service_topology == self.service_topology
        assert accessor._vm_accessor is not None
        assert accessor.config == self.service_config
        assert len(accessor._agg_functions) == 6  # sum, avg, mean, min, max, median

    def test_init_missing_vm_config(self):
        """Test initialization fails without vm_monitoring_config."""
        bad_config = MonitoringConfig(
            backend="service_aggregating",
            connection={},  # Missing vm_monitoring_config
            schema={"service_topology": self.service_topology},
            behavior={},
        )

        with pytest.raises(ValueError, match="vm_monitoring_config"):
            ServiceAggregatingAccessor(bad_config)

    def test_type(self):
        """Test accessor type is OBSERVATION."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        assert accessor.type == AccessorType.OBSERVATION

    # ==================== Aggregation Function Tests ====================

    def test_get_aggregation_function_gauge_default(self):
        """Test default aggregation for GAUGE is avg."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        agg_fn = accessor._get_aggregation_function(self.metric_cpu)
        assert agg_fn == "avg"

    def test_get_aggregation_function_counter_default(self):
        """Test default aggregation for COUNTER is sum."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        agg_fn = accessor._get_aggregation_function(self.metric_counter)
        assert agg_fn == "sum"

    def test_get_aggregation_function_rate_default(self):
        """Test default aggregation for RATE is sum."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        agg_fn = accessor._get_aggregation_function(self.metric_rate)
        assert agg_fn == "sum"

    def test_get_aggregation_function_custom(self):
        """Test custom aggregation function overrides default."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        agg_fn = accessor._get_aggregation_function(self.metric_custom_agg)
        assert agg_fn == "max"

    def test_get_aggregation_function_histogram(self):
        """Test default aggregation for HISTOGRAM is avg."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        metric_histogram = MetricAttributes(
            name="latency", type=MetricType.HISTOGRAM, dtype=Float()
        )
        agg_fn = accessor._get_aggregation_function(metric_histogram)
        assert agg_fn == "avg"

    # ==================== VM Lookup Tests ====================

    def test_get_vms_for_service(self):
        """Test getting all VMs for a service."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        vm_ids = accessor._get_vms_for_entity(self.service_uid)
        
        # Should include all VMs from all roles
        assert sorted(vm_ids) == [1, 2, 3, 4, 5, 6, 7, 8]

    def test_get_vms_for_role(self):
        """Test getting VMs for a specific role."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        vm_ids = accessor._get_vms_for_entity(self.role_uid_frontend)
        
        assert vm_ids == [1, 2, 3]

    def test_get_vms_for_role_backend(self):
        """Test getting VMs for backend role."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        vm_ids = accessor._get_vms_for_entity(self.role_uid_backend)
        
        assert vm_ids == [4, 5, 6]

    def test_get_vms_service_not_found(self):
        """Test error when service not in topology."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        bad_service_uid = EntityUID(EntityType.SERVICE, 999)
        
        with pytest.raises(ValueError, match="Service 999 not found"):
            accessor._get_vms_for_entity(bad_service_uid)

    def test_get_vms_role_not_found(self):
        """Test error when role not in service."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        bad_role_uid = EntityUID(EntityType.SERVICE_ROLE, "123_nonexistent")
        
        with pytest.raises(ValueError, match="Role 'nonexistent' not found"):
            accessor._get_vms_for_entity(bad_role_uid)

    def test_get_vms_invalid_entity_type(self):
        """Test error with invalid entity type."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        with pytest.raises(ValueError, match="only supports SERVICE and SERVICE_ROLE"):
            accessor._get_vms_for_entity(self.vm_uid_1)

    def test_get_vms_invalid_role_id_format(self):
        """Test error with invalid role ID format (non-numeric service ID)."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        bad_role_uid = EntityUID(EntityType.SERVICE_ROLE, "invalid_format")
        
        # Should raise error when service_id cannot be parsed as int
        with pytest.raises(ValueError, match="Cannot parse service_id from entity ID"):
            accessor._get_vms_for_entity(bad_role_uid)

    def test_get_vms_role_id_no_separator(self):
        """Test error when role ID has no underscore separator."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        bad_role_uid = EntityUID(EntityType.SERVICE_ROLE, "noseparator")
        
        # Should raise error when no underscore to separate service_id and role_name
        with pytest.raises(ValueError, match="Invalid service role entity ID format"):
            accessor._get_vms_for_entity(bad_role_uid)

    # ==================== Timeseries Aggregation Tests ====================

    def test_aggregate_timeseries_avg(self):
        """Test averaging multiple VM timeseries."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        # Create sample timeseries for 3 VMs
        timestamps = [
            datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 10, 1, 0, tzinfo=timezone.utc),
        ]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))
        
        vm_ts_list = []
        vm_values = [50.0, 60.0, 70.0]  # Three VMs with different CPU values
        
        for i, val in enumerate(vm_values):
            vm_uid = EntityUID(EntityType.VIRTUAL_MACHINE, i + 1)
            ts = Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([self.metric_cpu]),
                entity_uid_idx=np.array([vm_uid]),
                data=np.array([[[val]], [[val + 10]]]),
            )
            vm_ts_list.append(ts)
        
        # Aggregate with avg
        result = accessor._aggregate_timeseries(
            vm_ts_list, "avg", self.role_uid_frontend, self.metric_cpu
        )
        
        assert result is not None
        assert result.shape == (2, 1, 1)  # (time, metrics, entities)
        
        # Check averaged values: (50+60+70)/3 = 60, (60+70+80)/3 = 70
        expected_avg_t0 = np.mean([50.0, 60.0, 70.0])
        expected_avg_t1 = np.mean([60.0, 70.0, 80.0])
        
        assert np.isclose(result._data[0, 0, 0], expected_avg_t0)
        assert np.isclose(result._data[1, 0, 0], expected_avg_t1)
        
        # Check entity UID was replaced
        assert result._entity_idx.values[0] == self.role_uid_frontend

    def test_aggregate_timeseries_sum(self):
        """Test summing multiple VM timeseries."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))
        
        vm_ts_list = []
        vm_values = [100.0, 200.0, 300.0]
        
        for i, val in enumerate(vm_values):
            vm_uid = EntityUID(EntityType.VIRTUAL_MACHINE, i + 1)
            ts = Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([self.metric_counter]),
                entity_uid_idx=np.array([vm_uid]),
                data=np.array([[[val]]]),
            )
            vm_ts_list.append(ts)
        
        result = accessor._aggregate_timeseries(
            vm_ts_list, "sum", self.service_uid, self.metric_counter
        )
        
        assert result is not None
        # Sum: 100 + 200 + 300 = 600
        assert np.isclose(result._data[0, 0, 0], 600.0)

    def test_aggregate_timeseries_max(self):
        """Test max aggregation across VM timeseries."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))
        
        vm_ts_list = []
        vm_values = [50.0, 80.0, 60.0]  # Max should be 80
        
        for i, val in enumerate(vm_values):
            vm_uid = EntityUID(EntityType.VIRTUAL_MACHINE, i + 1)
            ts = Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([self.metric_cpu]),
                entity_uid_idx=np.array([vm_uid]),
                data=np.array([[[val]]]),
            )
            vm_ts_list.append(ts)
        
        result = accessor._aggregate_timeseries(
            vm_ts_list, "max", self.service_uid, self.metric_cpu
        )
        
        assert result is not None
        assert np.isclose(result._data[0, 0, 0], 80.0)

    def test_aggregate_timeseries_min(self):
        """Test min aggregation across VM timeseries."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))
        
        vm_ts_list = []
        vm_values = [50.0, 30.0, 60.0]  # Min should be 30
        
        for i, val in enumerate(vm_values):
            vm_uid = EntityUID(EntityType.VIRTUAL_MACHINE, i + 1)
            ts = Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([self.metric_cpu]),
                entity_uid_idx=np.array([vm_uid]),
                data=np.array([[[val]]]),
            )
            vm_ts_list.append(ts)
        
        result = accessor._aggregate_timeseries(
            vm_ts_list, "min", self.service_uid, self.metric_cpu
        )
        
        assert result is not None
        assert np.isclose(result._data[0, 0, 0], 30.0)

    def test_aggregate_timeseries_empty_list(self):
        """Test aggregation with empty timeseries list returns None."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        result = accessor._aggregate_timeseries(
            [], "avg", self.service_uid, self.metric_cpu
        )
        
        assert result is None

    def test_aggregate_timeseries_all_none(self):
        """Test aggregation with all None values returns None."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        result = accessor._aggregate_timeseries(
            [None, None, None], "avg", self.service_uid, self.metric_cpu
        )
        
        assert result is None

    def test_aggregate_timeseries_some_none(self):
        """Test aggregation filters out None values."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))
        
        vm_uid_1 = EntityUID(EntityType.VIRTUAL_MACHINE, 1)
        ts1 = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_cpu]),
            entity_uid_idx=np.array([vm_uid_1]),
            data=np.array([[[50.0]]]),
        )
        
        vm_uid_2 = EntityUID(EntityType.VIRTUAL_MACHINE, 2)
        ts2 = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_cpu]),
            entity_uid_idx=np.array([vm_uid_2]),
            data=np.array([[[70.0]]]),
        )
        
        result = accessor._aggregate_timeseries(
            [ts1, None, ts2], "avg", self.service_uid, self.metric_cpu
        )
        
        assert result is not None
        # Average of 50 and 70 = 60
        assert np.isclose(result._data[0, 0, 0], 60.0)

    def test_aggregate_timeseries_unsupported_function(self):
        """Test error with unsupported aggregation function."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))
        
        vm_uid = EntityUID(EntityType.VIRTUAL_MACHINE, 1)
        ts = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_cpu]),
            entity_uid_idx=np.array([vm_uid]),
            data=np.array([[[50.0]]]),
        )
        
        with pytest.raises(ValueError, match="Unsupported aggregation function"):
            accessor._aggregate_timeseries(
                [ts], "unsupported_fn", self.service_uid, self.metric_cpu
            )

    # ==================== Fetch Raw Data Tests ====================

    def test_fetch_raw_data_service(self, mocker: MockerFixture):
        """Test fetching and aggregating data for a service."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        # Create mock timeseries for each VM
        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))
        
        def mock_fetch(vm_uid, metric_attrs, time_range):
            """Return mock timeseries with VM ID as value."""
            return Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([metric_attrs]),
                entity_uid_idx=np.array([vm_uid]),
                data=np.array([[[float(vm_uid.id) * 10]]]),
            )
        
        # Mock the underlying VM accessor's fetch method
        mocker.patch.object(
            accessor._vm_accessor,
            "_fetch_raw_data",
            side_effect=mock_fetch
        )
        
        # Create time range
        time_range = Period(
            slice(timestamps[0], timestamps[0] + timedelta(hours=1), timedelta(minutes=1))
        )
        
        # Fetch data for service (aggregates all 8 VMs)
        result = accessor._fetch_raw_data(
            self.service_uid, self.metric_cpu, time_range
        )
        
        assert result is not None
        # VMs 1-8 with values 10,20,30,40,50,60,70,80
        # Average: (10+20+30+40+50+60+70+80)/8 = 45
        expected_avg = np.mean([10, 20, 30, 40, 50, 60, 70, 80])
        assert np.isclose(result._data[0, 0, 0], expected_avg)

    def test_fetch_raw_data_role(self, mocker: MockerFixture):
        """Test fetching and aggregating data for a role."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))
        
        def mock_fetch(vm_uid, metric_attrs, time_range):
            return Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([metric_attrs]),
                entity_uid_idx=np.array([vm_uid]),
                data=np.array([[[float(vm_uid.id) * 10]]]),
            )
        
        mocker.patch.object(
            accessor._vm_accessor,
            "_fetch_raw_data",
            side_effect=mock_fetch
        )
        
        time_range = Period(
            slice(timestamps[0], timestamps[0] + timedelta(hours=1), timedelta(minutes=1))
        )
        
        # Fetch data for frontend role (VMs 1, 2, 3)
        result = accessor._fetch_raw_data(
            self.role_uid_frontend, self.metric_cpu, time_range
        )
        
        assert result is not None
        # VMs 1-3 with values 10,20,30
        # Average: (10+20+30)/3 = 20
        expected_avg = np.mean([10, 20, 30])
        assert np.isclose(result._data[0, 0, 0], expected_avg)

    def test_fetch_raw_data_no_vms(self, mocker: MockerFixture):
        """Test fetching data for service with no VMs returns None."""
        # Create accessor with empty topology
        empty_config = MonitoringConfig(
            backend="service_aggregating",
            connection={"vm_monitoring_config": self.vm_config},
            schema={"service_topology": {999: {"empty": []}}},
            behavior={},
        )
        accessor = ServiceAggregatingAccessor(empty_config)
        
        empty_service_uid = EntityUID(EntityType.SERVICE, 999)
        time_range = Period(
            slice(
                datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
                timedelta(minutes=1)
            )
        )
        
        result = accessor._fetch_raw_data(
            empty_service_uid, self.metric_cpu, time_range
        )
        
        assert result is None

    def test_fetch_raw_data_vm_failures_handled(self, mocker: MockerFixture):
        """Test that VM fetch failures don't break aggregation."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        timestamps = [datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))
        
        def mock_fetch_with_failures(vm_uid, metric_attrs, time_range):
            # VM 2 fails
            if vm_uid.id == 2:
                raise Exception("VM 2 connection failed")
            return Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([metric_attrs]),
                entity_uid_idx=np.array([vm_uid]),
                data=np.array([[[float(vm_uid.id) * 10]]]),
            )
        
        # Mock print to capture warnings
        mock_print = mocker.patch("builtins.print")
        
        mocker.patch.object(
            accessor._vm_accessor,
            "_fetch_raw_data",
            side_effect=mock_fetch_with_failures
        )
        
        time_range = Period(
            slice(timestamps[0], timestamps[0] + timedelta(hours=1), timedelta(minutes=1))
        )
        
        # Fetch data for frontend role (VMs 1, 2, 3) - VM 2 will fail
        result = accessor._fetch_raw_data(
            self.role_uid_frontend, self.metric_cpu, time_range
        )
        
        assert result is not None
        # Should aggregate only VMs 1 and 3: (10+30)/2 = 20
        expected_avg = np.mean([10, 30])
        assert np.isclose(result._data[0, 0, 0], expected_avg)
        
        # Verify warning was printed
        mock_print.assert_called_once()
        assert "Warning" in str(mock_print.call_args)
        assert "VM 2" in str(mock_print.call_args)

    # ==================== Topology Management Tests ====================

    def test_update_service_topology(self):
        """Test updating service topology."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        # Update topology
        new_roles = {
            "frontend": [1, 2, 3, 9],  # Added VM 9
            "backend": [4, 5, 6, 10],  # Added VM 10
        }
        accessor.update_service_topology(123, new_roles)
        
        # Verify update
        topology = accessor.get_service_topology(123)
        assert topology == new_roles
        assert 9 in topology["frontend"]
        assert 10 in topology["backend"]

    def test_update_service_topology_new_service(self):
        """Test adding a new service to topology."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        new_service_roles = {
            "database": [20, 21, 22],
        }
        accessor.update_service_topology(789, new_service_roles)
        
        # Verify new service was added
        topology = accessor.get_service_topology(789)
        assert topology == new_service_roles

    def test_get_service_topology_specific(self):
        """Test getting topology for specific service."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        topology = accessor.get_service_topology(123)
        assert "frontend" in topology
        assert "backend" in topology
        assert topology["frontend"] == [1, 2, 3]

    def test_get_service_topology_all(self):
        """Test getting all service topologies."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        all_topology = accessor.get_service_topology()
        assert 123 in all_topology
        assert 456 in all_topology
        assert all_topology[123]["frontend"] == [1, 2, 3]

    def test_get_service_topology_nonexistent(self):
        """Test getting topology for nonexistent service returns empty dict."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        topology = accessor.get_service_topology(999)
        assert topology == {}

    # ==================== Integration Tests ====================

    def test_get_timeseries_full_flow(self, mocker: MockerFixture):
        """Test complete get_timeseries flow with post-processing."""
        accessor = ServiceAggregatingAccessor(self.service_config)
        
        # Create more realistic time series
        timestamps = [
            datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 10, 1, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 10, 2, 0, tzinfo=timezone.utc),
        ]
        time_idx = TimeIndex(np.array(timestamps, dtype="object"))
        
        def mock_fetch(vm_uid, metric_attrs, time_range):
            # Generate time series with slight variation per VM
            base_value = float(vm_uid.id) * 10
            data = np.array([
                [[base_value]],
                [[base_value + 5]],
                [[base_value + 10]]
            ])
            return Timeseries(
                time_idx=time_idx,
                metric_idx=np.array([metric_attrs]),
                entity_uid_idx=np.array([vm_uid]),
                data=data,
            )
        
        mocker.patch.object(
            accessor._vm_accessor,
            "_fetch_raw_data",
            side_effect=mock_fetch
        )
        
        # Create period
        period = Period(slice(timestamps[0], timestamps[-1], timedelta(minutes=1)))
        
        # Get timeseries (includes post-processing)
        result = accessor.get_timeseries(
            self.role_uid_frontend, self.metric_cpu, period
        )
        
        assert result is not None
        assert result.shape[0] >= 2  # At least 2 time points (may have interpolation)
        # Entity should be the role, not individual VMs
        assert result._entity_idx.values[0] == self.role_uid_frontend
