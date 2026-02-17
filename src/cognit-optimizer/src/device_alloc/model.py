from dataclasses import dataclass, field
from math import floor, inf, isclose
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from typing_extensions import Self
else:
    try:
        from typing import Self  # Python 3.11+
    except ImportError:
        from typing_extensions import Self  # Python 3.10 fallback

from .xmlrpc_client import OnedServerProxy


@dataclass(frozen=True, slots=True)
class Cluster:
    # The ID of the cluster.
    id: int
    # The number of VMs in the cluster.
    capacity: float
    max_capacity: float
    energy: list[tuple[float, float]] = field(default_factory=list)
    carbon_intensity: float = 0.0


_CLUSTER_METHOD = 'one.clusterpool.info'
_HOST_METHOD = 'one.host.info'


def _as_list(data) -> list:
    return data if isinstance(data, list) else [data]


def _parse_energy(breakpoints: str) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for bpt in breakpoints.split(';'):
        cpu, energy = bpt.split(',')
        result.append(((float(cpu)), float(energy)))
    return result


def _parse_contention(numa_nodes: dict[str, Any]) -> float:
    nodes = _as_list(numa_nodes['NODE'])
    cont = sum(len(_as_list(node['CORE'])) for node in nodes)
    return float(cont)


# TODO: Check optional fields in the XML schema.
def create_cluster_pool(oned_client: OnedServerProxy) -> list[Cluster]:
    data = oned_client(_CLUSTER_METHOD)['CLUSTER_POOL']['CLUSTER']
    clusters = _as_list(data)
    cluster_pool: list[Cluster] = []
    for cluster in clusters:
        interc_energy = inf
        cont = 0.0
        cont_energy = 0.0
        n_vms = 0
        cpu_total = 0.0
        cluster_template = cluster.get('TEMPLATE', {})
        ghg = float(cluster_template.get('CARBON_INTENSITY', 0.0))
        # host_ids = [int(id_) for id_ in _as_list(cluster['HOSTS']['ID'])]
        host_ids = _as_list(cluster['HOSTS']['ID'])
        for host_id in host_ids:
            host = oned_client(_HOST_METHOD, int(host_id))['HOST']
            template = host['TEMPLATE']
            if 'CPU_ENERGY' not in template:
                continue
            if (vm_ids := host['VMS']) is not None:
                n_vms += len(_as_list(vm_ids['ID']))
            cpu_total += float(host['HOST_SHARE']['TOTAL_CPU']) / 100.0
            cpu_total = float(floor(cpu_total))
            cpu_energy = template['CPU_ENERGY']
            host_energy = _parse_energy(cpu_energy)
            interc_energy = min(interc_energy, host_energy[0][1])
            cont_cpu = _parse_contention(host['HOST_SHARE']['NUMA_NODES'])
            cont += cont_cpu
            cont_energy += host_energy[-1][1]

        if cpu_total > cont:
            bpts = [(0.0, interc_energy), (cont, cont_energy), (cpu_total, cont_energy)]
        else:
            bpts = [(0.0, interc_energy), (cpu_total, cont_energy)]

        cluster_ = Cluster(
            id=int(cluster['ID']),
            capacity=float(n_vms),
            max_capacity=cpu_total,
            energy=bpts,
            carbon_intensity=ghg
        )
        cluster_pool.append(cluster_)
    return cluster_pool


@dataclass(frozen=True, slots=True)
class Device:
    # The ID of the device.
    id: int
    # The load of the device related to the average time needed to complete a
    # requirement and the frequency of requirements. This number represents an
    # equivalent of the number of VMs used to satisfy the requirements.
    load: float
    # The load required to calculate the required capacity of the allocated
    # cluster.
    capacity_load: float
    # The IDs of the clusters suitable for the device.
    cluster_ids: list[int]

    def adjust(self, n_iter: int) -> list[Self]:
        if n_iter < 2:
            raise ValueError("'n_iter' must be greater than or equal to 2")
        result = [self]
        load = self.capacity_load
        n_iter -= 1
        diff = (1.0 - load) / n_iter
        for _ in range(n_iter):
            load += diff
            # NOTE: This is a guard againts unwanted consequences of the
            # numerical errors.
            load = max(min(load, 1.0), 0.0)
            device = type(self)(self.id, self.load, load, self.cluster_ids)
            result.append(device)
        return result
