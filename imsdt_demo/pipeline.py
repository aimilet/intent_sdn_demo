"""闭环流水线：打通场景、意图、多智能体、数字孪生、历史记忆和结果输出。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from imsdt_demo.agents import CandidateCoordinator
from imsdt_demo.intent import explicit_intents_for_scene, infer_implicit_intents, resolve_intents
from imsdt_demo.memory import HistoryStore
from imsdt_demo.models import EvaluationResult, ExecutionResult, Intent, PredictionResult, SceneState, SyncState
from imsdt_demo.scenario import generate_scene
from imsdt_demo.twin import DigitalTwinEvaluator, FuturePredictor, Synchronizer, simulate_execution


@dataclass(frozen=True)
class DemoResult:
    """演示结果：封装 CLI 输出和测试断言需要的关键对象。"""

    scene: SceneState
    intents: tuple[Intent, ...]
    sync_state: SyncState
    prediction: PredictionResult
    evaluations: tuple[EvaluationResult, ...]
    selected: EvaluationResult
    execution: ExecutionResult
    memory_hits: int
    case_count: int


def run_demo(
    scenario: str = "emergency",
    history_path: Path | None = Path("data/history_cases.json"),
    seed: int = 7,
    save_history: bool = True,
) -> DemoResult:
    """运行一次端到端演示，并在需要时写回历史案例。"""

    scene = generate_scene(scenario, seed=seed)
    explicit = explicit_intents_for_scene(scene)
    implicit = infer_implicit_intents(scene)
    intents = explicit + implicit
    profile = resolve_intents(intents, scene)

    synchronizer = Synchronizer()
    predictor = FuturePredictor()
    evaluator = DigitalTwinEvaluator()
    sync_state = synchronizer.sync(scene)
    prediction = predictor.predict(scene, window_s=5)

    store = HistoryStore(history_path if save_history else None)
    memory_plans = store.reusable_plans(scene, profile, prediction)
    coordinator = CandidateCoordinator()
    candidates = coordinator.build_candidates(scene, profile, memory_plans)
    evaluations = tuple(
        evaluator.evaluate(candidate, scene, profile, prediction, sync_state)
        for candidate in candidates
    )
    selected = _select_best(evaluations)
    execution = simulate_execution(selected, scene)

    store.add_case(scene, profile, prediction, selected, execution)
    if save_history:
        store.save()

    return DemoResult(
        scene=scene,
        intents=tuple(intents),
        sync_state=sync_state,
        prediction=prediction,
        evaluations=evaluations,
        selected=selected,
        execution=execution,
        memory_hits=len(memory_plans),
        case_count=len(store.cases),
    )


def _select_best(evaluations: tuple[EvaluationResult, ...]) -> EvaluationResult:
    """在满足硬约束优先的前提下，选择意图满足度最高且风险较低的方案。"""

    if not evaluations:
        raise ValueError("没有可评估的候选策略。")

    def score(item: EvaluationResult) -> tuple[int, float, float]:
        feasible = 0 if item.sla_violation else 1
        return (feasible, item.intent_satisfaction, -item.risk_score)

    return max(evaluations, key=score)
