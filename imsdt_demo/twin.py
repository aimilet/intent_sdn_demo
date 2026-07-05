"""数字孪生模块：完成实时同步、短期预测、候选策略评估和执行反馈模拟。"""

from __future__ import annotations

from imsdt_demo.models import (
    CandidatePlan,
    EvaluationResult,
    ExecutionResult,
    IntentProfile,
    PredictionResult,
    SceneState,
    SyncState,
    Target,
)


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """限制指标范围，避免异常输入导致评分越界。"""

    return max(lower, min(upper, value))


class Synchronizer:
    """实时同步模块：用固定时间片模拟孪生状态质量。"""

    def sync(
        self, scene: SceneState, data_delay_ms: float = 35.0, missing_ratio: float = 0.0
    ) -> SyncState:
        """计算同步质量，当前 demo 不复制状态，只衡量状态可信度。"""

        state_error = clamp(data_delay_ms / 1000.0 + missing_ratio * 0.8)
        quality = clamp(1.0 - state_error)
        return SyncState(
            tick_ms=scene.tick_ms,
            data_delay_ms=data_delay_ms,
            missing_ratio=missing_ratio,
            state_error=state_error,
            quality=quality,
        )


class FuturePredictor:
    """未来预测模块：预测未来 1 到 10 秒内的负载、链路和超时风险。"""

    def predict(self, scene: SceneState, window_s: int = 5) -> PredictionResult:
        """使用透明规则模型生成短期风险，便于后续替换为学习模型。"""

        if window_s < 1 or window_s > 10:
            raise ValueError("预测窗口必须在 1 到 10 秒之间。")

        arrival_rate = 0.4 + scene.vehicle.density * 1.8 + scene.task.priority / 20.0
        future_rsu = clamp(scene.edge.rsu_utilization + arrival_rate * window_s * 0.025)
        future_edge = clamp(scene.edge.edge_utilization + arrival_rate * window_s * 0.018)
        speed_pressure = clamp(scene.vehicle.speed_mps / 35.0)
        link_risk = clamp((1.0 - scene.network.channel_quality) + scene.network.packet_loss * 2.0 + speed_pressure * 0.12)
        overload_risk = clamp(max(future_rsu - 0.78, future_edge - 0.82, 0.0) / 0.22)
        timeout_risk = clamp((scene.task.priority / 10.0) * 0.45 + overload_risk * 0.35 + link_risk * 0.20)
        confidence = clamp(0.88 - window_s * 0.025 - scene.network.packet_loss)
        return PredictionResult(
            window_s=window_s,
            future_edge_utilization=future_edge,
            future_rsu_utilization=future_rsu,
            task_arrival_rate=arrival_rate,
            link_degradation_risk=link_risk,
            overload_risk=overload_risk,
            timeout_risk=timeout_risk,
            confidence=confidence,
        )


