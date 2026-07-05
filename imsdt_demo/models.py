"""核心数据模型：定义场景、意图、候选方案、评估结果和历史案例的数据结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class IntentKind(StrEnum):
    """MVP 支持的意图类型，另保留负载均衡供隐式意图使用。"""

    LOW_LATENCY = "low_latency"
    LOW_ENERGY = "low_energy"
    EMERGENCY_PRIORITY = "emergency_priority"
    LOAD_BALANCE = "load_balance"


class Target(StrEnum):
    """任务可执行位置，覆盖当前 demo 的本地、路侧单元和边缘云。"""

    LOCAL = "local"
    RSU = "rsu"
    EDGE = "edge"


@dataclass(frozen=True)
class VehicleState:
    """车辆状态：为意图推断、任务执行和同步误差计算提供输入。"""

    vehicle_id: str
    position_m: float
    speed_mps: float
    battery_percent: float
    local_compute_ghz: float
    density: float


@dataclass(frozen=True)
class Task:
    """计算任务：描述任务规模、算力需求、截止时间和优先级。"""

    task_id: str
    size_mb: float
    cpu_cycles_g: float
    deadline_ms: float
    priority: int
    task_type: str


@dataclass(frozen=True)
class NetworkState:
    """网络状态：描述车辆到 RSU、边缘云链路的带宽、时延和可靠性。"""

    rsu_bandwidth_mbps: float
    edge_bandwidth_mbps: float
    channel_quality: float
    packet_loss: float
    base_latency_ms: float


@dataclass(frozen=True)
class EdgeState:
    """边缘资源状态：描述 RSU 和边缘云的可用算力与排队压力。"""

    rsu_compute_ghz: float
    edge_compute_ghz: float
    rsu_queue: int
    edge_queue: int
    rsu_utilization: float
    edge_utilization: float


@dataclass(frozen=True)
class SceneState:
    """当前场景状态：聚合车辆、任务、网络和边缘资源状态。"""

    name: str
    tick_ms: int
    vehicle: VehicleState
    task: Task
    network: NetworkState
    edge: EdgeState


@dataclass(frozen=True)
class Intent:
    """结构化意图：由显式输入或状态规则推断得到。"""

    kind: IntentKind
    source: str
    subject: str
    priority: int
    confidence: float
    deadline_ms: float | None
    reason: str


@dataclass(frozen=True)
class IntentProfile:
    """消解后的意图画像：包含动态权重、硬约束和解释。"""

    weights: dict[str, float]
    hard_latency_ms: float | None
    hard_reliability: float | None
    dominant_intent: IntentKind
    explanations: tuple[str, ...]


@dataclass(frozen=True)
class CandidatePlan:
    """候选决策方案：描述卸载位置、资源分配和策略来源。"""

    plan_id: str
    target: Target
    bandwidth_mbps: float
    compute_share: float
    queue_priority: int
    source: str
    explanation: str


@dataclass(frozen=True)
class SyncState:
    """实时同步状态：衡量数字孪生与真实状态的对齐质量。"""

    tick_ms: int
    data_delay_ms: float
    missing_ratio: float
    state_error: float
    quality: float


@dataclass(frozen=True)
class PredictionResult:
    """未来预测结果：描述短期负载、链路和任务风险。"""

    window_s: int
    future_edge_utilization: float
    future_rsu_utilization: float
    task_arrival_rate: float
    link_degradation_risk: float
    overload_risk: float
    timeout_risk: float
    confidence: float


@dataclass(frozen=True)
class EvaluationResult:
    """数字孪生评估结果：用于排序、解释和历史案例写回。"""

    plan: CandidatePlan
    latency_ms: float
    energy_j: float
    reliability: float
    resource_cost: float
    intent_satisfaction: float
    sla_violation: bool
    risk_score: float
    explanation: str


@dataclass(frozen=True)
class ExecutionResult:
    """执行结果：模拟真实执行后用于闭环反馈和案例置信度更新。"""

    latency_ms: float
    energy_j: float
    reliability: float
    intent_satisfaction: float
    sla_violation: bool
    prediction_error: float


@dataclass
class HistoryCase:
    """历史案例：保存相似检索、策略复用和执行反馈所需的最小信息。"""

    case_id: str
    scene_signature: dict[str, float]
    intent_vector: dict[str, float]
    plan: CandidatePlan
    evaluation: EvaluationResult
    execution: ExecutionResult
    prediction_error: float
    confidence: float
    timestamp_ms: int
    tags: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        """转换为 JSON 可写结构，避免在存储层处理 dataclass 细节。"""

        data = asdict(self)
        data["plan"]["target"] = self.plan.target.value
        return data
