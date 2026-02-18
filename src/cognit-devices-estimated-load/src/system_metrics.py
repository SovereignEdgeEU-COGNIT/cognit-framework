"""System-wide metrics collection for estimated load calculation."""

from typing import List, Dict, Any
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
import requests
from requests.auth import HTTPBasicAuth
from pyoneai.core import Entity, EntityType, EntityUID, MonitoringConfig
from pyoneai.core import Float, MetricAttributes, MetricType
from pyoneai.core.time import Period, Instant
from pyoneai.core.tsnumpy.io.sql import SQLEngine
from pyoneai.core.tsnumpy.timeseries import Timeseries
from pyoneai.core.tsnumpy.index import TimeIndex
import numpy as np
import cognit_conf as conf
from cognit_logger import get_logger

logger = get_logger(__name__)

# Per-service last stored timestamp for CPU sum (regular grid: last + interval).
# After process crash/reboot this is empty, so we use now() and re-sync.
_last_cpu_sum_time_per_service: Dict[int, datetime] = {}
# Max gap (seconds): if time since last store > this, use now() to re-sync (e.g. after long downtime).
MAX_STORAGE_GAP_SECONDS = 90


def get_next_storage_timestamp(service_id: int) -> datetime:
    """Return next timestamp for storing CPU sum: last + interval, or now() if no last or gap too large.
    Keeps a strict regular time grid (rows differ by exactly interval_seconds) so the SDK sees no NaN.
    """
    now = datetime.now()
    interval = getattr(conf, "ESTIMATED_LOAD_UPDATE_INTERVAL_SECONDS", 30)
    if service_id not in _last_cpu_sum_time_per_service:
        return now
    last = _last_cpu_sum_time_per_service[service_id]
    gap = (now - last).total_seconds()
    if gap > MAX_STORAGE_GAP_SECONDS:
        return now  # re-sync after crash or long gap
    next_ts = last + timedelta(seconds=interval)
    return next_ts


