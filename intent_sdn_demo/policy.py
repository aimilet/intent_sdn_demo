"""受控策略流水线：候选生成、确定性评价与可解释稳定排序分层实现。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Protocol

from intent_sdn_demo.arbitration import priority_rank, role_rank
from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.models import (
    CandidateEvaluation,
    CandidatePlan,
    Constraint,
    ConstraintMetric,
    ConstraintOperator,
    DecisionBundle,
    GroundingRecord,
    Intent,
    Objective,
    PolicyAction,
    SourcedIntent,
    Strength,
    TrafficClass,
)
from intent_sdn_demo.topology import TopologyInventory, default_topology


class CandidateGenerator:
    """只生成固定四类白名单计划，不读取外部文本或 Grounding 数值。"""

    def __init__(self, topology: TopologyInventory | None = None) -> None:
        """保存拓扑资源清单，供上层评价器检查计划引用。"""

        self._topology = topology or default_topology()

    def generate(self, _arbitration=None) -> tuple[CandidatePlan, ...]:
        """返回固定顺序候选；参数仅用于保持未来按仲裁裁剪候选的接口稳定。"""

        return _templates()


class DecisionEvaluator(Protocol):
    """候选评价接口；后续模型评价器只能替换此接口。"""

    def evaluate(
        self,
        plan: CandidatePlan,
        intents: tuple[SourcedIntent, ...],
        groundings: Mapping[Intent, GroundingRecord] | None = None,
    ) -> CandidateEvaluation:
        """返回可行性、约束覆盖、评价来源和效用分解。"""


class DeterministicDecisionEvaluator:
    """仅依据白名单能力和 SLA 证据评价候选，不伪造动态 KPI 预测。"""

    def __init__(self, topology: TopologyInventory | None = None) -> None:
        """保存拓扑清单，确保候选动作资源不能越界。"""

        self._topology = topology or default_topology()

    def evaluate(
        self,
        plan: CandidatePlan,
        intents: tuple[SourcedIntent, ...],
        groundings: Mapping[Intent, GroundingRecord],
    ) -> CandidateEvaluation:
        """确定性计算硬约束、软覆盖率和效用分解。"""

        reasons: list[str] = []
        groundings = groundings or {}
        feasible = plan.plan_id in self._topology.plan_ids and all(
            action.resource in self._topology.resources for action in plan.actions
        )
        if not feasible:
            reasons.append("模板引用了当前拓扑不存在的策略资源。")

        hard_intents = [item for item in intents if item.intent.strength is Strength.MUST]
        soft_intents = [item for item in intents if item.intent.strength is Strength.PREFER]
        hard_satisfied = all(
            _covers(plan, item.intent, groundings.get(item.intent)) for item in hard_intents
        )
        if not hard_satisfied:
            reasons.append("未覆盖全部硬目标或显式数值约束。")
            if any(
                item.intent.objective in plan.supported_objectives
                and not _plan_supports_scope(plan, item.intent)
                for item in hard_intents
            ):
                reasons.append("固定动作不覆盖该业务范围与目标组合。")

        covered_weight = 0.0
        source_weight = 0.0
        sla_preference_weight = 0.0
        weighted_total = 0.0
        for sourced_intent in soft_intents:
            intent = sourced_intent.intent
            grounding = groundings.get(intent)
            preference_order = grounding.preference_order if grounding else ()
            preference_weight = _preference_weight(intent.objective, preference_order)
            weight = float(role_rank(sourced_intent.actor_role) * priority_rank(intent.priority))
            weighted_total += weight * preference_weight
            if _covers(plan, intent, grounding):
                covered_weight += weight * preference_weight
                source_weight += weight
                sla_preference_weight += preference_weight
        soft_coverage = covered_weight / weighted_total if weighted_total else 0.0
        hard_component = float(len(hard_intents)) if hard_satisfied else 0.0
        utility_score = hard_component * 1000.0 + covered_weight
        utility_breakdown = (
            ("hard_target_coverage", hard_component),
            ("soft_source_priority", source_weight),
            ("sla_preference", sla_preference_weight),
            ("soft_weighted_coverage", covered_weight),
            ("soft_weighted_total", weighted_total),
        )

        return CandidateEvaluation(
            plan=plan,
            feasible=feasible,
            hard_satisfied=hard_satisfied,
            soft_coverage=soft_coverage,
            rejection_reasons=tuple(reasons),
            evaluation_source="deterministic_configuration",
            dynamic_kpis=_not_available_kpis(),
            utility_score=utility_score,
            utility_breakdown=utility_breakdown,
        )


class StableDecisionSelector:
    """按效用、变更量和计划标识执行不依赖输入顺序的稳定选择。"""

    def select(self, evaluations: tuple[CandidateEvaluation, ...]) -> CandidateEvaluation | None:
        """从已过滤的可行候选中返回唯一稳定结果。"""

        viable = [item for item in evaluations if item.feasible and item.hard_satisfied]
        if not viable:
            return None
        return sorted(
            viable,
            key=lambda item: (-item.utility_score, item.plan.change_count, item.plan.plan_id),
        )[0]


class PolicyCompiler:
    """协调候选生成、评价和排序，并输出可审计 DecisionBundle。"""

    def __init__(
        self,
        topology: TopologyInventory,
        *,
        candidate_generator: CandidateGenerator | None = None,
        evaluator: DecisionEvaluator | None = None,
        selector: StableDecisionSelector | None = None,
    ) -> None:
        """允许替换评价器，但始终保留固定候选和稳定选择器。"""

        self._topology = topology
        self._candidate_generator = candidate_generator or CandidateGenerator(topology)
        # 安全评价器固定使用本地确定性实现，外部评价器不能替换硬安全门。
        self._safety_evaluator = DeterministicDecisionEvaluator(topology)
        self._evaluator = evaluator or DeterministicDecisionEvaluator(topology)
        self._selector = selector or StableDecisionSelector()

    def decide(
        self,
        arbitration,
        groundings: Mapping[Intent, GroundingRecord] | None = None,
        *,
        grounding_records: tuple[GroundingRecord, ...] | None = None,
    ) -> DecisionBundle:
        """评估全部模板；Grounding 冲突或仲裁阻断均不得生成下发计划。"""

        grounding_map = dict(groundings or {})
        output_groundings = tuple(grounding_records or tuple(grounding_map.values()))
        grounding_conflicts = tuple(
            conflict for record in output_groundings for conflict in record.conflicts
        )
        if grounding_conflicts:
            blocked_arbitration = replace(
                arbitration,
                status="blocked",
                active_intents=(),
                blockers=tuple(arbitration.blockers) + grounding_conflicts,
            )
            return DecisionBundle(
                status="blocked",
                arbitration=blocked_arbitration,
                candidates=(),
                selected_plan=None,
                selection_reason="显式约束与版本化 SLA 派生约束冲突，来源已记录，禁止下发。",
                grounding=output_groundings,
            )
        if arbitration.status != "ready":
            return DecisionBundle(
                status="blocked",
                arbitration=arbitration,
                candidates=(),
                selected_plan=None,
                selection_reason="存在未消解的歧义或硬冲突，禁止生成下发策略。",
                grounding=output_groundings,
            )

        candidates = self._candidate_generator.generate(arbitration)
        validated_evaluations: list[CandidateEvaluation] = []
        for candidate in candidates:
            safety_evaluation = self._safety_evaluator.evaluate(
                candidate, arbitration.active_intents, grounding_map
            )
            evaluation = self._evaluator.evaluate(
                candidate, arbitration.active_intents, grounding_map
            )
            if not isinstance(evaluation, CandidateEvaluation):
                raise IntentError(
                    "invalid_evaluation",
                    "候选评价器返回了不受支持的结果，禁止生成下发策略。",
                    422,
                )
            if evaluation.plan != candidate:
                raise IntentError(
                    "invalid_evaluation",
                    "候选评价器修改了候选计划或动作，禁止生成下发策略。",
                    422,
                )
            if not isinstance(evaluation.feasible, bool) or not isinstance(
                evaluation.hard_satisfied, bool
            ):
                raise IntentError(
                    "invalid_evaluation",
                    "候选评价器返回了非法安全标志，禁止生成下发策略。",
                    422,
                )
            # 外部评价只能收紧结果；确定性安全评价拒绝时不得被模型评分放行。
            validated_evaluations.append(
                replace(
                    evaluation,
                    feasible=safety_evaluation.feasible and evaluation.feasible,
                    hard_satisfied=safety_evaluation.hard_satisfied
                    and evaluation.hard_satisfied,
                    rejection_reasons=_merge_rejection_reasons(
                        safety_evaluation.rejection_reasons,
                        evaluation.rejection_reasons,
                    ),
                )
            )
        evaluations = tuple(validated_evaluations)
        selected = self._selector.select(evaluations)
        if selected is None:
            return DecisionBundle(
                status="blocked",
                arbitration=arbitration,
                candidates=evaluations,
                selected_plan=None,
                selection_reason="没有候选能够覆盖全部硬目标、业务范围或数值约束，禁止下发。",
                grounding=output_groundings,
            )
        if not isinstance(selected, CandidateEvaluation) or selected not in evaluations:
            raise IntentError(
                "invalid_selection",
                "策略选择器返回了未验证的候选评价，禁止生成下发策略。",
                422,
            )
        if not selected.feasible or not selected.hard_satisfied:
            raise IntentError(
                "invalid_selection",
                "策略选择器返回了未验证且未通过确定性安全评价的候选，禁止生成下发策略。",
                422,
            )
        return DecisionBundle(
            status="ready",
            arbitration=arbitration,
            candidates=evaluations,
            selected_plan=selected.plan,
            selection_reason=(
                f"{selected.plan.plan_id} 覆盖全部硬目标，效用分 {selected.utility_score:.2f}，"
                f"软目标加权覆盖率 {selected.soft_coverage:.2f}，"
                f"变更 {selected.plan.change_count} 项；评价来源为配置推导，动态 KPI 为 not_available。"
            ),
            grounding=output_groundings,
        )


def _covers(
    plan: CandidatePlan, intent: Intent, grounding: GroundingRecord | None = None
) -> bool:
    """判断模板目标及显式/适用 SLA 派生约束是否均被保证。"""

    if not _plan_supports_scope(plan, intent):
        return False
    constraints = list(intent.constraints)
    if grounding is not None:
        # 背景视频的限速 SLA 是治理目标；用户只表达优先级时不额外改变旧计划语义。
        if intent.objective in {
            Objective.RELIEVE_NETWORK_CONGESTION,
            Objective.LIMIT_BACKGROUND_TRAFFIC,
        }:
            constraints.extend(grounding.derived_constraints)
        else:
            constraints.extend(
                item
                for item in grounding.derived_constraints
                if item.metric
                in {ConstraintMetric.LATENCY_MS, ConstraintMetric.MIN_BANDWIDTH_MBPS}
            )
    return intent.objective in plan.supported_objectives and all(
        _guarantee_satisfies(plan.guarantees, constraint) for constraint in constraints
    )


def _plan_supports_scope(plan: CandidatePlan, intent: Intent) -> bool:
    """校验业务范围与固定动作一致，避免用紧急 UDP 规则覆盖其他业务。"""

    supported = {
        "critical_priority": {
            TrafficClass.EMERGENCY: frozenset(
                {Objective.PRIORITIZE_TRAFFIC, Objective.MINIMIZE_LATENCY}
            )
        },
        "congestion_relief": {
            TrafficClass.VIDEO: frozenset(
                {Objective.RELIEVE_NETWORK_CONGESTION, Objective.LIMIT_BACKGROUND_TRAFFIC}
            )
        },
        "combined": {
            TrafficClass.EMERGENCY: frozenset(
                {Objective.PRIORITIZE_TRAFFIC, Objective.MINIMIZE_LATENCY}
            ),
            TrafficClass.VIDEO: frozenset(
                {Objective.RELIEVE_NETWORK_CONGESTION, Objective.LIMIT_BACKGROUND_TRAFFIC}
            ),
        },
    }.get(plan.plan_id, {})
    return intent.objective in supported.get(intent.scope.traffic_class, frozenset())


def _guarantee_satisfies(
    guarantees: tuple[Constraint, ...], requested: Constraint
) -> bool:
    """仅按模板声明能力核验约束，绝不从输入生成新的数值。"""

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


def _merge_rejection_reasons(
    safety_reasons: tuple[str, ...], evaluation_reasons: tuple[str, ...]
) -> tuple[str, ...]:
    """合并确定性安全评价与外部评价的原因并保持首次出现顺序。"""

    merged: list[str] = []
    for reason in (*safety_reasons, *evaluation_reasons):
        if reason not in merged:
            merged.append(reason)
    return tuple(merged)


def _preference_weight(objective: Objective, preference_order: tuple[str, ...]) -> float:
    """将 SLA 有序偏好转换为有限、可解释的离散权重。"""

    metric = {
        Objective.MINIMIZE_LATENCY: "latency",
        Objective.PRIORITIZE_TRAFFIC: "latency",
        Objective.RELIEVE_NETWORK_CONGESTION: "bandwidth",
        Objective.LIMIT_BACKGROUND_TRAFFIC: "bandwidth",
    }[objective]
    if not preference_order:
        return 1.0
    try:
        index = preference_order.index(metric)
    except ValueError:
        return 1.0
    return float(max(1, len(preference_order) - index))


def _not_available_kpis() -> dict[str, object]:
    """返回无可靠模型时的逐项动态 KPI 状态，禁止用模板常量冒充预测。"""

    return {
        "emergency_p95_latency_ms": "not_available",
        "throughput_mbps": "not_available",
        "packet_loss_percent": "not_available",
        "link_utilization_percent": "not_available",
    }


def _templates() -> tuple[CandidatePlan, ...]:
    """构造全部固定模板；此处是唯一允许出现路径、队列和带宽常量的位置。"""

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
            "queue", "low-latency-path", (("queue_id", "1"), ("min_rate_mbps", "12"))
        ),
    )
    critical = CandidatePlan(
        plan_id="critical_priority",
        description="紧急业务走低时延路径，并绑定高优先级出口队列。",
        supported_objectives=frozenset(
            {Objective.PRIORITIZE_TRAFFIC, Objective.MINIMIZE_LATENCY}
        ),
        guarantees=(
            Constraint(ConstraintMetric.LATENCY_MS, ConstraintOperator.LESS_OR_EQUAL, 20, "ms"),
            Constraint(ConstraintMetric.MIN_BANDWIDTH_MBPS, ConstraintOperator.GREATER_OR_EQUAL, 12, "Mbps"),
        ),
        actions=critical_actions,
    )
    congestion_actions = (
        PolicyAction(
            "flow", "rsu", (("match", "udp,tp_dst=5004"), ("path", "high-capacity-path"))
        ),
        PolicyAction(
            "qos", "high-capacity-path", (("traffic_class", "video"), ("max_rate_mbps", "8"))
        ),
        PolicyAction("meter", "rsu", (("meter_id", "2"), ("max_rate_mbps", "8"))),
    )
    congestion = CandidatePlan(
        plan_id="congestion_relief",
        description="背景视频改走高容量路径，并进行固定出口限速。",
        supported_objectives=frozenset(
            {Objective.RELIEVE_NETWORK_CONGESTION, Objective.LIMIT_BACKGROUND_TRAFFIC}
        ),
        guarantees=(
            Constraint(ConstraintMetric.MAX_BANDWIDTH_MBPS, ConstraintOperator.LESS_OR_EQUAL, 8, "Mbps"),
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
