"""Mock pyoneai module to avoid dependency on private repository."""
import sys


class MockFloat:
    pass


class MockMetricType:
    GAUGE = 'gauge'


class MockMetricAttributes:
    def __init__(self, name, type, dtype):
        pass


class MockPyoneai:
    Float = MockFloat
    MetricType = MockMetricType
    MetricAttributes = MockMetricAttributes


def setup_mock():
    """Setup pyoneai mock before importing cognit_conf."""
    sys.modules['pyoneai'] = MockPyoneai
    sys.modules['pyoneai.core'] = MockPyoneai