def get_oneflow_services() -> List[Dict[str, Any]]:
    """Get all OneFlow services via REST API."""
    try:
        oneflow_url = conf.ONE_XMLRPC.replace(':2633/RPC2', ':2474')
        response = requests.get(
            f"{oneflow_url}/service",
            auth=HTTPBasicAuth(conf.ONE_API_USER, conf.ONE_API_PASSWORD),
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        docs = data.get('DOCUMENT_POOL', {}).get('DOCUMENT', [])
        return docs if isinstance(docs, list) else [docs] if docs else []
        
    except Exception as e:
        logger.error(f"Error fetching OneFlow services: {e}")
        return []


def collect_system_metrics() -> List[Dict[str, Any]]:
    """Collect metrics for each OneFlow service with Frontend role.
    
    Returns:
        List of dicts: [{"service_id": int, "service_name": str, "queue_total": int, "sum_cpu_faas_role": float,
                         "faas_vm_count": int, "frontend_vm_count": int}]
    """
    all_services = get_oneflow_services()

    frontend_services = []
    for service in all_services:
        if has_frontend_role(service):
            frontend_services.append(service)

    service_metrics = []

    if not frontend_services:
        return service_metrics

    try:
        services_data = []
        for service in frontend_services:
            service_id = service.get("ID")
            service_name = service.get("NAME", f"service_{service_id}")

            frontend_vms = []
            faas_vms = []

            try:
                body = service.get("TEMPLATE", {}).get("BODY", {})
                if isinstance(body, str):
                    body = json.loads(body)
                roles = body.get("roles", [])

                for role in roles:
                    role_name = role.get("name")
                    nodes = role.get("nodes", [])

                    for node in nodes:
                        vm_id = node.get("deploy_id")
                        if vm_id:
                            vm_info = {"id": vm_id}
                            if role_name == "Frontend":
                                frontend_vms.append(vm_info)
                            elif role_name == "FaaS":
                                faas_vms.append(vm_info)
            except Exception as e:
                logger.warning(f"Warning: Could not extract VM info from service {service_id}: {e}")

            services_data.append({
                "service_id": service_id,
                "service_name": service_name,
                "frontend_vms": frontend_vms,
                "faas_vms": faas_vms,
                "frontend_vm_count": len(frontend_vms),
                "faas_vm_count": len(faas_vms),
            })

        service_topology = build_service_topology(services_data)
        monitoring_config = create_service_monitoring_config(service_topology)

        for service_data in services_data:
            service_id = service_data["service_id"]
            service_name = service_data["service_name"]

            metrics = get_service_metrics(service_id, service_name, monitoring_config)

            service_metrics.append({
                "service_id": service_id,
                "service_name": service_name,
                "queue_total": metrics["queue_total"],
                "sum_cpu_faas_role": metrics["sum_cpu_faas_role"],
                "frontend_vm_count": int(service_data.get("frontend_vm_count", 0) or 0),
                "faas_vm_count": int(service_data.get("faas_vm_count", 0) or 0),
            })

    except Exception as e:
        logger.error(f"Error collecting system metrics: {e}")

    return service_metrics


def has_frontend_role(service: Dict) -> bool:
    """Check if a service has a Frontend role."""
    try:
        body = service.get("TEMPLATE", {}).get("BODY", {})
        if isinstance(body, str):
            body = json.loads(body)
        roles = body.get("roles", [])
        return any(role.get("name") == "Frontend" for role in roles)
    except Exception:
        return False


def extract_flavour_from_service_name(service_name: str) -> str:
    """Extract flavour from service name and normalize to lowercase.
    
    Extracts the flavour by taking the part after the last underscore,
    or the entire name if no underscore is found.
    """
    if '_' in service_name:
        flavour = service_name.split('_')[-1]
    else:
        flavour = service_name
    
    return flavour.lower()


def calculate_estimated_load_for_service(service_cpu_percent: float, device_count: int) -> float:
    """Calculate estimated load for a single service.
    
    Returns:
        Estimated load in range [0.0, 1.0]
    """
    if device_count == 0:
        return 1.0
    
    if service_cpu_percent == 0:
        return 0.0
    
    estimated_load = (service_cpu_percent / 100.0) / device_count
    
    return min(estimated_load, 1.0)


def calculate_estimated_load(device_count: int) -> float:
    """Calculate estimated load from system metrics and device count."""
    service_metrics = collect_system_metrics()
    
    if not service_metrics:
        return 0.0
    
    total_backlog = sum(service["queue_total"] for service in service_metrics)
    
    if total_backlog > 0:
        return 1.0
    
    total_cpu_percent = 0.0
    for service in service_metrics:
        if service["sum_cpu_faas_role"] is not None:
            total_cpu_percent += service["sum_cpu_faas_role"]
    
    if total_cpu_percent == 0:
        return 0.0
    
    if device_count == 0:
        return 1.0
    
    estimated_load = (total_cpu_percent / 100.0) / device_count
    
    return min(estimated_load, 1.0)


def build_service_topology(services_data: list[dict]) -> dict:
    """Build service topology mapping for SDK role-level aggregation."""
    topology = {}

    for service_data in services_data:
        if not service_data:
            continue

        service_id = service_data.get("service_id")

        if not service_id:
            continue

        roles = {}

        if service_data["frontend_vms"]:
            frontend_ids = [int(vm["id"]) for vm in service_data["frontend_vms"]]
            roles["Frontend"] = frontend_ids

        if service_data["faas_vms"]:
            faas_ids = [int(vm["id"]) for vm in service_data["faas_vms"]]
            roles["FaaS"] = faas_ids

        if roles:
            topology[int(service_id)] = roles

    return topology


def create_service_monitoring_config(service_topology: dict) -> MonitoringConfig:
    """Create MonitoringConfig for SDK role-level aggregation."""
    vm_monitoring = MonitoringConfig.opennebula_db_mysql(
        **conf.DB_CONFIG,
        metric_xpath_mapping={
            "queue_total": "QUEUE_TOTAL",
            "cpu": "CPU",
        }
    )

    return MonitoringConfig(
        backend="service_aggregating",
        connection={"vm_monitoring_config": vm_monitoring},
        schema={"service_topology": service_topology},
        behavior={"monitor_interval": 60}
    )


def get_service_metrics(
    service_id: int,
    service_name: str,
    monitoring_config: MonitoringConfig
) -> dict[str, Any]:
    """Fetch latest metrics for a service using SDK role-level aggregation."""
    end_time = datetime.now()
    start_time = end_time - timedelta(minutes=5)
    period = Period(slice(start_time, end_time, timedelta(minutes=1)))

    results = {"queue_total": 0, "sum_cpu_faas_role": 0}

    try:
        frontend_role = Entity(
            uid=EntityUID(type=EntityType.SERVICE_ROLE, id=f"{service_id}_Frontend"),
            metrics={
                "queue_total": MetricAttributes(
                    name="queue_total",
                    type=MetricType.GAUGE,
                    dtype=Float(),
                    aggregation_fn="sum"
                )
            },
            monitoring=monitoring_config
        )

        queue_data = frontend_role["queue_total"][period]
        if queue_data is not None and queue_data.values.size > 0:
            latest_queue = queue_data.values.flatten()[-1]
            if not math.isnan(latest_queue):
                results["queue_total"] = int(latest_queue)
                logger.info(f"Service {service_id} ({service_name}): queue_total={results['queue_total']} (latest)")
            else:
                logger.info(f"Service {service_id} ({service_name}): queue_total=NaN (no data)")

    except Exception as e:
        logger.warning(f"Warning: Could not fetch queue_total for service {service_id}: {e}")

    try:
        faas_role = Entity(
            uid=EntityUID(type=EntityType.SERVICE_ROLE, id=f"{service_id}_FaaS"),
            metrics={
                "cpu": MetricAttributes(
                    name="cpu",
                    type=MetricType.GAUGE,
                    dtype=Float(),
                    aggregation_fn="sum"
                )
            },
            monitoring=monitoring_config
        )

        cpu_data = faas_role["cpu"][period]
        if cpu_data is not None and cpu_data.values.size > 0:
            logger.info(f"For service {service_id} ({service_name}): CPU data shape: {cpu_data.values.shape}, values: {cpu_data.values.flatten()}")
            latest_cpu = cpu_data.values.flatten()[-1]
            if not math.isnan(latest_cpu):
                results["sum_cpu_faas_role"] = float(latest_cpu)
                logger.info(f"Service {service_id} ({service_name}): sum_cpu_faas_role={results['sum_cpu_faas_role']:.2f}% (latest)")
            else:
                logger.info(f"Service {service_id} ({service_name}): sum_cpu_faas_role=NaN (no data)")

    except Exception as e:
        logger.warning(f"Warning: Could not fetch sum_cpu_faas_role for service {service_id}: {e}")

    return results


def store_cpu_sum_for_service(service_id: int, cpu_sum: float, timestamp: datetime) -> None:
    """Store CPU sum time series for a service using pyoneai SQLEngine."""
    try:
        db_dir = Path(conf.CPU_TIMESERIES_DB_DIR)
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / f"{service_id}.db"
        
        engine = SQLEngine(path=str(db_path), suffix="monitoring")
        
        entity_uid = EntityUID(type=EntityType.SERVICE_ROLE, id=str(service_id))
        
        metric_attrs = MetricAttributes(
            name="cpu_sum",
            type=MetricType.GAUGE,
            dtype=Float()
        )
        
        time_index = TimeIndex(np.array([timestamp], dtype="object"))
        
        timeseries = Timeseries(
            time_idx=time_index,
            metric_idx=np.array([metric_attrs]),
            entity_uid_idx=np.array([entity_uid]),
            data=np.array([[cpu_sum]]).reshape(1, 1, 1)
        )
        
        engine.insert_data(timeseries)
        _last_cpu_sum_time_per_service[service_id] = timestamp
        logger.debug(
            f"Stored CPU sum {cpu_sum:.2f}% for service {service_id} "
            f"at {timestamp.isoformat()} in {db_path.name}"
        )
        
    except Exception as e:
        logger.warning(f"Warning: Could not store CPU sum for service {service_id}: {e}")


def get_cpu_forecast_for_service(service_id: int, horizon_seconds: int = None) -> float | None:
    """Get CPU forecast for a service by querying with future Instant."""
    if horizon_seconds is None:
        horizon_seconds = conf.FORECAST_HORIZON_SECONDS
    
    try:
        db_dir = Path(conf.CPU_TIMESERIES_DB_DIR)
        db_path = db_dir / f"{service_id}.db"
        
        if not db_path.exists():
            logger.debug(f"CPU timeseries DB not found for service {service_id}, cannot forecast")
            return None
        
        monitoring_config = MonitoringConfig.opennebula_sqlite(
            db_path=str(db_path),
            monitor_interval=conf.ESTIMATED_LOAD_UPDATE_INTERVAL_SECONDS
        )
        
        entity_uid = EntityUID(type=EntityType.SERVICE_ROLE, id=str(service_id))
        
        entity = Entity(
            uid=entity_uid,
            metrics={
                "cpu_sum": MetricAttributes(
                    name="cpu_sum",
                    type=MetricType.GAUGE,
                    dtype=Float()
                )
            },
            monitoring=monitoring_config
        )
        
        now = datetime.now()
        future_time = now + timedelta(seconds=horizon_seconds)
        future_instant = Instant(future_time)
        
        forecast_ts = entity["cpu_sum"][future_instant]
        
        if forecast_ts is None:
            logger.debug(f"No forecast data returned for service {service_id}")
            return None
        
        raw = forecast_ts.values
        if hasattr(raw, "ndim"):
            forecast_array = np.asarray(raw)
        else:
            forecast_array = np.asarray([raw])
        
        if forecast_array.size == 0:
            logger.debug(f"Empty forecast array for service {service_id}")
            return None
        
        if forecast_array.ndim == 0:
            forecast_value = float(forecast_array)
        else:
            forecast_value = float(np.atleast_1d(forecast_array).flat[0])
        
        if math.isnan(forecast_value):
            logger.debug(f"Forecast is NaN for service {service_id}")
            return None
        
        return forecast_value
        
    except Exception as e:
        logger.warning(f"Warning: Could not get CPU forecast for service {service_id}: {e}")
        return None


def store_cpu_forecast_for_service(service_id: int, cpu_forecast: float, timestamp: datetime) -> None:
    """Store CPU forecast time series for a service using pyoneai SQLEngine."""
    try:
        db_dir = Path(conf.CPU_TIMESERIES_DB_DIR)
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / f"{service_id}.db"
        
        engine = SQLEngine(path=str(db_path), suffix="monitoring")
        
        entity_uid = EntityUID(type=EntityType.SERVICE_ROLE, id=str(service_id))
        
        metric_attrs = MetricAttributes(
            name="cpu_sum_forecast",
            type=MetricType.GAUGE,
            dtype=Float()
        )
        
        time_index = TimeIndex(np.array([timestamp], dtype="object"))
        
        timeseries = Timeseries(
            time_idx=time_index,
            metric_idx=np.array([metric_attrs]),
            entity_uid_idx=np.array([entity_uid]),
            data=np.array([[cpu_forecast]]).reshape(1, 1, 1)
        )
        
        engine.insert_data(timeseries)
        
        logger.debug(
            f"Stored CPU forecast {cpu_forecast:.2f}% for service {service_id} "
            f"at {timestamp.isoformat()} in {db_path.name}"
        )
        
    except Exception as e:
        logger.warning(f"Warning: Could not store CPU forecast for service {service_id}: {e}")
