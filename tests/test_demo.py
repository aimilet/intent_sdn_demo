"""回归测试：验证 demo 主链路、历史记忆和场景边界。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from imsdt_demo import run_demo
from imsdt_demo.scenario import generate_scene
from imsdt_demo.task_queue import generate_task_queue
from imsdt_demo.trace import build_visual_trace


class DemoPipelineTest(unittest.TestCase):
    """覆盖端到端流程的最小测试集。"""

    def test_emergency_demo_selects_feasible_plan(self) -> None:
        """紧急场景应生成可执行方案，并优先避免硬约束违约。"""

        result = run_demo("emergency", history_path=None, save_history=False)
        self.assertGreaterEqual(len(result.batch_schedule.queue), 6)
        self.assertEqual(len(result.batch_schedule.queue), len(result.batch_schedule.scheduled))
        self.assertGreaterEqual(len(result.evaluations), 3)
        self.assertFalse(result.selected.sla_violation)
        self.assertGreater(result.selected.intent_satisfaction, 0.5)

    def test_history_store_reuses_second_run(self) -> None:
        """同一场景第二次运行应命中历史案例并增加候选来源。"""

        with tempfile.TemporaryDirectory() as tmp_dir:
            history_path = Path(tmp_dir) / "history.json"
            first = run_demo("normal", history_path=history_path, save_history=True)
            second = run_demo("normal", history_path=history_path, save_history=True)

        self.assertEqual(first.memory_hits, 0)
        self.assertGreaterEqual(second.memory_hits, 1)
        self.assertGreater(second.case_count, first.case_count)

    def test_unknown_scene_is_rejected(self) -> None:
        """未知场景必须 fail-fast，避免用错误输入继续决策。"""

        with self.assertRaises(ValueError):
            generate_scene("unknown")

    def test_task_queue_contains_multiple_vehicle_tasks(self) -> None:
        """任务生成器应输出多车辆任务队列，而不是单一焦点任务。"""

        queue = generate_task_queue(generate_scene("normal"))
        self.assertGreaterEqual(len(queue), 6)
        self.assertEqual(len({item.vehicle.vehicle_id for item in queue}), len(queue))
        self.assertTrue(any(item.role == "focus" for item in queue))

    def test_visual_trace_contains_topology_and_steps(self) -> None:
        """前端轨迹应包含组件、链路、步骤和已选择方案。"""

        trace = build_visual_trace("emergency", history_path=None, save_history=False)
        self.assertGreaterEqual(len(trace["vehicles"]), 6)
        self.assertGreaterEqual(len(trace["roads"]), 5)
        self.assertEqual(len(trace["taskQueue"]), len(trace["batchDecisions"]))
        self.assertGreaterEqual(len(trace["nodes"]), 6)
        self.assertGreaterEqual(len(trace["links"]), 5)
        self.assertEqual(len(trace["steps"]), 10)
        self.assertIn(trace["selected"]["target"], {"local", "rsu", "edge"})


if __name__ == "__main__":
    unittest.main()
