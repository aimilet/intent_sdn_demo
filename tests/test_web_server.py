"""本地 HTTP 接口测试：验证回环服务可用且不依赖远程大模型。"""

from __future__ import annotations

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from intent_sdn_demo.web_server import create_server


class WebServerTest(unittest.TestCase):
    """覆盖健康检查和结构化 JSON 到策略编译的最小 API 闭环。"""

    def setUp(self) -> None:
        """启动随机端口的本地服务，避免测试依赖固定端口。"""

        try:
            self.server = create_server(port=0)
        except PermissionError as exc:
            self.skipTest(f"当前沙箱禁止监听本地端口：{exc}")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        """关闭服务并等待线程退出，避免残留监听端口。"""

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_health_and_structured_parse_compile(self) -> None:
        """页面、健康检查和结构化输入应完成预览，未启用执行时必须拒绝确认下发。"""

        health = self._get_json("/api/health")
        self.assertEqual(health["status"], "ok")
        self.assertIn("车联网通信意图转译工作台", self._get_text("/"))
        self.assertEqual(self._get_json("/api/metrics")["status"], "not_available")

        envelope = self._post_json(
            "/api/intents/parse",
            {
                "source_channel": "json",
                "actor_role": "dispatcher",
                "payload": {
                    "intents": [
                        {
                            "scope": {
                                "vehicle_ids": ["veh-emergency-01"],
                                "traffic_class": "emergency",
                            },
                            "objective": "prioritize_traffic",
                            "strength": "must",
                            "priority": "critical",
                            "constraints": [],
                            "evidence": ["结构化测试"],
                            "ambiguities": [],
                        }
                    ]
                },
            },
        )
        decision = self._post_json("/api/policies/compile", {"envelope": envelope})

        self.assertEqual(decision["status"], "ready")
        self.assertEqual(decision["selected_plan"]["plan_id"], "critical_priority")
        error = self._post_error(
            "/api/policies/apply",
            {"plan_id": decision["selected_plan"]["plan_id"]},
        )
        self.assertEqual(error["error"]["code"], "mininet_disabled")
        self.assertEqual(self._post_json("/api/policies/reset", {})["status"], "reset")

    def _get_json(self, path: str) -> dict[str, object]:
        """发送本地 GET 请求并解码 JSON。"""

        with urlopen(f"{self.base_url}{path}", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get_text(self, path: str) -> str:
        """读取静态页面资源，验证本地工作台由同一回环服务提供。"""

        with urlopen(f"{self.base_url}{path}", timeout=3) as response:
            return response.read().decode("utf-8")

    def _post_json(self, path: str, payload: object) -> dict[str, object]:
        """发送本地 JSON POST 请求并解码 JSON。"""

        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_error(self, path: str, payload: object) -> dict[str, object]:
        """发送预期失败的请求并读取受控错误对象。"""

        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as captured:
            urlopen(request, timeout=3)
        return json.loads(captured.exception.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
