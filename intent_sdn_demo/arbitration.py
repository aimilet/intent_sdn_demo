"""多意图仲裁模块：按范围、硬目标和来源等级确定可进入策略编译的意图集合。"""

from __future__ import annotations

from collections import defaultdict

from intent_sdn_demo.models import (
    ActorRole,
    ArbitrationResult,
    ConstraintMetric,
    Intent,
    IntentEnvelope,
    Objective,
    Priority,
    SourcedIntent,
    Strength,
    SuppressedIntent,
)


ROLE_RANK = {
    ActorRole.DISPATCHER: 4,
    ActorRole.OPERATOR: 3,
    ActorRole.DRIVER: 2,
    ActorRole.APPLICATION: 1,
}

PRIORITY_RANK = {
    Priority.CRITICAL: 4,
    Priority.HIGH: 3,
    Priority.NORMAL: 2,
    Priority.LOW: 1,
}

CONFLICTING_OBJECTIVES = {
    frozenset({Objective.PRIORITIZE_TRAFFIC, Objective.LIMIT_BACKGROUND_TRAFFIC}),
}


class IntentArbitrator:
    """执行方案中定义的确定性仲裁，不依赖模型概率或外部网络状态。"""

    def resolve(self, envelopes: tuple[IntentEnvelope, ...]) -> ArbitrationResult:
        """处理多意图冲突；任意阻断项存在时不允许后续策略下发。"""

        ambiguities = [
            ambiguity
            for envelope in envelopes
            for intent in envelope.intents
            for ambiguity in intent.ambiguities
        ]
        if ambiguities:
            return ArbitrationResult(
                status="blocked",
                active_intents=(),
                suppressed_intents=(),
                blockers=tuple(f"意图存在歧义：{item}" for item in ambiguities),
            )

        grouped: dict[tuple[tuple[str, ...], object], list[SourcedIntent]] = defaultdict(list)
        for envelope in envelopes:
            for intent in envelope.intents:
                grouped[intent.scope.key()].append(SourcedIntent(envelope.actor_role, intent))

        active: list[SourcedIntent] = []
        suppressed: list[SuppressedIntent] = []
        blockers: list[str] = []
        for intents in grouped.values():
            group_active, group_suppressed, group_blockers = self._resolve_group(
                intents
            )
            active.extend(group_active)
            suppressed.extend(group_suppressed)
            blockers.extend(group_blockers)

        return ArbitrationResult(
            status="blocked" if blockers else "ready",
            active_intents=tuple(active) if not blockers else (),
            suppressed_intents=tuple(suppressed),
            blockers=tuple(blockers),
        )

    def _resolve_group(
        self, intents: list[SourcedIntent]
    ) -> tuple[list[SourcedIntent], list[SuppressedIntent], list[str]]:
        """在同一车辆和业务范围内逐对消解直接冲突。"""

        active: list[SourcedIntent] = []
        suppressed: list[SuppressedIntent] = []
        blockers: list[str] = []
        for current in intents:
            current_suppressed = False
            for existing in list(active):
                if not _conflicts(current.intent, existing.intent):
                    continue
                outcome = _compare_conflict(current, existing)
                if outcome == "block":
                    blockers.append(
                        "同一范围内存在同等级互斥硬目标："
                        f"{existing.intent.objective.value} 与 {current.intent.objective.value}。"
                    )
                    continue
                if outcome == "current":
                    active.remove(existing)
                    suppressed.append(
                        SuppressedIntent(
                            existing,
                            f"与 {current.intent.objective.value} 冲突，按硬目标和来源等级被覆盖。",
                        )
                    )
                elif outcome == "existing":
                    suppressed.append(
                        SuppressedIntent(
                            current,
                            f"与 {existing.intent.objective.value} 冲突，按硬目标和来源等级被覆盖。",
                        )
                    )
                    current_suppressed = True
                    break
            if not current_suppressed:
                active.append(current)
        return active, suppressed, blockers


def role_rank(role: ActorRole) -> int:
    """公开来源等级，供策略选择对软目标加权复用。"""

    return ROLE_RANK[role]


def priority_rank(priority: Priority) -> int:
    """公开优先级等级，供策略选择对软目标加权复用。"""

    return PRIORITY_RANK[priority]


def _conflicts(first: Intent, second: Intent) -> bool:
    """识别目标反向和最小/最大带宽不可兼容两类第一版直接冲突。"""

    objectives = frozenset({first.objective, second.objective})
    if objectives in CONFLICTING_OBJECTIVES:
        return True
    return _bandwidth_constraints_conflict(first, second)


def _bandwidth_constraints_conflict(first: Intent, second: Intent) -> bool:
    """判断跨意图的带宽下界是否大于上界。"""

    minimums = [
        item.value
        for intent in (first, second)
        for item in intent.constraints
        if item.metric is ConstraintMetric.MIN_BANDWIDTH_MBPS
    ]
    maximums = [
        item.value
        for intent in (first, second)
        for item in intent.constraints
        if item.metric is ConstraintMetric.MAX_BANDWIDTH_MBPS
    ]
    return bool(minimums and maximums and max(minimums) > min(maximums))


def _compare_conflict(current: SourcedIntent, existing: SourcedIntent) -> str:
    """按硬目标、来源等级和软目标优先级返回冲突处理结果。"""

    if current.intent.strength is Strength.MUST and existing.intent.strength is Strength.PREFER:
        return "current"
    if existing.intent.strength is Strength.MUST and current.intent.strength is Strength.PREFER:
        return "existing"
    current_role = role_rank(current.actor_role)
    existing_role = role_rank(existing.actor_role)
    if current.intent.strength is Strength.MUST and existing.intent.strength is Strength.MUST:
        if current_role > existing_role:
            return "current"
        if existing_role > current_role:
            return "existing"
        # 同等级主体的互斥硬目标必须由人工改写，不能猜测取舍。
        return "block"

    if current_role > existing_role:
        return "current"
    if existing_role > current_role:
        return "existing"
    current_priority = priority_rank(current.intent.priority)
    existing_priority = priority_rank(existing.intent.priority)
    if current_priority > existing_priority:
        return "current"
    if existing_priority > current_priority:
        return "existing"
    # 同一来源、同优先级的软目标可以共同保留，交给候选覆盖率择优。
    return "keep_both"
