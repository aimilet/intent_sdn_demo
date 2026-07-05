"""多车辆任务生成模块：为同一车联网场景构建批量调度任务队列。"""

from __future__ import annotations

from imsdt_demo.models import QueuedTask, SceneState, Task, VehicleState


def generate_task_queue(scene: SceneState) -> tuple[QueuedTask, ...]:
    """生成多车辆任务队列，焦点车辆和环境车辆共同参与批量调度。"""

    queue = (
        QueuedTask(
            queue_id="q-focus",
            vehicle=scene.vehicle,
            task=scene.task,
            role="focus",
            vehicle_type="emergency" if scene.name == "emergency" else "focus",
            path=tuple(_vehicle_path()),
        ),
        _queued_task(
            "q-014",
            "veh-014",
            "background",
            "car",
            73.0,
            11.8,
            17.5,
            "map_update",
            0.80,
            0.55,
            220.0,
            3,
            _straight_path(14, 76, 78, 64, drift=-2),
        ),
        _queued_task(
            "q-027",
            "veh-027",
            "background",
            "car",
            64.0,
            9.6,
            15.0,
            "infotainment",
            2.40,
            0.90,
            450.0,
            2,
            _straight_path(65, 88, 56, 23, drift=1),
        ),
        _queued_task(
            "q-033",
            "veh-033",
            "background",
            "truck",
            81.0,
            8.4,
            21.0,
            "cooperative_perception",
            1.70,
            1.15,
            180.0,
            6,
            _straight_path(9, 33, 70, 24, drift=2),
        ),
        _queued_task(
            "q-041",
            "veh-041",
            "background",
            "ev",
            19.0,
            10.1,
            16.5,
            "diagnostics",
            0.60,
            0.45,
            260.0,
            4,
            _straight_path(25, 86, 31, 26, drift=-1),
        ),
        _queued_task(
            "q-052",
            "veh-052",
            "background",
            "car",
            58.0,
            13.2,
            18.0,
            "cache_prefetch",
            1.10,
            0.72,
            300.0,
            3,
            _straight_path(39, 70, 91, 58, drift=-1),
        ),
    )
    _validate_queue(queue)
    return queue


def _queued_task(
    queue_id: str,
    vehicle_id: str,
    role: str,
    vehicle_type: str,
    battery_percent: float,
    speed_mps: float,
    local_compute_ghz: float,
    task_type: str,
    size_mb: float,
    cpu_cycles_g: float,
    deadline_ms: float,
    priority: int,
    path: list[dict[str, float]],
) -> QueuedTask:
    """构造环境车辆任务，保持车辆和任务字段完整。"""

    vehicle = VehicleState(
        vehicle_id=vehicle_id,
        position_m=path[0]["x"] * 25.0,
        speed_mps=speed_mps,
        battery_percent=battery_percent,
        local_compute_ghz=local_compute_ghz,
        density=0.0,
    )
    task = Task(
        task_id=f"task-{vehicle_id}",
        size_mb=size_mb,
        cpu_cycles_g=cpu_cycles_g,
        deadline_ms=deadline_ms,
        priority=priority,
        task_type=task_type,
    )
    return QueuedTask(
        queue_id=queue_id,
        vehicle=vehicle,
        task=task,
        role=role,
        vehicle_type=vehicle_type,
        path=tuple(path),
    )


def _validate_queue(queue: tuple[QueuedTask, ...]) -> None:
    """校验生成队列，避免无效任务流入调度器。"""

    seen_ids: set[str] = set()
    for item in queue:
        if item.queue_id in seen_ids:
            raise ValueError(f"重复任务队列编号: {item.queue_id}")
        seen_ids.add(item.queue_id)
        if item.task.size_mb <= 0 or item.task.cpu_cycles_g <= 0:
            raise ValueError(f"任务规模必须为正数: {item.task.task_id}")
        if item.task.deadline_ms <= 0:
            raise ValueError(f"任务截止时间必须为正数: {item.task.task_id}")
        if len(item.path) < 2:
            raise ValueError(f"车辆轨迹至少需要两个点: {item.vehicle.vehicle_id}")


def _vehicle_path() -> list[dict[str, float]]:
    """焦点车辆移动轨迹，与前端 10 个播放步骤保持一致。"""

    return [
        {"x": 8, "y": 73},
        {"x": 14, "y": 71},
        {"x": 20, "y": 69},
        {"x": 27, "y": 67},
        {"x": 34, "y": 64},
        {"x": 41, "y": 62},
        {"x": 48, "y": 60},
        {"x": 55, "y": 59},
        {"x": 62, "y": 61},
        {"x": 69, "y": 64},
    ]


def _straight_path(
    start_x: float, start_y: float, end_x: float, end_y: float, *, drift: float
) -> list[dict[str, float]]:
    """按 10 个播放步骤生成环境车辆移动轨迹。"""

    points: list[dict[str, float]] = []
    for index in range(10):
        ratio = index / 9
        wave = (index % 3 - 1) * drift * 0.28
        points.append(
            {
                "x": start_x + (end_x - start_x) * ratio,
                "y": start_y + (end_y - start_y) * ratio + wave,
            }
        )
    return points
