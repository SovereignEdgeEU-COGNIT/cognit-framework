from collections import defaultdict
from collections.abc import Collection
from math import isclose
from typing import TYPE_CHECKING

from pulp import (
    LpAffineExpression as LinExpr,
    # LpMaximize as _MAX,
    LpMinimize as _MIN,
    LpProblem as Model,
    LpSolutionOptimal as _OPTIMAL_STATUS,
    LpSolver as Solver,
    LpVariable as Var,
    getSolver as _get_solver,
    lpSum as sum_
)

from . model import Cluster, Device


class DeviceOptimizer:
    __slots__ = (
        '_devices',
        '_clusters',
        '_cap_lb',
        '_cap_ub',
        '_model',
        '_solver',
        '_x',
        '_n_vms',
        '_energy',
        '_energy_bpts'
    )

    if TYPE_CHECKING:
        _devices: dict[int, Device]
        _clusters: dict[int, Cluster]
        _cap_lb: float
        _cap_ub: float
        _model: Model
        _solver: Solver
        _x: dict[tuple[int, int], Var]
        _n_vms: dict[int, Var]
        _energy: dict[int, LinExpr]
        _energy_bpts: dict[int, list[tuple[float, float]]]

    def __init__(
        self,
        devices: Collection[Device],
        clusters: Collection[Cluster],
        min_capacity: int = 0,
        max_capacity: int | None = None,
        contention_corr: float | None = 1.0,
        solver: str = 'COIN_CMD',
        **kwargs
    ) -> None:
        self._devices = {device.id: device for device in devices}
        self._clusters = {cluster.id: cluster for cluster in clusters}
        self._cap_lb = float(min_capacity)
        if max_capacity is None:
            self._cap_ub = sum(cluster.max_capacity for cluster in clusters)
        else:
            self._cap_ub = float(max_capacity)
        self._model = Model(name='', sense=_MIN)
        self._solver = _get_solver(solver, **kwargs)
        self._x = {}
        self._n_vms = {}
        self._energy = {}
        # TODO: Use `match` if Python version allows.
        self._energy_bpts = e_bpts = {}
        if contention_corr is None:
            # Contention is not allowed at all.
            for id_, cluster in self._clusters.items():
                energy = cluster.energy
                if isclose(energy[-1][1], energy[-2][1], rel_tol=0.01):
                    # TODO: Check this assumption on contention.
                    e_bpts[id_] = energy[:-1]
                else:
                    e_bpts[id_] = energy
        elif contention_corr == 1:
            # Contention is allowed without penalizing the objective.
            for id_, cluster in self._clusters.items():
                e_bpts[id_] = cluster.energy
        else:
            # Contention is allowed, but the objective is penalized according
            # to the value of ``contention_corr``.
            for id_, cluster in self._clusters.items():
                energy = cluster.energy
                last_bpt = energy[-1]
                if isclose(last_bpt[1], energy[-2][1], rel_tol=0.01):
                    # TODO: Check this assumption on contention.
                    bpts = energy[:-1]
                    bpts.append((last_bpt[0], last_bpt[1] * contention_corr))
                    e_bpts[id_] = bpts
                else:
                    e_bpts[id_] = energy

    def _add_vars(self) -> None:
        cap_ub = self._cap_ub
        x = self._x
        n_vms = self._n_vms

        for device_id, device in self._devices.items():
            for cluster_id in device.cluster_ids:
                x[device_id, cluster_id] = Var(
                    name=f'x_{device_id}_{cluster_id}', cat='Binary'
                )

        # NOTE: The number of VMs can be calculated outside of the optimizer.
        for cluster_id, cluster in self._clusters.items():
            n_vms[cluster_id] = Var(
                name=f'n_vms_{cluster_id}',
                cat='Integer',
                lowBound=0.0,
                upBound=min(cluster.max_capacity, cap_ub)
            )

    def _add_constrs(self) -> None:
        devices = self._devices
        clusters = self._clusters
        model = self._model
        n_vms = self._n_vms

        # One device is allocated to exactly one cluster.
        sum_alloc: defaultdict[int, LinExpr] = defaultdict(LinExpr)
        for (device_id, cluster_id), x_var in self._x.items():
            sum_alloc[device_id] += x_var

        for device_id, n_allocs in sum_alloc.items():
            model += (
                n_allocs == 1.0,
                f'device_{device_id}_allocated_to_1_cluster'
            )

        # Capacity constraints.
        cluster_loads: defaultdict[int, LinExpr] = defaultdict(LinExpr)
        for (device_id, cluster_id), x_var in self._x.items():
            device_load = devices[device_id].capacity_load
            cluster_loads[cluster_id] += x_var * device_load

        for cluster_id, cluster_load in cluster_loads.items():
            model += (
                cluster_load <= clusters[cluster_id].max_capacity,
                f'cluster_{cluster_id}_capacity_constraint'
            )
            model += (
                cluster_load <= n_vms[cluster_id],
                f'cluster_{cluster_id}_vm_count_lb_constraint'
            )
            model += (
                cluster_load + 0.9999 >= n_vms[cluster_id],
                f'cluster_{cluster_id}_vm_count_ub_constraint'
            )

        # Total capacity of the system.
        cap = sum_(self._n_vms.values())
        model += (
            cap >= self._cap_lb,
            'capacity_lb_constraint'
        )
        model += (
            cap <= self._cap_ub,
            'capacity_ub_constraint'
        )

    def _add_energy(self) -> None:
        model = self._model
        devices = self._devices
        energy = self._energy

        cluster_loads: defaultdict[int, LinExpr] = defaultdict(LinExpr)
        for (device_id, cluster_id), x_var in self._x.items():
            device_load = devices[device_id].load
            cluster_loads[cluster_id] += x_var * device_load

        # for cluster_id, cluster in self._clusters.items():
        for cluster_id, breakpoints in self._energy_bpts.items():
            cluster_cpu = LinExpr()
            cluster_energy = LinExpr()
            energy[cluster_id] = cluster_energy
            # breakpoints = cluster.energy
            if not breakpoints or len(breakpoints) == 1:
                continue
            breakpoints = sorted(breakpoints)
            zip_ = zip(breakpoints[:-1], breakpoints[1:])
            seg_inds: list[Var] = []

            # Iterate through the segments:
            for i, ((l_cpu, l_energy), (r_cpu, r_energy)) in enumerate(zip_):
                name = f"cluster_{cluster_id}_segment_{i}_"
                seg_ind = Var(name=f"{name}_indicator", cat="Binary")
                seg_inds.append(seg_ind)
                seg_weight = Var(name=f"{name}_weight", lowBound=0)
                model += (
                    seg_weight <= seg_ind,
                    f"{name}_weight_constraint"
                )
                d_cpu = r_cpu - l_cpu
                cluster_cpu += seg_ind * l_cpu + seg_weight * d_cpu
                d_energy = r_energy - l_energy
                cluster_energy += seg_ind * l_energy + seg_weight * d_energy

            # NOTE: This constraint implies that a cluster consumes energy even
            # when there are no VMs allocated on it.
            model += (
                sum_(seg_inds) == 1.0,
                f"cluster_{cluster_id}_cpu_usage_piecewise_segments_constraint"
            )
            model += (
                cluster_loads[cluster_id] == cluster_cpu,
                f"cluster_{cluster_id}_cpu_usage_piecewise_balance_constraint"
            )
            energy[cluster_id] = cluster_energy

    def _add_obj(self) -> None:
        # devices = self._devices
        clusters = self._clusters

        self._add_energy()

        # self._model += sum_(
        #     x_var
        #     * devices[device_id].load
        #     * clusters[cluster_id].carbon_intensity
        #     for (device_id, cluster_id), x_var in self._x.items()
        # )
        self._model += sum_(
            clusters[cluster_id].carbon_intensity * energy
            for cluster_id, energy in self._energy.items()
        )

    def optimize(
        self
    ) -> tuple[dict[int, int], dict[int, int], float] | tuple[()]:
        self._add_vars()
        self._add_constrs()
        self._add_obj()
        self._model.solve(solver=self._solver)

        if self._model.status != _OPTIMAL_STATUS:
            return ()

        allocs: dict[int, int] = {}
        for (device_id, cluster_id), x_var in self._x.items():
            if round(x_var.value()):
                allocs[device_id] = cluster_id

        n_vms: dict[int, int] = {}
        for cluster_id, n_vms_var in self._n_vms.items():
            n_vms_val = round(n_vms_var.value())
            n_vms[cluster_id] = n_vms_val

        return allocs, n_vms, self._model.objective.value()


