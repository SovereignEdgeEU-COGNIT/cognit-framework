import pytest

from pyoneai.core import (
    Entity,
    EntityType,
    EntityUID,
    Float,
    UInt,
    MetricAttributes,
    MetricType,
)


class TestEntity:
    @pytest.fixture(autouse=True)
    def setup(self, mocker):
        self.uid = EntityUID(type=EntityType.VIRTUAL_MACHINE, id=0)
        self.monitoring = {
            "db_path": "dummy.db",
            "monitor_interval": 60,
        }
        self.metrics = {
            "cpu": MetricAttributes(
                name="cpu",
                type=MetricType.COUNTER,
                dtype=Float(0.0, 100.0),
            ),
            "memory": MetricAttributes(
                name="memory",
                type=MetricType.GAUGE,
                dtype=UInt(0, 1000000),
            ),
        }

        # Mock the registry create method instead of SQLiteAccessor
        self.mock_monitoring_accessor = mocker.MagicMock()
        self.mock_registry_create = mocker.patch(
            "pyoneai.core.entity.MonitoringAccessorRegistry.create",
            return_value=self.mock_monitoring_accessor
        )
        self.mock_predictor_accessor = mocker.patch(
            "pyoneai.core.entity.PredictorAccessor"
        )
        self.mock_metric_accessor = mocker.patch(
            "pyoneai.core.entity.MetricAccessor"
        )
        self.mock_metric = mocker.patch("pyoneai.core.entity.Metric")
        self.mock_fourier_model = mocker.patch(
            "pyoneai.ml.FourierPredictionModel"
        )

        self.entity = Entity(
            uid=self.uid,
            metrics=self.metrics,
            monitoring=self.monitoring,
        )

    def test_init(self):
        assert self.entity._uid == self.uid
        assert len(self.entity._metrics) == 2
        assert "cpu" in self.entity._metrics
        assert "memory" in self.entity._metrics
        # Check that MonitoringAccessorRegistry.create was called
        # (should be called once with a MonitoringConfig)
        self.mock_registry_create.assert_called_once()
        # Verify the accessor was assigned
        assert self.entity._obs == self.mock_monitoring_accessor
        self.mock_metric.call_count == 2
        self.mock_metric_accessor.call_count == 2
        self.mock_fourier_model.assert_called_once()
        self.mock_predictor_accessor.assert_called_once()
