"""策略编译模块：从固定白名单模板生成候选，并按硬目标与软目标稳定择优。"""

from __future__ import annotations

from intent_sdn_demo.arbitration import priority_rank, role_rank
from intent_sdn_demo.models import (
    CandidateEvaluation,
    CandidatePlan,
    Constraint,
    ConstraintMetric,
    ConstraintOperator,
    DecisionBundle,
    Intent,
    Objective,
    PolicyAction,
    SourcedIntent,
    Strength,
)
from intent_sdn_demo.topology import TopologyInventory


class PolicyCompiler:
    """将仲裁结果映射到有限策略模板，避免任何外部输入影响执行动作形态。"""

    def __init__(self, topology: TopologyInventory) -> None:
        self._topology = topology

    def decide(self, arbitration) -> DecisionBundle:
        """评估全部模板，返回可审计的最终决策或显式阻断原因。"""

        if arbitration.status != "ready":
            return DecisionBundle(
                status="blocked",
                arbitration=arbitration,
                candidates=(),
                selected_plan=None,
                selection_reason="存在未消解的歧义或硬冲突，禁止生成下发策略。",
            )

        evaluations = tuple(
            self._evaluate(template, arbitration.active_intents)
            for template in _templates()
        )
        viable = [item for item in evaluations if item.feasible and item.hard_satisfied]
        if not viable:
            return DecisionBundle(
                status="blocked",
                arbitration=arbitration,
                candidates=evaluations,
                selected_plan=None,
                selection_reason="没有候选能够覆盖全部硬目标或数值约束，禁止下发。",
            )

        selected = sorted(
            viable,
            key=lambda item: (-item.soft_coverage, item.plan.change_count, item.plan.plan_id),
        )[0]
        return DecisionBundle(
            status="ready",
            arbitration=arbitration,
            candidates=evaluations,
            selected_plan=selected.plan,
            selection_reason=(
                f"{selected.plan.plan_id} 覆盖全部硬目标，软目标加权覆盖率 "
                f"{selected.soft_coverage:.2f}，变更 {selected.plan.change_count} 项。"
            ),
        )

    def _evaluate(
        self, plan: CandidatePlan, intents: tuple[SourcedIntent, ...]
    ) -> CandidateEvaluation:
        """在不执行网络动作的前提下计算候选可行性和意图覆盖情况。"""

        reasons: list[str] = []
        feasible = plan.plan_id in self._topology.plan_ids and all(
            action.resource in self._topology.resources for action in plan.actions
        )
        if not feasible:
            reasons.append("模板引用了当前拓扑不存在的策略资源。")

        hard_intents = [item for item in intents if item.intent.strength is Strength.MUST]
        soft_intents = [item for item in intents if item.intent.strength is Strength.PREFER]
        hard_satisfied = all(_covers(plan, item.intent) for item in hard_intents)
        if not hard_satisfied:
            reasons.append("未覆盖全部硬目标或显式数值约束。")

        total_weight = 0
        covered_weight = 0
        for sourced_intent in soft_intents:
            intent = sourced_intent.intent
            weight = role_rank(sourced_intent.actor_role) * priority_rank(intent.priority)
            total_weight += weight
            if _covers(plan, intent):
                covered_weight += weight
        soft_coverage = covered_weight / total_weight if total_weight else 0.0

        return CandidateEvaluation(
            plan=plan,
            feasible=feasible,
            hard_satisfied=hard_satisfied,
            soft_coverage=soft_coverage,
            rejection_reasons=tuple(reasons),
        )


def _covers(plan: CandidatePlan, intent: Intent) -> bool:
    """判断模板是否声明支持目标，并满足其全部显式数值约束。"""

    return intent.objective in plan.supported_objectives and all(
        _guarantee_satisfies(plan.guarantees, constraint) for constraint in intent.constraints
    )


def _guarantee_satisfies(
    guarantees: tuple[Constraint, ...], requested: Constraint
) -> bool:
    """仅按模板声明能力核验数值，绝不从用户输入生成新的限速值。"""

    matching = [item for item in guarantees if item.metric is requested.metric]
    if not matching:
        return False
    if requested.metric is ConstraintMetric.LATENCY_MS:
        return any(item.value <= requested.value for item in matching)
    if requested.metric is ConstraintMetric.MIN_BANDWIDTH_MBPS:
        return any(item.value >= requested.value for item in matching)
    if requested.metric is ConstraintMetric.MAX_BANDWIDTH_MBPS:
        return any(item.value <= requested.value for item in matching)
    return False


def _templates() -> tuple[CandidatePlan, ...]:
    """构造全部固定模板；此处是唯一允许出现路径队列和带宽常量的位置。"""

    baseline = CandidatePlan(
        plan_id="baseline",
        description="保留当前流表和 QoS，不进行策略变更。",
        supported_objectives=frozenset(),
        guarantees=(),
        actions=(),
    )
    critical_actions = (
        PolicyAction(
            "flow", "rsu", (("match", "udp,tp_dst=5001"), ("path", "low-latency-path"))
        ),
        PolicyAction(
            "queue", "low-latency-path", (("queue_id", "1"), ("min_rate_mbps", "12")))
    )
    critical = CandidatePlan(
        plan_id="critical_priority",
        description="紧急业务走低时延路径，并绑定高优先级出口队列。",
        supported_objectives=frozenset(
            {Objective.PRIORITIZE_TRAFFIC, Objective.MINIMIZE_LATENCY}
        ),
        guarantees=(
            Constraint(ConstraintMetric.LATENCY_MS, ConstraintOperator.LESS_OR_EQUAL, 20, "ms"),
            Constraint(
                ConstraintMetric.MIN_BANDWIDTH_MBPS,
                ConstraintOperator.GREATER_OR_EQUAL,
                12,
                "Mbps",
            ),
        ),
        actions=critical_actions,
    )
    congestion_actions = (
        PolicyAction(
            "flow", "rsu", (("match", "udp,tp_dst=5004"), ("path", "high-capacity-path"))
        ),
        PolicyAction(
            "qos",
            "high-capacity-path",
            (("traffic_class", "video"), ("max_rate_mbps", "8")),
        ),
        PolicyAction(
            "meter",
            "rsu",
            (("meter_id", "2"), ("max_rate_mbps", "8")),
        ),
    )
    congestion = CandidatePlan(
        plan_id="congestion_relief",
        description="背景视频改走高容量路径，并进行固定出口限速。",
        supported_objectives=frozenset(
            {Objective.RELIEVE_NETWORK_CONGESTION, Objective.LIMIT_BACKGROUND_TRAFFIC}
        ),
        guarantees=(
            Constraint(
                ConstraintMetric.MAX_BANDWIDTH_MBPS,
                ConstraintOperator.LESS_OR_EQUAL,
                8,
                "Mbps",
            ),
        ),
        actions=congestion_actions,
    )
    combined = CandidatePlan(
        plan_id="combined",
        description="同时保障紧急业务并治理背景视频流量。",
        supported_objectives=critical.supported_objectives | congestion.supported_objectives,
        guarantees=critical.guarantees + congestion.guarantees,
        actions=critical.actions + congestion.actions,
    )
    return (baseline, critical, congestion, combined)
