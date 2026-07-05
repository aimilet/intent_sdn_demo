"""闭环流水线：打通场景、意图、多智能体、数字孪生、历史记忆和结果输出。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from imsdt_demo.batch_scheduler import BatchScheduler
from imsdt_demo.intent import explicit_intents_for_scene, infer_implicit_intents, resolve_intents
from imsdt_demo.memory import HistoryStore
from imsdt_demo.models import (
    BatchScheduleResult,
    EvaluationResult,
    ExecutionResult,
    Intent,
    IntentProfile,
    PredictionResult,
    SceneState,
    SyncState,
)
from imsdt_demo.scenario import generate_scene
from imsdt_demo.task_queue import generate_task_queue
from imsdt_demo.twin import FuturePredictor, Synchronizer


@dataclass(frozen=True)
class DemoResult:
    """演示结果：封装 CLI 输出和测试断言需要的关键对象。"""

    scene: SceneState
    intents: tuple[Intent, ...]
    profile: IntentProfile
    sync_state: SyncState
    prediction: PredictionResult
    evaluations: tuple[EvaluationResult, ...]
    selected: EvaluationResult
    execution: ExecutionResult
    batch_schedule: BatchScheduleResult
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
    sync_state = synchronizer.sync(scene)
    prediction = predictor.predict(scene, window_s=5)

    store = HistoryStore(history_path if save_history else None)
    memory_plans = store.reusable_plans(scene, profile, prediction)
    task_queue = generate_task_queue(scene)
    batch_schedule = BatchScheduler().schedule(
        scene,
        task_queue,
        sync_state,
        prediction,
        memory_plans={"q-focus": memory_plans},
    )
    focus = _focus_schedule(batch_schedule)
    evaluations = focus.evaluations
    selected = focus.selected
    execution = focus.execution

    store.add_case(scene, profile, prediction, selected, execution)
    if save_history:
        store.save()

    return DemoResult(
        scene=scene,
        intents=tuple(intents),
        profile=profile,
        sync_state=sync_state,
        prediction=prediction,
        evaluations=evaluations,
        selected=selected,
        execution=execution,
        batch_schedule=batch_schedule,
        memory_hits=len(memory_plans),
        case_count=len(store.cases),
    )


def _focus_schedule(batch_schedule: BatchScheduleResult):
    """从批量调度结果中取出焦点车辆任务。"""

    for item in batch_schedule.scheduled:
        if item.queue_item.role == "focus":
            return item
    raise ValueError("批量调度结果缺少焦点车辆任务。")
