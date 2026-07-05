"""意图模块：负责显式意图输入、隐式意图推断、冲突消解和动态权重生成。"""

from __future__ import annotations

from collections import defaultdict

from imsdt_demo.models import Intent, IntentKind, IntentProfile, SceneState


BASE_WEIGHTS = {
    "latency": 0.35,
    "energy": 0.20,
    "reliability": 0.20,
    "priority": 0.15,
    "load_balance": 0.10,
}


INTENT_WEIGHT_HINTS = {
    IntentKind.LOW_LATENCY: {
        "latency": 0.55,
        "energy": 0.10,
        "reliability": 0.20,
        "priority": 0.10,
        "load_balance": 0.05,
    },
    IntentKind.LOW_ENERGY: {
        "latency": 0.22,
        "energy": 0.50,
        "reliability": 0.15,
        "priority": 0.08,
        "load_balance": 0.05,
    },
    IntentKind.EMERGENCY_PRIORITY: {
        "latency": 0.36,
        "energy": 0.04,
        "reliability": 0.24,
        "priority": 0.32,
        "load_balance": 0.04,
    },
    IntentKind.LOAD_BALANCE: {
        "latency": 0.20,
        "energy": 0.12,
        "reliability": 0.20,
        "priority": 0.08,
        "load_balance": 0.40,
    },
}


def explicit_intents_for_scene(scene: SceneState) -> list[Intent]:
    """用场景名模拟结构化显式意图，避免 demo 依赖大语言模型。"""

    if scene.name == "low_energy":
        return [
            Intent(
                kind=IntentKind.LOW_ENERGY,
                source="user",
                subject=scene.vehicle.vehicle_id,
                priority=8,
                confidence=0.95,
                deadline_ms=None,
                reason="车辆处于低电量策略场景，优先降低能耗。",
            )
        ]
    if scene.name == "emergency":
        return [
            Intent(
                kind=IntentKind.EMERGENCY_PRIORITY,
                source="vehicle",
                subject=scene.vehicle.vehicle_id,
                priority=10,
                confidence=0.98,
                deadline_ms=scene.task.deadline_ms,
                reason="紧急感知任务要求低时延和高可靠。",
            )
        ]
    return [
        Intent(
            kind=IntentKind.LOW_LATENCY,
            source="user",
            subject=scene.vehicle.vehicle_id,
            priority=6,
            confidence=0.90,
            deadline_ms=scene.task.deadline_ms,
            reason="普通计算任务默认优先降低完成时延。",
        )
    ]


def infer_implicit_intents(scene: SceneState) -> list[Intent]:
    """根据状态阈值推断隐式意图，覆盖低电量、紧急任务和资源保护。"""

    inferred: list[Intent] = []
    if scene.vehicle.battery_percent < 25.0:
        inferred.append(
            Intent(
                kind=IntentKind.LOW_ENERGY,
                source="system",
                subject=scene.vehicle.vehicle_id,
                priority=7,
                confidence=0.88,
                deadline_ms=None,
                reason="车辆电量低于 25%，推断低能耗意图。",
            )
        )
    if scene.task.priority >= 9 or "emergency" in scene.task.task_type:
        inferred.append(
            Intent(
                kind=IntentKind.EMERGENCY_PRIORITY,
                source="system",
                subject=scene.task.task_id,
                priority=10,
                confidence=0.93,
                deadline_ms=scene.task.deadline_ms,
                reason="任务优先级较高，推断紧急任务优先意图。",
            )
        )
    if scene.edge.rsu_utilization > 0.80 or scene.edge.edge_utilization > 0.85:
        inferred.append(
            Intent(
                kind=IntentKind.LOAD_BALANCE,
                source="system",
                subject="edge-cluster",
                priority=6,
                confidence=0.84,
                deadline_ms=None,
                reason="边缘资源接近过载，推断负载均衡保护意图。",
            )
        )
    return inferred


def resolve_intents(intents: list[Intent], scene: SceneState) -> IntentProfile:
    """合并多源意图并生成动态目标权重和硬约束。"""

    if not intents:
        intents = explicit_intents_for_scene(scene)

    weighted = defaultdict(float)
    total_strength = 0.0
    dominant = max(intents, key=lambda item: item.priority * item.confidence)
    hard_latency: float | None = None
    hard_reliability: float | None = None
    explanations: list[str] = []

    for intent in intents:
        strength = max(0.01, intent.priority * intent.confidence)
        total_strength += strength
        hints = INTENT_WEIGHT_HINTS[intent.kind]
        for key, value in hints.items():
            weighted[key] += value * strength
        explanations.append(intent.reason)

        # 硬约束只从紧急和显式截止时间中产生，避免普通偏好误伤策略空间。
        if intent.kind == IntentKind.EMERGENCY_PRIORITY:
            hard_latency = min(
                hard_latency if hard_latency is not None else scene.task.deadline_ms,
                intent.deadline_ms or scene.task.deadline_ms,
            )
            hard_reliability = max(hard_reliability or 0.0, 0.97)
        elif intent.deadline_ms is not None and intent.kind == IntentKind.LOW_LATENCY:
            hard_latency = min(
                hard_latency if hard_latency is not None else intent.deadline_ms,
                intent.deadline_ms,
            )

    weights = {
        key: weighted[key] / total_strength if total_strength > 0 else value
        for key, value in BASE_WEIGHTS.items()
    }
    weight_sum = sum(weights.values())
    weights = {key: value / weight_sum for key, value in weights.items()}

    kinds = {intent.kind for intent in intents}
    if IntentKind.LOW_LATENCY in kinds and IntentKind.LOW_ENERGY in kinds:
        explanations.append("检测到低时延与低能耗冲突，按优先级和置信度折中生成权重。")
    if IntentKind.EMERGENCY_PRIORITY in kinds and IntentKind.LOAD_BALANCE in kinds:
        explanations.append("检测到紧急任务与负载均衡冲突，紧急硬约束优先。")

    return IntentProfile(
        weights=weights,
        hard_latency_ms=hard_latency,
        hard_reliability=hard_reliability,
        dominant_intent=dominant.kind,
        explanations=tuple(explanations),
    )
