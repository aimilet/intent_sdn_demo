"""核心数据模型：定义统一 Intent IR、策略候选和可解释决策输出。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SourceChannel(StrEnum):
    """原始意图的输入通道。"""

    TEXT = "text"
    VOICE = "voice"
    JSON = "json"


class ActorRole(StrEnum):
    """意图提交者角色，决定冲突仲裁时的来源等级。"""

    DISPATCHER = "dispatcher"
    OPERATOR = "operator"
    DRIVER = "driver"
    APPLICATION = "application"


class TrafficClass(StrEnum):
    """第一版支持的流量类别，与 Mininet 分类规则对应。"""

    EMERGENCY = "emergency"
    CONTROL = "control"
    NAVIGATION = "navigation"
    VIDEO = "video"
    ALL = "all"


class ServiceType(StrEnum):
    """固定业务服务类型，用于查询本地版本化 SLA 条目。"""

    EMERGENCY_V2X = "emergency_v2x"
    VEHICLE_CONTROL = "vehicle_control"
    NAVIGATION = "navigation"
    BACKGROUND_VIDEO = "background_video"


class SemanticMetric(StrEnum):
    """语义层允许表达的质量指标，不携带任何执行参数。"""

    LATENCY = "latency"
    BANDWIDTH = "bandwidth"
    RELIABILITY = "reliability"


class SemanticLevel(StrEnum):
    """语义质量等级；缺少数值时只保留等级，不推造阈值。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SemanticOrigin(StrEnum):
    """语义要求在原文中的来源。"""

    EXPLICIT = "explicit"
    INFERRED = "inferred"


class Objective(StrEnum):
    """可被策略模板覆盖的网络层目标。"""

    PRIORITIZE_TRAFFIC = "prioritize_traffic"
    MINIMIZE_LATENCY = "minimize_latency"
    RELIEVE_NETWORK_CONGESTION = "relieve_network_congestion"
    LIMIT_BACKGROUND_TRAFFIC = "limit_background_traffic"


class Strength(StrEnum):
    """区分不可静默降级的硬目标与可折中的软偏好。"""

    MUST = "must"
    PREFER = "prefer"


class Priority(StrEnum):
    """软目标加权和展示使用的相对优先级。"""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class ConstraintMetric(StrEnum):
    """受支持的显式数值约束指标。"""

    LATENCY_MS = "latency_ms"
    MIN_BANDWIDTH_MBPS = "min_bandwidth_mbps"
    MAX_BANDWIDTH_MBPS = "max_bandwidth_mbps"


class ConstraintOperator(StrEnum):
    """指标可接受的比较操作符。"""

    LESS_OR_EQUAL = "<="
    GREATER_OR_EQUAL = ">="


