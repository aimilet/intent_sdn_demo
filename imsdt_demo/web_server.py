"""前端演示服务：用标准库 HTTP 服务静态页面和实时决策轨迹 API。"""

from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from imsdt_demo.trace import SCENARIOS, build_visual_trace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"
DEFAULT_HISTORY = PROJECT_ROOT / "data" / "web_history_cases.json"


def main(argv: list[str] | None = None) -> int:
    """启动本地前端演示服务。"""

    parser = argparse.ArgumentParser(description="IMSDT-VEC 前端演示服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="历史案例文件路径")
    args = parser.parse_args(argv)

    handler = _handler_factory(args.history)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"IMSDT-VEC 前端演示: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


def _handler_factory(history_path: Path) -> type[BaseHTTPRequestHandler]:
    """绑定历史案例路径，避免使用全局可变状态。"""

    class DemoRequestHandler(BaseHTTPRequestHandler):
        """处理静态资源和 JSON API。"""

        def do_GET(self) -> None:
            """根据路径分发静态页面或 API 请求。"""

            parsed = urlparse(self.path)
            if parsed.path == "/api/run":
                self._handle_run(parsed.query)
                return
            if parsed.path == "/api/reset-history":
                self._handle_reset_history()
                return
            self._serve_static(parsed.path)

        def log_message(self, fmt: str, *args: object) -> None:
            """压缩默认访问日志，保留请求路径和状态。"""

            print(f"{self.address_string()} - {fmt % args}")

        def _handle_run(self, query: str) -> None:
            """运行一次后端 demo，并返回可视化轨迹。"""

            params = parse_qs(query)
            scenario = params.get("scenario", ["emergency"])[0]
            if scenario not in SCENARIOS:
                self._send_json({"error": f"未知场景: {scenario}"}, HTTPStatus.BAD_REQUEST)
                return
            seed = _parse_int(params.get("seed", ["7"])[0], default=7)
            persist = params.get("persist", ["1"])[0] != "0"
            trace = build_visual_trace(
                scenario,
                seed=seed,
                history_path=history_path if persist else None,
                save_history=persist,
            )
            self._send_json(trace)

        def _handle_reset_history(self) -> None:
            """清空前端演示用历史案例文件。"""

            if history_path.exists():
                history_path.unlink()
            self._send_json({"ok": True, "caseCount": 0})

        def _serve_static(self, request_path: str) -> None:
            """安全地服务 web 目录内的静态资源。"""

            rel_path = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
            target = (WEB_ROOT / rel_path).resolve()
            web_root = WEB_ROOT.resolve()
            if not target.is_relative_to(web_root) or not target.exists() or target.is_dir():
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            content_type, _ = mimetypes.guess_type(target.name)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(target.read_bytes())

        def _send_json(
            self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            """发送 JSON 响应。"""

            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return DemoRequestHandler


def _parse_int(value: str, *, default: int) -> int:
    """解析整数参数，非法输入回退到默认值。"""

    try:
        return int(value)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