def optimize_contention(
    devices: Collection[Device],
    clusters: Collection[Cluster],
    min_capacity: int = 0,
    max_capacity: int | None = None,
    contention_corr: float = 2.0,
    solver: str = 'COIN_CMD',
    **kwargs
) -> tuple[dict[int, int], dict[int, int], float] | tuple[()]:
    """
    Run optimization with automatic contention fallback.
    
    Tries optimization without contention penalty first. If it fails,
    retries with contention correction to spread VMs across clusters.
    
    Args:
        devices: Collection of devices to assign
        clusters: Collection of clusters to optimize over
        min_capacity: Minimum total system capacity
        max_capacity: Maximum total system capacity
        contention_corr: Contention penalty multiplier (default: 2.0)
        solver: MILP solver to use (default: 'COIN_CMD')
        
    Returns:
        Tuple of (allocs, n_vms, objective) or empty tuple if optimization fails
    """
    # First attempt: no contention allowed (strict, better performance)
    # Removes last energy breakpoint → clusters cannot reach full capacity
    opt = DeviceOptimizer(
        devices=devices,
        clusters=clusters,
        min_capacity=min_capacity,
        max_capacity=max_capacity,
        contention_corr=None,
        solver=solver,
        **kwargs
    )

    import pickle
    with open('/tmp/optimizer_1.pickle', mode='wb') as file:
        pickle.dump(opt, file)

    if result := opt.optimize():
        return result  # Success: found solution without contention
    
    # Second attempt: allow contention with penalty (relaxed, more flexible)
    # Keeps last breakpoint but inflates energy consumption → clusters can reach full capacity
    opt = DeviceOptimizer(
        devices=devices,
        clusters=clusters,
        min_capacity=min_capacity,
        max_capacity=max_capacity,
        contention_corr=contention_corr,
        solver=solver,
        **kwargs
    )

    with open('/tmp/optimizer_2.pickle', mode='wb') as file:
        pickle.dump(opt, file)

    return opt.optimize()


def optimize(
    clusters: Collection[Cluster], devices: Collection[Device], n_iter: int
) -> list[tuple]:
    """
    Run optimization for multiple device load variations.
    
    Args:
        clusters: Collection of clusters to optimize over
        devices: Collection of devices to assign
        n_iter: Number of iterations to create device load variations
        
    Returns:
        List of tuples (allocs, n_vms, objective) for each successful optimization
    """
    new_devices = [device.adjust(n_iter) for device in devices]
    new_devices = list(list(row) for row in zip(*new_devices))

    results: list[tuple[dict[int, int], dict[int, int], float]] = []
    for devices_ in new_devices:
        result = optimize_contention(devices_, clusters, msg=False)
        if result:
            results.append(result)

    return results
