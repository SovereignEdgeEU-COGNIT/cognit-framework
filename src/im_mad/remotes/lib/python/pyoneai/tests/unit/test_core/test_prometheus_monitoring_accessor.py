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
from pyoneai.core.prometheus_monitoring_accessor import (
    PrometheusMonitoringAccessor,
)


class TestPrometheusMonitoringAccessor:
    @pytest.fixture(autouse=True)
    def setup(self, mocker: MockerFixture):
        """Setup common test fixtures."""
        # Mock requests module
        self.mock_requests = mocker.MagicMock()
        self.mock_session = mocker.MagicMock()
        self.mock_requests.Session.return_value = self.mock_session
        mocker.patch.dict("sys.modules", {"requests": self.mock_requests})

        self.config = MonitoringConfig.opennebula_prometheus(
            url="http://localhost:9090"
        )
        self.entity_uid = EntityUID(EntityType.HOST, 1)
        self.metric_attrs = MetricAttributes(
            name="cpu", type=MetricType.GAUGE, dtype=Float()
        )

    def test_init(self):
        """Test Prometheus accessor initialization."""
        accessor = PrometheusMonitoringAccessor(self.config)

        assert accessor.base_url == "http://localhost:9090"
        assert accessor.timeout == 30
        assert accessor.session is not None

    def test_init_missing_requests(self, mocker: MockerFixture):
        """Test initialization fails without requests library."""
        # Remove requests from sys.modules
        mocker.patch.dict("sys.modules", {"requests": None})

        # Mock import to raise ImportError
        def mock_import(name, *args, **kwargs):
            if name == "requests":
                raise ImportError("No module named requests")
            return __import__(name, *args, **kwargs)

        mocker.patch("builtins.__import__", side_effect=mock_import)

        with pytest.raises(
            ImportError, match="requests library is required"
        ):
            PrometheusMonitoringAccessor(self.config)

    def test_type(self):
        """Test accessor type is OBSERVATION."""
        accessor = PrometheusMonitoringAccessor(self.config)
        assert accessor.type == AccessorType.OBSERVATION

    def test_build_prometheus_query_simple(self):
        """Test building simple Prometheus query."""
        accessor = PrometheusMonitoringAccessor(self.config)

        query = accessor._build_prometheus_query(
            self.entity_uid, self.metric_attrs
        )

        # Should build query with metric template and labels
        assert "opennebula_cpu" in query
        # EntityType enum gets converted to string representation
        assert 'entity_type=' in query
        assert 'entity_id="1"' in query

    def test_build_prometheus_query_custom_template(self):
        """Test building query with custom metric template."""
        custom_config = MonitoringConfig(
            backend="prometheus",
            connection={"url": "http://localhost:9090"},
            schema={
                "metric_name_template": "node_{metric_name}_total",
                "label_mappings": {"entity_id": "instance"},
            },
            behavior={},
        )
        accessor = PrometheusMonitoringAccessor(custom_config)

        query = accessor._build_prometheus_query(
            self.entity_uid, self.metric_attrs
        )

        assert "node_cpu_total" in query
        assert 'instance="1"' in query

    def test_build_prometheus_query_with_custom_labels(self):
        """Test building query with custom labels."""
        custom_config = MonitoringConfig(
            backend="prometheus",
            connection={"url": "http://localhost:9090"},
            schema={
                "metric_name_template": "{metric_name}",
                "label_mappings": {},
                "custom_labels": {"job": "opennebula", "env": "production"},
            },
            behavior={},
        )
        accessor = PrometheusMonitoringAccessor(custom_config)

        query = accessor._build_prometheus_query(
            self.entity_uid, self.metric_attrs
        )

        assert "cpu" in query
        assert 'job="opennebula"' in query
        assert 'env="production"' in query

    def test_parse_prometheus_response_success(self):
        """Test parsing successful Prometheus response."""
        accessor = PrometheusMonitoringAccessor(self.config)

        # Mock Prometheus response
        prom_response = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "cpu"},
                        "values": [
                            [1704110400, "50.0"],
                            [1704110460, "60.0"],
                        ],
                    }
                ]
            },
        }

        result = accessor._parse_prometheus_response(
            prom_response, self.entity_uid, self.metric_attrs
        )

        assert result is not None
        assert len(result.time_index) == 2
        assert result.values[0, 0, 0] == 50.0
        assert result.values[1, 0, 0] == 60.0

    def test_parse_prometheus_response_empty_result(self):
        """Test parsing Prometheus response with no data."""
        accessor = PrometheusMonitoringAccessor(self.config)

        prom_response = {"status": "success", "data": {"result": []}}

        result = accessor._parse_prometheus_response(
            prom_response, self.entity_uid, self.metric_attrs
        )

        assert result is None

    def test_parse_prometheus_response_failure(self):
        """Test parsing failed Prometheus response."""
        accessor = PrometheusMonitoringAccessor(self.config)

        prom_response = {
            "status": "error",
            "error": "Query timeout",
        }

        with pytest.raises(ValueError, match="Prometheus query failed"):
            accessor._parse_prometheus_response(
                prom_response, self.entity_uid, self.metric_attrs
            )

    def test_parse_prometheus_response_handles_inf(self):
        """Test parsing handles infinity values."""
        accessor = PrometheusMonitoringAccessor(self.config)

        prom_response = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {},
                        "values": [
                            [1704110400, "inf"],
                            [1704110460, "-inf"],
                            [1704110520, "50.0"],
                        ],
                    }
                ]
            },
        }

        result = accessor._parse_prometheus_response(
            prom_response, self.entity_uid, self.metric_attrs
        )

        # inf values should be converted to NaN
        assert np.isnan(result.values[0, 0, 0])
        assert np.isnan(result.values[1, 0, 0])
        assert result.values[2, 0, 0] == 50.0

    def test_parse_prometheus_response_handles_invalid_values(self):
        """Test parsing handles non-numeric values."""
        accessor = PrometheusMonitoringAccessor(self.config)

        prom_response = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {},
                        "values": [
                            [1704110400, "invalid"],
                            [1704110460, "50.0"],
                        ],
                    }
                ]
            },
        }

        result = accessor._parse_prometheus_response(
            prom_response, self.entity_uid, self.metric_attrs
        )

        # Invalid values should be NaN
        assert np.isnan(result.values[0, 0, 0])
        assert result.values[1, 0, 0] == 50.0

    def test_fetch_raw_data_success(self, mocker: MockerFixture):
        """Test successful data fetching."""
        accessor = PrometheusMonitoringAccessor(self.config)

        # Mock HTTP response
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {},
                        "values": [[1704110400, "50.0"]],
                    }
                ]
            },
        }
        self.mock_session.get.return_value = mock_response

        # Create time range
        start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        time_range = Period(slice(start, end, timedelta(minutes=1)))

        result = accessor._fetch_raw_data(
            self.entity_uid, self.metric_attrs, time_range
        )

        # Verify HTTP request was made
        self.mock_session.get.assert_called_once()
        call_args = self.mock_session.get.call_args

        assert "http://localhost:9090/api/v1/query_range" in call_args[0][0]
        assert "params" in call_args[1]
        assert "query" in call_args[1]["params"]

        # Verify result
        assert result is not None

    def test_fetch_raw_data_connection_error(self, mocker: MockerFixture):
        """Test handling of connection errors."""
        accessor = PrometheusMonitoringAccessor(self.config)

        # Create a proper RequestException
        exception_class = type('RequestException', (Exception,), {})
        self.mock_requests.exceptions.RequestException = exception_class
        
        # Mock connection error - don't call json() on exception
        self.mock_session.get.side_effect = exception_class("Connection failed")

        start = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        time_range = Period(slice(start, end, timedelta(minutes=1)))

        with pytest.raises(ConnectionError, match="Failed to fetch data"):
            accessor._fetch_raw_data(
                self.entity_uid, self.metric_attrs, time_range
            )

    def test_authentication_basic(self):
        """Test basic authentication setup."""
        auth_config = MonitoringConfig(
            backend="prometheus",
            connection={
                "url": "http://localhost:9090",
                "auth": {
                    "type": "basic",
                    "username": "admin",
                    "password": "secret",
                },
            },
            schema={},
            behavior={},
        )

        accessor = PrometheusMonitoringAccessor(auth_config)

        # Verify basic auth was set
        assert accessor.session.auth == ("admin", "secret")

    def test_authentication_bearer(self):
        """Test bearer token authentication setup."""
        auth_config = MonitoringConfig(
            backend="prometheus",
            connection={
                "url": "http://localhost:9090",
                "auth": {"type": "bearer", "token": "my-token-123"},
            },
            schema={},
            behavior={},
        )

        accessor = PrometheusMonitoringAccessor(auth_config)

        # Verify bearer token was set (check that __setitem__ was called)
        # The mock session stores this differently
        accessor.session.headers.__setitem__.assert_any_call(
            "Authorization", "Bearer my-token-123"
        )

    def test_custom_timeout(self):
        """Test custom timeout configuration."""
        custom_config = MonitoringConfig(
            backend="prometheus",
            connection={"url": "http://localhost:9090", "timeout": 60},
            schema={},
            behavior={},
        )

        accessor = PrometheusMonitoringAccessor(custom_config)
        assert accessor.timeout == 60

    def test_url_trailing_slash_removal(self):
        """Test that trailing slash is removed from URL."""
        config_with_slash = MonitoringConfig(
            backend="prometheus",
            connection={"url": "http://localhost:9090/"},
            schema={},
            behavior={},
        )

        accessor = PrometheusMonitoringAccessor(config_with_slash)
        assert accessor.base_url == "http://localhost:9090"
