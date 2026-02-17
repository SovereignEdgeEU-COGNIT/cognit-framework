from . import (
    Cluster,
    Device,
    DeviceOptimizer,
    OnedServerProxy,
    create_cluster_pool,
    optimize
)


def test_1() -> None:
    clusters = [
        Cluster(
            id=1,
            capacity=2.0,
            max_capacity=2.0,
            carbon_intensity=500.0,
            energy=[(0.0, 5.0), (2.0, 10.0)]
        ),
        Cluster(
            id=2,
            capacity=4.0,
            max_capacity=4.0,
            carbon_intensity=1_000.0,
            energy=[(0.0, 6.0), (2.0, 12.0), (4.0, 16.0)]
        ),
        Cluster(
            id=3,
            capacity=5.0,
            max_capacity=5.0,
            carbon_intensity=600.0,
            energy=[(0.0, 5.0), (2.0, 10.0), (5.0, 15.0)]
        ),
        Cluster(
            id=4,
            capacity=10.0,
            max_capacity=10.0,
            carbon_intensity=800.0,
            energy=[(0.0, 5.0), (5.0, 20.0), (10.0, 25.0)]
        )
    ]

    devices = [
        Device(id=11, load=0.1, capacity_load=0.1, cluster_ids=[1, 2, 3, 4]),
        Device(id=12, load=0.2, capacity_load=0.2, cluster_ids=[1, 2, 3, 4]),
        Device(id=13, load=0.3, capacity_load=0.3, cluster_ids=[1, 2, 3, 4]),
        Device(id=14, load=0.4, capacity_load=0.4, cluster_ids=[1, 2, 3, 4]),
        Device(id=15, load=0.5, capacity_load=0.5, cluster_ids=[1, 2, 3, 4]),
        Device(id=16, load=0.6, capacity_load=0.6, cluster_ids=[1, 2, 3, 4]),
        Device(id=17, load=0.7, capacity_load=0.7, cluster_ids=[1, 2, 3, 4]),
        Device(id=18, load=0.8, capacity_load=0.8, cluster_ids=[1, 2, 3, 4]),
        Device(id=19, load=0.9, capacity_load=0.9, cluster_ids=[1, 2, 3, 4]),
        Device(id=20, load=1.0, capacity_load=1.0, cluster_ids=[1, 2, 3, 4])
    ]

    opt = DeviceOptimizer(devices=devices, clusters=clusters, msg=False)
    result = opt.optimize()
    print(result)


def test_2() -> None:
    clusters = [
        Cluster(
            id=1,
            capacity=2.0,
            max_capacity=2.0,
            carbon_intensity=500.0,
            energy=[(0.0, 5.0), (2.0, 10.0)]
        ),
        Cluster(
            id=2,
            capacity=4.0,
            max_capacity=4.0,
            carbon_intensity=1_000.0,
            energy=[(0.0, 6.0), (2.0, 12.0), (4.0, 16.0)]
        ),
        Cluster(
            id=3,
            capacity=5.0,
            max_capacity=5.0,
            carbon_intensity=600.0,
            energy=[(0.0, 5.0), (2.0, 10.0), (5.0, 15.0)]
        ),
        Cluster(
            id=4,
            capacity=10.0,
            max_capacity=10.0,
            carbon_intensity=800.0,
            energy=[(0.0, 5.0), (5.0, 20.0), (10.0, 25.0)]
        )
    ]

    devices = [
        Device(id=11, load=0.1, capacity_load=0.1, cluster_ids=[1, 2, 3, 4]),
        Device(id=12, load=0.2, capacity_load=0.2, cluster_ids=[1, 2, 3, 4]),
        Device(id=13, load=0.3, capacity_load=0.3, cluster_ids=[1, 2, 3, 4]),
        Device(id=14, load=0.4, capacity_load=0.4, cluster_ids=[1, 2, 3, 4]),
        Device(id=15, load=0.5, capacity_load=0.5, cluster_ids=[1, 2, 3, 4]),
        Device(id=16, load=0.6, capacity_load=0.6, cluster_ids=[1, 2, 3, 4]),
        Device(id=17, load=0.7, capacity_load=0.7, cluster_ids=[1, 2, 3, 4]),
        Device(id=18, load=0.8, capacity_load=0.8, cluster_ids=[1, 2, 3, 4]),
        Device(id=19, load=0.9, capacity_load=0.9, cluster_ids=[1, 2, 3, 4]),
        Device(id=20, load=1.0, capacity_load=1.0, cluster_ids=[1, 2, 3, 4])
    ]

    result = optimize(clusters, devices, n_iter=5)
    print(result)


def test_3() -> None:
    with OnedServerProxy() as oned_client:
        clusters = create_cluster_pool(oned_client)
    print(clusters)

    devices = [
        Device(id=11, load=0.1, capacity_load=0.1, cluster_ids=[105, 106]),
        Device(id=12, load=0.2, capacity_load=0.2, cluster_ids=[105, 106]),
        Device(id=13, load=0.3, capacity_load=0.3, cluster_ids=[105, 106]),
        Device(id=14, load=0.4, capacity_load=0.4, cluster_ids=[105, 106]),
        Device(id=15, load=0.5, capacity_load=0.5, cluster_ids=[105, 106]),
        Device(id=16, load=0.6, capacity_load=0.6, cluster_ids=[105, 106]),
        Device(id=17, load=0.7, capacity_load=0.7, cluster_ids=[105, 106]),
        Device(id=18, load=0.8, capacity_load=0.8, cluster_ids=[105, 106]),
        Device(id=19, load=0.9, capacity_load=0.9, cluster_ids=[105, 106]),
        Device(id=20, load=1.0, capacity_load=1.0, cluster_ids=[105, 106])
    ]

    opt = DeviceOptimizer(devices=devices, clusters=clusters, msg=False)
    result = opt.optimize()
    print(result)


def main() -> None:
    # test_1()
    # test_2()
    test_3()


if __name__ == '__main__':
    main()