class DigitalTwinEvaluator:
    """轻量数字孪生评估器：把候选策略转化为时延、能耗、可靠性和意图满足度。"""

    def evaluate(
        self,
        plan: CandidatePlan,
        scene: SceneState,
        profile: IntentProfile,
        prediction: PredictionResult,
        sync_state: SyncState,
    ) -> EvaluationResult:
        """评估单个候选策略，并给出硬约束是否违约。"""

        latency = self._latency_ms(plan, scene, prediction)
        energy = self._energy_j(plan, scene)
        reliability = self._reliability(plan, scene, prediction, sync_state)
        resource_cost = self._resource_cost(plan, scene)
        risk = self._risk_score(plan, prediction, sync_state)
        satisfaction = self._intent_satisfaction(
            latency, energy, reliability, resource_cost, risk, plan, scene, profile
        )
        sla_violation = False
        if profile.hard_latency_ms is not None and latency > profile.hard_latency_ms:
            sla_violation = True
        if profile.hard_reliability is not None and reliability < profile.hard_reliability:
            sla_violation = True

        return EvaluationResult(
            plan=plan,
            latency_ms=latency,
            energy_j=energy,
            reliability=reliability,
            resource_cost=resource_cost,
            intent_satisfaction=satisfaction,
            sla_violation=sla_violation,
            risk_score=risk,
            explanation=self._explain(plan, latency, reliability, sla_violation),
        )

    def _latency_ms(
        self, plan: CandidatePlan, scene: SceneState, prediction: PredictionResult
    ) -> float:
        """计算任务端到端时延，包含传输、排队和计算三部分。"""

        task = scene.task
        if plan.target == Target.LOCAL:
            compute_capacity = scene.vehicle.local_compute_ghz * max(plan.compute_share, 0.2)
            return task.cpu_cycles_g / compute_capacity * 1000.0

        if plan.target == Target.RSU:
            tx = task.size_mb * 8.0 / plan.bandwidth_mbps * 1000.0 + scene.network.base_latency_ms
            utilization = scene.edge.rsu_utilization
            queue = scene.edge.rsu_queue
            compute_capacity = scene.edge.rsu_compute_ghz * max(plan.compute_share, 0.2)
            future_pressure = prediction.future_rsu_utilization
        else:
            tx = task.size_mb * 8.0 / plan.bandwidth_mbps * 1000.0 + scene.network.base_latency_ms + 8.0
            utilization = scene.edge.edge_utilization
            queue = scene.edge.edge_queue
            compute_capacity = scene.edge.edge_compute_ghz * max(plan.compute_share, 0.2)
            future_pressure = prediction.future_edge_utilization

        priority_factor = 1.0 + plan.queue_priority / 10.0
        queue_delay = queue * 3.5 * (0.6 + utilization) / priority_factor
        compute = task.cpu_cycles_g / compute_capacity * 1000.0
        pressure_penalty = max(0.0, future_pressure - 0.85) * 45.0
        return tx + queue_delay + compute + pressure_penalty

    def _energy_j(self, plan: CandidatePlan, scene: SceneState) -> float:
        """估算车辆侧能耗，本地计算更耗电，卸载主要产生传输能耗。"""

        task = scene.task
        if plan.target == Target.LOCAL:
            return task.cpu_cycles_g * 2.6 * plan.compute_share
        tx_energy = task.size_mb * (0.18 if plan.target == Target.RSU else 0.25)
        control_energy = 0.25 + scene.network.packet_loss * 4.0
        return tx_energy + control_energy

    def _reliability(
        self,
        plan: CandidatePlan,
        scene: SceneState,
        prediction: PredictionResult,
        sync_state: SyncState,
    ) -> float:
        """计算策略可靠性，网络策略受链路风险和同步质量影响。"""

        if plan.target == Target.LOCAL:
            base = 0.985
        else:
            base = scene.network.channel_quality * (1.0 - scene.network.packet_loss)
            base -= prediction.link_degradation_risk * (0.05 if plan.target == Target.RSU else 0.08)
        return clamp(base * (0.92 + sync_state.quality * 0.08), 0.0, 0.999)

    def _resource_cost(self, plan: CandidatePlan, scene: SceneState) -> float:
        """计算资源成本，远端和高负载位置成本更高。"""

        if plan.target == Target.LOCAL:
            return clamp(plan.compute_share * 0.55)
        if plan.target == Target.RSU:
            return clamp(0.35 + scene.edge.rsu_utilization * 0.40 + plan.compute_share * 0.20)
        return clamp(0.42 + scene.edge.edge_utilization * 0.35 + plan.compute_share * 0.18)

    def _risk_score(
        self, plan: CandidatePlan, prediction: PredictionResult, sync_state: SyncState
    ) -> float:
        """聚合未来风险和同步质量，作为策略选择的惩罚项。"""

        link = prediction.link_degradation_risk if plan.target != Target.LOCAL else 0.05
        return clamp(
            prediction.timeout_risk * 0.45
            + prediction.overload_risk * 0.30
            + link * 0.15
            + (1.0 - sync_state.quality) * 0.10
        )

    def _intent_satisfaction(
        self,
        latency: float,
        energy: float,
        reliability: float,
        resource_cost: float,
        risk: float,
        plan: CandidatePlan,
        scene: SceneState,
        profile: IntentProfile,
    ) -> float:
        """按动态权重计算意图满足度，同时保留硬约束惩罚。"""

        target_latency = profile.hard_latency_ms or scene.task.deadline_ms
        latency_score = clamp(target_latency / max(latency, 1.0))
        energy_score = clamp(1.0 - energy / 4.0)
        reliability_score = reliability
        priority_score = clamp(plan.queue_priority / 10.0)
        load_balance_score = 1.0 - resource_cost

        weights = profile.weights
        score = (
            weights["latency"] * latency_score
            + weights["energy"] * energy_score
            + weights["reliability"] * reliability_score
            + weights["priority"] * priority_score
            + weights["load_balance"] * load_balance_score
        )
        score -= risk * 0.18
        if profile.hard_latency_ms is not None and latency > profile.hard_latency_ms:
            score -= 0.18
        if profile.hard_reliability is not None and reliability < profile.hard_reliability:
            score -= 0.12
        return clamp(score)

    def _explain(
        self, plan: CandidatePlan, latency: float, reliability: float, sla_violation: bool
    ) -> str:
        """生成面向用户的短解释，说明选择风险和约束情况。"""

        status = "触发硬约束风险" if sla_violation else "满足当前硬约束"
        return (
            f"{plan.target.value} 方案预计时延 {latency:.1f} ms，"
            f"可靠性 {reliability:.3f}，{status}。"
        )


def simulate_execution(evaluation: EvaluationResult, scene: SceneState) -> ExecutionResult:
    """模拟真实执行结果，用于闭环反馈和历史案例置信度更新。"""

    drift = 1.0 + (scene.vehicle.density - 0.5) * 0.06 + scene.network.packet_loss
    actual_latency = evaluation.latency_ms * drift
    actual_energy = evaluation.energy_j * (1.0 + scene.network.packet_loss * 0.5)
    actual_reliability = clamp(evaluation.reliability - max(0.0, drift - 1.0) * 0.04)
    prediction_error = abs(actual_latency - evaluation.latency_ms) / max(actual_latency, 1.0)
    actual_satisfaction = clamp(evaluation.intent_satisfaction - prediction_error * 0.35)
    return ExecutionResult(
        latency_ms=actual_latency,
        energy_j=actual_energy,
        reliability=actual_reliability,
        intent_satisfaction=actual_satisfaction,
        sla_violation=evaluation.sla_violation,
        prediction_error=prediction_error,
    )
