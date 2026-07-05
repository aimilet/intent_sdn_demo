"""批量调度模块：一次性处理多车辆任务队列并维护共享资源占用。"""

from __future__ import annotations

from dataclasses import replace

from imsdt_demo.agents import CandidateCoordinator
from imsdt_demo.intent import infer_implicit_intents, resolve_intents
from imsdt_demo.models import (
    BatchScheduleResult,
    CandidatePlan,
    EdgeState,
    EvaluationResult,
    Intent,
    IntentKind,
    PredictionResult,
    QueuedTask,
    SceneState,
    ScheduledTask,
    SyncState,
    Target,
)
from imsdt_demo.twin import DigitalTwinEvaluator, clamp, simulate_execution


class BatchScheduler:
    """多车辆任务批量调度器：复用单任务评估内核并加入共享资源记账。"""

    def __init__(self) -> None:
        self.coordinator = CandidateCoordinator()
        self.evaluator = DigitalTwinEvaluator()

    def schedule(
        self,
        scene: SceneState,
        queue: tuple[QueuedTask, ...],
        sync_state: SyncState,
        prediction: PredictionResult,
        memory_plans: dict[str, list[CandidatePlan]] | None = None,
    ) -> BatchScheduleResult:
        """一次性调度整个任务队列，输出逐任务决策和批量聚合指标。"""

        if not queue:
            raise ValueError("批量调度队列不能为空。")

        memory_plans = memory_plans or {}
        scheduled: list[ScheduledTask] = []
        reservations = {"rsu_queue": 0, "edge_queue": 0, "rsu_util": 0.0, "edge_util": 0.0}
        sorted_queue = sorted(
            queue,
            key=lambda item: (-item.task.priority, item.task.deadline_ms, item.queue_id),
        )

        for order, item in enumerate(sorted_queue, start=1):
            task_scene = self._scene_for_item(scene, item, reservations)
            intents = self._intents_for_item(task_scene, item)
            profile = resolve_intents(intents + infer_implicit_intents(task_scene), task_scene)
            candidates = self.coordinator.build_candidates(
                task_scene,
                profile,
                memory_plans.get(item.queue_id),
            )
            evaluations = tuple(
                self.evaluator.evaluate(candidate, task_scene, profile, prediction, sync_state)
                for candidate in candidates
            )
            selected = self._select_best(evaluations)
            execution = simulate_execution(selected, task_scene)
            scheduled.append(
                ScheduledTask(
                    queue_item=item,
                    profile=profile,
                    evaluations=evaluations,
                    selected=selected,
                    execution=execution,
                    order=order,
                )
            )
            self._reserve(selected, reservations)

        return self._result(queue, tuple(scheduled))

    def _scene_for_item(
        self, scene: SceneState, item: QueuedTask, reservations: dict[str, float]
    ) -> SceneState:
        """为队列项构造临时场景，并注入当前批次已占用资源。"""

        edge = EdgeState(
            rsu_compute_ghz=scene.edge.rsu_compute_ghz,
            edge_compute_ghz=scene.edge.edge_compute_ghz,
            rsu_queue=scene.edge.rsu_queue + int(reservations["rsu_queue"]),
            edge_queue=scene.edge.edge_queue + int(reservations["edge_queue"]),
            rsu_utilization=clamp(scene.edge.rsu_utilization + reservations["rsu_util"]),
            edge_utilization=clamp(scene.edge.edge_utilization + reservations["edge_util"]),
        )
        vehicle = replace(item.vehicle, density=scene.vehicle.density)
        return replace(scene, vehicle=vehicle, task=item.task, edge=edge)

    def _intents_for_item(self, scene: SceneState, item: QueuedTask) -> list[Intent]:
        """根据任务和车辆状态生成结构化显式意图。"""

        if item.task.priority >= 9 or "emergency" in item.task.task_type:
            return [
                Intent(
                    kind=IntentKind.EMERGENCY_PRIORITY,
                    source="vehicle",
                    subject=item.vehicle.vehicle_id,
                    priority=10,
                    confidence=0.98,
                    deadline_ms=item.task.deadline_ms,
                    reason=f"{item.vehicle.vehicle_id} 的高优先级任务需要紧急保障。",
                )
            ]
        if item.vehicle.battery_percent < 25.0:
            return [
                Intent(
                    kind=IntentKind.LOW_ENERGY,
                    source="vehicle",
                    subject=item.vehicle.vehicle_id,
                    priority=8,
                    confidence=0.92,
                    deadline_ms=None,
                    reason=f"{item.vehicle.vehicle_id} 电量较低，优先降低车辆侧能耗。",
                )
            ]
        return [
            Intent(
                kind=IntentKind.LOW_LATENCY,
                source="vehicle",
                subject=item.vehicle.vehicle_id,
                priority=max(4, item.task.priority),
                confidence=0.88,
                deadline_ms=item.task.deadline_ms,
                reason=f"{item.vehicle.vehicle_id} 请求尽快完成 {item.task.task_type} 任务。",
            )
        ]

    def _select_best(self, evaluations: tuple[EvaluationResult, ...]) -> EvaluationResult:
        """选择批量调度中单个任务的最优候选。"""

        if not evaluations:
            raise ValueError("没有可评估的候选策略。")

        def score(item: EvaluationResult) -> tuple[int, float, float]:
            feasible = 0 if item.sla_violation else 1
            return (feasible, item.intent_satisfaction, -item.risk_score)

        return max(evaluations, key=score)

    def _reserve(self, selected: EvaluationResult, reservations: dict[str, float]) -> None:
        """根据已选策略更新本批次共享资源占用。"""

        if selected.plan.target == Target.RSU:
            reservations["rsu_queue"] += 1
            reservations["rsu_util"] += 0.035 + selected.plan.compute_share * 0.025
        elif selected.plan.target == Target.EDGE:
            reservations["edge_queue"] += 1
            reservations["edge_util"] += 0.030 + selected.plan.compute_share * 0.020

    def _result(
        self, queue: tuple[QueuedTask, ...], scheduled: tuple[ScheduledTask, ...]
    ) -> BatchScheduleResult:
        """生成批量调度聚合指标。"""

        count = len(scheduled)
        target_counts = {target.value: 0 for target in Target}
        for item in scheduled:
            target_counts[item.selected.plan.target.value] += 1

        return BatchScheduleResult(
            queue=queue,
            scheduled=scheduled,
            average_latency_ms=sum(item.execution.latency_ms for item in scheduled) / count,
            total_energy_j=sum(item.execution.energy_j for item in scheduled),
            average_satisfaction=sum(item.execution.intent_satisfaction for item in scheduled) / count,
            sla_violation_rate=sum(item.execution.sla_violation for item in scheduled) / count,
            target_counts=target_counts,
        )
