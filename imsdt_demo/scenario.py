"""场景生成模块：构造轻量车联网边缘计算场景，替代第一版中的高保真仿真。"""

from __future__ import annotations

import random

from imsdt_demo.models import EdgeState, NetworkState, SceneState, Task, VehicleState


def generate_scene(name: str, seed: int = 7) -> SceneState:
    """按场景名称生成确定性状态，保证 demo 和测试结果可复现。"""

    rng = random.Random(seed)
    supported = {"normal", "low_energy", "emergency", "high_load"}
    if name not in supported:
        allowed = ", ".join(sorted(supported))
        raise ValueError(f"未知场景: {name}，可选值: {allowed}")

    battery = 78.0
    density = 0.42
    task_priority = 4
    task_type = "perception"
    deadline_ms = 120.0
    size_mb = 1.5
    cpu_cycles_g = 1.2
    rsu_queue = 4
    edge_queue = 8
    rsu_utilization = 0.46
    edge_utilization = 0.52
    channel_quality = 0.94
    packet_loss = 0.015

    if name == "low_energy":
        battery = 18.0
        deadline_ms = 160.0
        size_mb = 1.2
        cpu_cycles_g = 1.0
        task_priority = 5
    elif name == "emergency":
        battery = 61.0
        density = 0.68
        task_priority = 10
        task_type = "emergency_perception"
        deadline_ms = 55.0
        size_mb = 0.6
        cpu_cycles_g = 0.65
        rsu_queue = 2
        edge_queue = 7
        channel_quality = 0.97
        packet_loss = 0.006
    elif name == "high_load":
        density = 0.82
        deadline_ms = 115.0
        size_mb = 2.0
        cpu_cycles_g = 1.5
        rsu_queue = 14
        edge_queue = 22
        rsu_utilization = 0.86
        edge_utilization = 0.91
        channel_quality = 0.82
        packet_loss = 0.04

    vehicle = VehicleState(
        vehicle_id="veh-001",
        position_m=1200.0 + rng.uniform(-25.0, 25.0),
        speed_mps=14.0 + rng.uniform(-2.0, 2.0),
        battery_percent=battery,
        local_compute_ghz=18.0,
        density=density,
    )
    task = Task(
        task_id=f"task-{name}",
        size_mb=size_mb,
        cpu_cycles_g=cpu_cycles_g,
        deadline_ms=deadline_ms,
        priority=task_priority,
        task_type=task_type,
    )
    network = NetworkState(
        rsu_bandwidth_mbps=85.0 if name != "high_load" else 42.0,
        edge_bandwidth_mbps=55.0 if name != "high_load" else 30.0,
        channel_quality=channel_quality,
        packet_loss=packet_loss,
        base_latency_ms=8.0 if name != "high_load" else 16.0,
    )
    edge = EdgeState(
        rsu_compute_ghz=42.0,
        edge_compute_ghz=92.0,
        rsu_queue=rsu_queue,
        edge_queue=edge_queue,
        rsu_utilization=rsu_utilization,
        edge_utilization=edge_utilization,
    )
    return SceneState(
        name=name,
        tick_ms=seed * 100,
        vehicle=vehicle,
        task=task,
        network=network,
        edge=edge,
    )