@dataclass(frozen=True)
class Scope:
    """意图作用范围：车辆列表可为空，业务类型始终明确。"""

    vehicle_ids: tuple[str, ...]
    traffic_class: TrafficClass

    def key(self) -> tuple[tuple[str, ...], TrafficClass]:
        """生成仲裁分组使用的稳定键。"""

        return (tuple(sorted(self.vehicle_ids)), self.traffic_class)

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可序列化的数据。"""

        return {
            "vehicle_ids": list(self.vehicle_ids),
            "traffic_class": self.traffic_class.value,
        }


@dataclass(frozen=True)
class Constraint:
    """结构化数值约束；具体合法性由 validation 模块保证。"""

    metric: ConstraintMetric
    operator: ConstraintOperator
    value: float
    unit: str

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可序列化的数据。"""

        return {
            "metric": self.metric.value,
            "operator": self.operator.value,
            "value": self.value,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class Intent:
    """单条统一意图，保留语义、显式约束、证据与不确定性。"""

    scope: Scope
    objective: Objective
    strength: Strength
    priority: Priority
    constraints: tuple[Constraint, ...]
    evidence: tuple[str, ...]
    ambiguities: tuple[str, ...]
    # 这两个字段追加在旧字段之后，保证已有 Python 调用方的构造顺序不变。
    service: ServiceType | None = None
    semantic_requirements: tuple["SemanticRequirement", ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可序列化的数据。"""

        payload = {
            "scope": self.scope.to_dict(),
            "objective": self.objective.value,
            "strength": self.strength.value,
            "priority": self.priority.value,
            "constraints": [item.to_dict() for item in self.constraints],
            "evidence": list(self.evidence),
            "ambiguities": list(self.ambiguities),
            "semantic_requirements": [item.to_dict() for item in self.semantic_requirements],
        }
        # 旧版直接构造的 Intent 没有 service；省略该键可由校验器按 traffic_class 补齐。
        if self.service is not None:
            payload["service"] = (
                self.service.value if isinstance(self.service, ServiceType) else self.service
            )
        return payload


@dataclass(frozen=True)
class IntentEnvelope:
    """一次请求的规范化意图集合，是解析与仲裁之间的唯一契约。"""

    request_id: str
    source_channel: SourceChannel
    actor_role: ActorRole
    original_text: str
    intents: tuple[Intent, ...]

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可序列化的数据。"""

        return {
            "request_id": self.request_id,
            "source_channel": self.source_channel.value,
            "actor_role": self.actor_role.value,
            "original_text": self.original_text,
            "intents": [item.to_dict() for item in self.intents],
        }


@dataclass(frozen=True)
class PolicyAction:
    """仅由模板生成的内部动作，不携带未经验证的外部命令。"""

    action_type: str
    resource: str
    parameters: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 预览结构。"""

        return {
            "action_type": self.action_type,
            "resource": self.resource,
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class CandidatePlan:
    """白名单策略模板实例，包含其目标覆盖能力和固定动作集。"""

    plan_id: str
    description: str
    supported_objectives: frozenset[Objective]
    guarantees: tuple[Constraint, ...]
    actions: tuple[PolicyAction, ...]

    @property
    def change_count(self) -> int:
        """返回策略变更量，用于同分候选的最小化选择。"""

        return len(self.actions)

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 预览结构。"""

        return {
            "plan_id": self.plan_id,
            "description": self.description,
            "supported_objectives": sorted(item.value for item in self.supported_objectives),
            "guarantees": [item.to_dict() for item in self.guarantees],
            "actions": [item.to_dict() for item in self.actions],
            "change_count": self.change_count,
        }


@dataclass(frozen=True)
class SourcedIntent:
    """将单条意图与其提交角色绑定，供跨请求汇总仲裁保留来源信息。"""

    actor_role: ActorRole
    intent: Intent

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可序列化的数据。"""

        return {"actor_role": self.actor_role.value, "intent": self.intent.to_dict()}


@dataclass(frozen=True)
class SuppressedIntent:
    """记录因冲突仲裁而未参与策略选择的带来源意图及原因。"""

    sourced_intent: SourcedIntent
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可序列化的数据。"""

        return {"sourced_intent": self.sourced_intent.to_dict(), "reason": self.reason}


@dataclass(frozen=True)
class SemanticRequirement:
    """显式或推断出的语义要求，严格限制为可展示的非数值信息。"""

    metric: SemanticMetric
    level: SemanticLevel
    origin: SemanticOrigin
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        """转换为前端可展示的语义证据。"""

        return {
            "metric": self.metric.value,
            "level": self.level.value,
            "origin": self.origin.value,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class GroundingRecord:
    """服务端依据只读 SLA 条目生成的知识落地证据。"""

    service: ServiceType
    profile_id: str
    profile_version: str
    derived_constraints: tuple[Constraint, ...]
    preference_order: tuple[str, ...]
    reason: str
    conflicts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 证据结构；该记录不包含可执行命令。"""

        return {
            "service": self.service.value,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "derived_constraints": [item.to_dict() for item in self.derived_constraints],
            "preference_order": list(self.preference_order),
            "reason": self.reason,
            "conflicts": list(self.conflicts),
        }


@dataclass(frozen=True)
class ArbitrationResult:
    """仲裁输出：ready 可继续编译，blocked 时必须停止下发。"""

    status: str
    active_intents: tuple[SourcedIntent, ...]
    suppressed_intents: tuple[SuppressedIntent, ...]
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可序列化的数据。"""

        return {
            "status": self.status,
            "active_intents": [item.to_dict() for item in self.active_intents],
            "suppressed_intents": [item.to_dict() for item in self.suppressed_intents],
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class CandidateEvaluation:
    """候选的可行性、硬目标覆盖和软目标覆盖率。"""

    plan: CandidatePlan
    feasible: bool
    hard_satisfied: bool
    soft_coverage: float
    rejection_reasons: tuple[str, ...]
    # 评价器输出的来源和 KPI 状态均为展示数据，不能直接成为执行参数。
    evaluation_source: str = "deterministic_configuration"
    dynamic_kpis: dict[str, object] = field(
        default_factory=lambda: {
            "emergency_p95_latency_ms": "not_available",
            "throughput_mbps": "not_available",
            "packet_loss_percent": "not_available",
        }
    )
    utility_score: float = 0.0
    utility_breakdown: tuple[tuple[str, float], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可序列化的数据。"""

        return {
            "plan": self.plan.to_dict(),
            "feasible": self.feasible,
            "hard_satisfied": self.hard_satisfied,
            "soft_coverage": round(self.soft_coverage, 4),
            "rejection_reasons": list(self.rejection_reasons),
            "evaluation_source": self.evaluation_source,
            "dynamic_kpis": self.dynamic_kpis,
            "utility_score": round(self.utility_score, 4),
            "utility_breakdown": {
                key: round(value, 4) for key, value in self.utility_breakdown
            },
        }


@dataclass(frozen=True)
class DecisionBundle:
    """最终策略决策，包含仲裁过程、全部候选和选择结果。"""

    status: str
    arbitration: ArbitrationResult
    candidates: tuple[CandidateEvaluation, ...]
    selected_plan: CandidatePlan | None
    selection_reason: str
    grounding: tuple[GroundingRecord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可序列化的数据。"""

        return {
            "status": self.status,
            "arbitration": self.arbitration.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
            "selected_plan": self.selected_plan.to_dict() if self.selected_plan else None,
            "selection_reason": self.selection_reason,
            "grounding": [item.to_dict() for item in self.grounding],
        }


@dataclass(frozen=True)
class TrafficMetrics:
    """一次固定流量场景的实测指标，供基线与策略后结果成对展示。"""

    emergency_p95_latency_ms: float | None
    throughput_mbps: dict[str, float]
    packet_loss_percent: dict[str, float]
    link_utilization_percent: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 和前端可消费的数据。"""

        return {
            "emergency_p95_latency_ms": self.emergency_p95_latency_ms,
            "throughput_mbps": self.throughput_mbps,
            "packet_loss_percent": self.packet_loss_percent,
            "link_utilization_percent": self.link_utilization_percent,
        }


@dataclass(frozen=True)
class MetricSnapshot:
    """一次 Mininet 验证的基线与策略后指标，禁止以模拟数值代替任一侧。"""

    plan_id: str
    baseline: TrafficMetrics
    applied: TrafficMetrics

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 和前端可消费的数据。"""

        return {
            "plan_id": self.plan_id,
            "baseline": self.baseline.to_dict(),
            "applied": self.applied.to_dict(),
        }
