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
from pyoneai.core.opennebula_db_monitoring_accessor import (
    OpenNebulaDBMonitoringAccessor,
)
from pyoneai.core.tsnumpy.index import TimeIndex


class TestOpenNebulaDBMonitoringAccessor:
    @pytest.fixture(autouse=True)
    def setup(self, mocker: MockerFixture):
        """Setup common test fixtures."""
        self.entity_uid_host = EntityUID(EntityType.HOST, 5)
        self.entity_uid_vm = EntityUID(EntityType.VIRTUAL_MACHINE, 448)

        self.metric_attrs_cpu = MetricAttributes(
            name="used_cpu", type=MetricType.GAUGE, dtype=Float()
        )
        self.metric_attrs_memory = MetricAttributes(
            name="memory", type=MetricType.GAUGE, dtype=Float()
        )

        # Sample XML bodies from OpenNebula
        self.sample_host_xml = """<MONITORING><TIMESTAMP>1760646106</TIMESTAMP><ID>5</ID><CAPACITY><FREE_CPU><![CDATA[1176]]></FREE_CPU><USED_CPU><![CDATA[24]]></USED_CPU><FREE_MEMORY><![CDATA[24186620]]></FREE_MEMORY><USED_MEMORY><![CDATA[8062088]]></USED_MEMORY></CAPACITY><SYSTEM><NETRX><![CDATA[2774603158]]></NETRX><NETTX><![CDATA[132955362]]></NETTX></SYSTEM></MONITORING>"""

        self.sample_vm_xml = """<MONITORING><CPU><![CDATA[1.0]]></CPU><MEMORY><![CDATA[2136624]]></MEMORY><NETRX><![CDATA[259579686]]></NETRX><NETTX><![CDATA[7603738]]></NETTX><DISKRDBYTES><![CDATA[404498338]]></DISKRDBYTES><DISKWRBYTES><![CDATA[6717573120]]></DISKWRBYTES><TIMESTAMP><![CDATA[1760646122]]></TIMESTAMP></MONITORING>"""

    # SQLite Tests
    def test_init_sqlite(self):
        """Test SQLite accessor initialization."""
        config = MonitoringConfig.opennebula_db_sqlite(
            db_path="/var/lib/one/one.db"
        )
        accessor = OpenNebulaDBMonitoringAccessor(config)

        assert accessor._db_type == "sqlite"
        assert accessor._db_path == "/var/lib/one/one.db"
        assert accessor._table_mapping["host"] == "host_monitoring"
        assert accessor._table_mapping["virtualmachine"] == "vm_monitoring"
        assert accessor._id_column_mapping["host"] == "hid"
        assert accessor._timestamp_column_mapping["host"] == "last_mon_time"

    def test_init_mysql(self, mocker: MockerFixture):
        """Test MySQL accessor initialization."""
        # Mock pymysql import
        mock_pymysql = mocker.MagicMock()
        mocker.patch.dict("sys.modules", {"pymysql": mock_pymysql})

        config = MonitoringConfig.opennebula_db_mysql(
            host="localhost",
            port=3306,
            database="opennebula",
            user="oneadmin",
            password="secret",
        )
        accessor = OpenNebulaDBMonitoringAccessor(config)

        assert accessor._db_type == "mysql"
        assert accessor._mysql_config["host"] == "localhost"
        assert accessor._mysql_config["port"] == 3306
        assert accessor._mysql_config["database"] == "opennebula"

    def test_init_mysql_missing_pymysql(self):
        """Test MySQL accessor fails gracefully without pymysql."""
        config = MonitoringConfig.opennebula_db_mysql()

        with pytest.raises(ImportError, match="pymysql library is required"):
            OpenNebulaDBMonitoringAccessor(config)

    def test_init_unsupported_db_type(self):
        """Test unsupported database type raises error."""
        config = MonitoringConfig(
            backend="opennebula_db",
            connection={"type": "postgres", "db_path": "/tmp/test.db"},
            schema={},
            behavior={},
        )

        with pytest.raises(ValueError, match="Unsupported database type"):
            OpenNebulaDBMonitoringAccessor(config)

    def test_type(self):
        """Test accessor type is OBSERVATION."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        assert accessor.type == AccessorType.OBSERVATION

    def test_parse_xml_metric_value_host(self):
        """Test parsing metric values from host XML."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        # Test various host metrics
        assert (
            accessor._parse_xml_metric_value(
                self.sample_host_xml, "CAPACITY/USED_CPU"
            )
            == 24.0
        )
        assert (
            accessor._parse_xml_metric_value(
                self.sample_host_xml, "CAPACITY/FREE_CPU"
            )
            == 1176.0
        )
        assert (
            accessor._parse_xml_metric_value(
                self.sample_host_xml, "CAPACITY/USED_MEMORY"
            )
            == 8062088.0
        )
        assert (
            accessor._parse_xml_metric_value(
                self.sample_host_xml, "SYSTEM/NETRX"
            )
            == 2774603158.0
        )

    def test_parse_xml_metric_value_vm(self):
        """Test parsing metric values from VM XML."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        # Test various VM metrics
        assert (
            accessor._parse_xml_metric_value(self.sample_vm_xml, "CPU") == 1.0
        )
        assert (
            accessor._parse_xml_metric_value(self.sample_vm_xml, "MEMORY")
            == 2136624.0
        )
        assert (
            accessor._parse_xml_metric_value(self.sample_vm_xml, "NETRX")
            == 259579686.0
        )
        assert (
            accessor._parse_xml_metric_value(self.sample_vm_xml, "DISKRDBYTES")
            == 404498338.0
        )

    def test_parse_xml_metric_value_not_found(self):
        """Test parsing returns None for non-existent metrics."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        result = accessor._parse_xml_metric_value(
            self.sample_host_xml, "NONEXISTENT/METRIC"
        )
        assert result is None

    def test_parse_xml_metric_value_invalid_xml(self):
        """Test parsing handles invalid XML gracefully."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        result = accessor._parse_xml_metric_value(
            "invalid xml", "CAPACITY/USED_CPU"
        )
        assert result is None

    def test_parse_xml_metric_value_invalid_number(self):
        """Test parsing handles non-numeric values gracefully."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        xml = "<MONITORING><VALUE><![CDATA[not_a_number]]></VALUE></MONITORING>"
        result = accessor._parse_xml_metric_value(xml, "VALUE")
        assert result is None

    def test_get_metric_xpath(self):
        """Test retrieving XPath from schema mapping."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        xpath = accessor._get_metric_xpath("used_cpu")
        assert xpath == "CAPACITY/USED_CPU"

        xpath = accessor._get_metric_xpath("cpu")
        assert xpath == "CPU"

    def test_get_metric_xpath_not_mapped(self):
        """Test error when metric not in schema mapping."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        with pytest.raises(ValueError, match="Metric 'unknown' not mapped"):
            accessor._get_metric_xpath("unknown")

    def test_execute_query_sqlite(self, mocker: MockerFixture):
        """Test SQLite query execution."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        # Mock sqlite3.connect and the context manager
        mock_cursor = mocker.MagicMock()
        mock_fetchall = [(1760646106, self.sample_host_xml)]
        mock_cursor.execute.return_value.fetchall.return_value = mock_fetchall
        
        mock_conn = mocker.MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.__exit__.return_value = None
        
        mocker.patch("sqlite3.connect", return_value=mock_conn)

        results = accessor._execute_query(
            "SELECT * FROM test", [1, 2, 3]
        )

        assert len(results) == 1
        assert results[0] == (1760646106, self.sample_host_xml)

    def test_fetch_raw_data_host(self, mocker: MockerFixture):
        """Test fetching raw data for host entity."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        # Mock database query results
        timestamp1 = 1760646100
        timestamp2 = 1760646160
        mock_results = [
            (timestamp1, self.sample_host_xml),
            (timestamp2, self.sample_host_xml),
        ]

        mocker.patch.object(
            accessor, "_execute_query", return_value=mock_results
        )

        # Create time range
        start = datetime.fromtimestamp(timestamp1, timezone.utc)
        end = datetime.fromtimestamp(timestamp2, timezone.utc)
        time_range = Period(slice(start, end, timedelta(minutes=1)))

        # Fetch data
        ts = accessor._fetch_raw_data(
            self.entity_uid_host, self.metric_attrs_cpu, time_range
        )

        assert ts is not None
        assert len(ts.time_index) == 2
        assert ts.values[0, 0, 0] == 24.0
        assert ts.values[1, 0, 0] == 24.0
        assert ts.entity_uids[0] == self.entity_uid_host

    def test_fetch_raw_data_vm(self, mocker: MockerFixture):
        """Test fetching raw data for VM entity."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        # Mock database query results
        timestamp = 1760646122
        mock_results = [(timestamp, self.sample_vm_xml)]

        mocker.patch.object(
            accessor, "_execute_query", return_value=mock_results
        )

        # Create time range
        dt = datetime.fromtimestamp(timestamp, timezone.utc)
        time_range = Period(slice(dt, dt + timedelta(minutes=1), timedelta(seconds=60)))

        # Fetch data
        ts = accessor._fetch_raw_data(
            self.entity_uid_vm, self.metric_attrs_memory, time_range
        )

        assert ts is not None
        assert len(ts.time_index) == 1
        assert ts.values[0, 0, 0] == 2136624.0
        assert ts.entity_uids[0] == self.entity_uid_vm

    def test_fetch_raw_data_no_results(self, mocker: MockerFixture):
        """Test fetching when no data found."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        # Mock empty results
        mocker.patch.object(accessor, "_execute_query", return_value=[])

        start = datetime.now(timezone.utc)
        end = start + timedelta(hours=1)
        time_range = Period(slice(start, end, timedelta(minutes=1)))

        ts = accessor._fetch_raw_data(
            self.entity_uid_host, self.metric_attrs_cpu, time_range
        )

        assert ts is None

    def test_fetch_raw_data_unparseable_values(self, mocker: MockerFixture):
        """Test fetching with unparseable XML values."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        # Mock results with invalid XML
        timestamp = 1760646100
        invalid_xml = "<MONITORING><INVALID>data</INVALID></MONITORING>"
        mock_results = [(timestamp, invalid_xml)]

        mocker.patch.object(
            accessor, "_execute_query", return_value=mock_results
        )

        start = datetime.fromtimestamp(timestamp, timezone.utc)
        end = start + timedelta(hours=1)
        time_range = Period(slice(start, end, timedelta(minutes=1)))

        ts = accessor._fetch_raw_data(
            self.entity_uid_host, self.metric_attrs_cpu, time_range
        )

        # Should return None when all values are unparseable
        assert ts is None

    def test_fetch_raw_data_unsupported_entity_type(
        self, mocker: MockerFixture
    ):
        """Test error for unsupported entity type."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        # Create entity with unsupported type by mocking
        mock_entity_uid = mocker.MagicMock()
        mock_entity_uid.type.value = "datastore"
        mock_entity_uid.id = 1

        start = datetime.now(timezone.utc)
        time_range = Period(slice(start, start + timedelta(hours=1), timedelta(minutes=1)))

        with pytest.raises(ValueError, match="Entity type 'datastore' not supported"):
            accessor._fetch_raw_data(
                mock_entity_uid, self.metric_attrs_cpu, time_range
            )

    def test_custom_metric_xpath_mapping(self):
        """Test custom metric XPath mappings."""
        custom_mappings = {
            "custom_metric": "CUSTOM/PATH",
            "another_metric": "ANOTHER/PATH/TO/METRIC",
        }

        config = MonitoringConfig.opennebula_db_sqlite(
            db_path="/tmp/test.db", metric_xpath_mapping=custom_mappings
        )
        accessor = OpenNebulaDBMonitoringAccessor(config)

        assert accessor._metric_xpath_mapping == custom_mappings
        assert (
            accessor._get_metric_xpath("custom_metric") == "CUSTOM/PATH"
        )

    def test_get_timeseries_integration(self, mocker: MockerFixture):
        """Test full get_timeseries call (integration test)."""
        config = MonitoringConfig.opennebula_db_sqlite(db_path="/tmp/test.db")
        accessor = OpenNebulaDBMonitoringAccessor(config)

        # Mock _fetch_raw_data to return a simple timeseries
        timestamp = datetime.now(timezone.utc)
        time_idx = TimeIndex(np.array([timestamp], dtype="object"))

        from pyoneai.core.tsnumpy.timeseries import Timeseries

        mock_ts = Timeseries(
            time_idx=time_idx,
            metric_idx=np.array([self.metric_attrs_cpu]),
            entity_uid_idx=np.array([self.entity_uid_host]),
            data=np.array([[[50.0]]]),
        )

        mocker.patch.object(accessor, "_fetch_raw_data", return_value=mock_ts)

        # Call get_timeseries
        start = timestamp - timedelta(hours=1)
        end = timestamp
        period = Period(slice(start, end, timedelta(minutes=1)))

        result = accessor.get_timeseries(
            self.entity_uid_host, self.metric_attrs_cpu, period
        )

        # Verify post-processing was applied (from base class)
        assert result is not None
        accessor._fetch_raw_data.assert_called_once()
