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

__all__ = ["SQLiteMonitoringAccessor"]

from typing import TYPE_CHECKING

from .monitoring_accessor import BaseMonitoringAccessor
from .tsnumpy.timeseries import Timeseries

if TYPE_CHECKING:
    from .entity_uid import EntityUID
    from .metric_types import MetricAttributes
    from .monitoring_config import MonitoringConfig
    from .time import Period


class SQLiteMonitoringAccessor(BaseMonitoringAccessor):
    """
    SQLite database monitoring accessor.

    Uses the Timeseries.read_from_database method to fetch data
    from SQLite database with schema mapping support.

    Parameters
    ----------
    config : MonitoringConfig
        Monitoring configuration with SQLite backend settings.
    """

    def __init__(self, config: MonitoringConfig):
        super().__init__(config)
        self._db_path = self.connection_params["db_path"]

    def _fetch_raw_data(
        self,
        entity_uid: EntityUID,
        metric_attrs: MetricAttributes,
        time_range: Period,
    ) -> Timeseries | None:
        """
        Fetch raw data from SQLite database.

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
        # Use the existing Timeseries.read_from_database method
        # which handles the SQLite querying internally
        ts = Timeseries.read_from_database(
            path=self._db_path,
            metric_attrs=metric_attrs,
            entity_uid=entity_uid,
            time=time_range,
        )

        return ts
