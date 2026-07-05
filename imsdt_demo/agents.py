"""多智能体决策模块：生成卸载候选、分配资源、检查约束并排序候选方案。"""

from __future__ import annotations

from imsdt_demo.models import CandidatePlan, IntentKind, IntentProfile, SceneState, Target


class TaskOffloadingAgent:
    """任务卸载智能体：枚举本地、RSU 和边缘云三类候选执行位置。"""

    def generate(self, scene: SceneState) -> list[CandidatePlan]:
        """生成初始卸载动作，资源字段由后续资源智能体补全。"""

        return [
            CandidatePlan(
                plan_id=f"{scene.task.task_id}-local",
                target=Target.LOCAL,
                bandwidth_mbps=0.0,
                compute_share=1.0,
                queue_priority=scene.task.priority,
                source="agent",
                explanation="本地执行避免传输时延，但车辆能耗更高。",
            ),
            CandidatePlan(
                plan_id=f"{scene.task.task_id}-rsu",
                target=Target.RSU,
                bandwidth_mbps=scene.network.rsu_bandwidth_mbps,
                compute_share=0.55,
                queue_priority=scene.task.priority,
                source="agent",
                explanation="RSU 距离近，适合低时延卸载。",
            ),
            CandidatePlan(
                plan_id=f"{scene.task.task_id}-edge",
                target=Target.EDGE,
                bandwidth_mbps=scene.network.edge_bandwidth_mbps,
                compute_share=0.45,
                queue_priority=scene.task.priority,
                source="agent",
                explanation="边缘云算力更强，但传输路径更长。",
            ),
        ]


class ResourceAllocationAgent:
    """资源分配智能体：根据意图画像调整带宽、算力份额和队列优先级。"""

    def allocate(
        self, plans: list[CandidatePlan], scene: SceneState, profile: IntentProfile
    ) -> list[CandidatePlan]:
        """为每个候选方案补充资源分配，保持候选动作与目标一致。"""

        allocated: list[CandidatePlan] = []
        emergency = profile.dominant_intent == IntentKind.EMERGENCY_PRIORITY
        energy_first = profile.dominant_intent == IntentKind.LOW_ENERGY
        load_balance_weight = profile.weights.get("load_balance", 0.0)

        for plan in plans:
            bandwidth = plan.bandwidth_mbps
            compute_share = plan.compute_share
            queue_priority = plan.queue_priority
            explanation = plan.explanation

            if emergency:
                queue_priority = max(queue_priority, 10)
                compute_share = min(0.95, compute_share + 0.25)
                bandwidth = bandwidth * 1.15 if bandwidth > 0 else bandwidth
                explanation += " 紧急意图触发高队列优先级和额外资源倾斜。"
            elif energy_first and plan.target == Target.LOCAL:
                compute_share = 0.65
                explanation += " 低能耗意图降低本地算力占用。"

            if load_balance_weight > 0.25 and plan.target == Target.RSU:
                compute_share = min(compute_share, 0.45)
                explanation += " 边缘高负载时限制 RSU 额外占用。"
            if load_balance_weight > 0.25 and plan.target == Target.EDGE:
                compute_share = min(compute_share, 0.35)
                explanation += " 边缘高负载时降低边缘云分配份额。"

            allocated.append(
                CandidatePlan(
                    plan_id=plan.plan_id,
                    target=plan.target,
                    bandwidth_mbps=bandwidth,
                    compute_share=compute_share,
                    queue_priority=queue_priority,
                    source=plan.source,
                    explanation=explanation,
                )
            )
        return allocated


class ConstraintAgent:
    """约束检查智能体：提前过滤明显不可执行的候选策略。"""

    def filter(self, plans: list[CandidatePlan], scene: SceneState) -> list[CandidatePlan]:
        """仅过滤物理资源无效的候选，性能约束交给数字孪生评估。"""

        feasible: list[CandidatePlan] = []
        for plan in plans:
            if plan.target != Target.LOCAL and plan.bandwidth_mbps <= 0:
                continue
            if plan.compute_share <= 0:
                continue
            if scene.task.deadline_ms <= 0 or scene.task.size_mb <= 0:
                raise ValueError("任务截止时间和数据规模必须为正数。")
            feasible.append(plan)
        return feasible


class CandidateCoordinator:
    """候选方案协调器：串联三个智能体，输出可评估候选集合。"""

    def __init__(self) -> None:
        self.offloading_agent = TaskOffloadingAgent()
        self.resource_agent = ResourceAllocationAgent()
        self.constraint_agent = ConstraintAgent()

    def build_candidates(
        self,
        scene: SceneState,
        profile: IntentProfile,
        memory_plans: list[CandidatePlan] | None = None,
    ) -> list[CandidatePlan]:
        """合并智能体候选与历史复用候选，并去重保持策略来源可追踪。"""

        plans = self.offloading_agent.generate(scene)
        plans = self.resource_agent.allocate(plans, scene, profile)
        if memory_plans:
            plans.extend(memory_plans)

        deduped: dict[tuple[Target, str], CandidatePlan] = {}
        for plan in plans:
            deduped[(plan.target, plan.source)] = plan
        return self.constraint_agent.filter(list(deduped.values()), scene)
