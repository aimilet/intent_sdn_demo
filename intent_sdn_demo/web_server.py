"""本地 Web 服务：暴露意图解析和策略编译 API，且只绑定回环地址。"""

from __future__ import annotations

import argparse
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import urlparse

from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.service import IntentSdnService


LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 64 * 1024


class IntentSdnRequestHandler(BaseHTTPRequestHandler):
    """处理受限 JSON API；不记录请求正文，避免原始意图进入服务器日志。"""

    service: ClassVar[IntentSdnService]

    def do_GET(self) -> None:  # noqa: N802
        """返回健康状态、固定拓扑或最小 API 说明。"""

        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/topology":
            self._send_json(HTTPStatus.OK, self.service.topology_snapshot())
            return
        if path == "/":
            self._send_json(
                HTTPStatus.OK,
                {
                    "service": "intent-sdn-demo",
                    "endpoints": [
                        "GET /api/health",
                        "GET /api/topology",
                        "POST /api/intents/parse",
                        "POST /api/policies/compile",
                    ],
                },
            )
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "请求的资源不存在。")

    def do_POST(self) -> None:  # noqa: N802
        """处理 JSON 解析和编译请求，错误时不泄露内部栈或配置。"""

        path = urlparse(self.path).path
        try:
            payload = self._read_json_body()
            if path == "/api/intents/parse":
                self._send_json(HTTPStatus.OK, self.service.parse_request(payload).to_dict())
                return
            if path == "/api/policies/compile":
                self._send_json(HTTPStatus.OK, self.service.compile_request(payload).to_dict())
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "请求的资源不存在。")
        except IntentError as exc:
            self._send_error_json(exc.status_code, exc.code, exc.message)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_json", "请求体不是合法 UTF-8 JSON。")
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_request", "请求体格式无效。")

    def _read_json_body(self) -> object:
        """在读取前限制 Content-Length，防止本地接口被大请求耗尽内存。"""

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise IntentError("missing_length", "请求必须提供 Content-Length。")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise IntentError("invalid_length", "Content-Length 必须是整数。") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise IntentError("request_too_large", "请求体超过本地服务允许的大小。", 413)
        raw_body = self.rfile.read(length)
        return json.loads(raw_body.decode("utf-8"))

    def _send_json(self, status: HTTPStatus | int, payload: object) -> None:
        """以 UTF-8 JSON 写入成功响应。"""

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, status: HTTPStatus | int, code: str, message: str) -> None:
        """统一输出不包含内部异常详情的错误结构。"""

        self._send_json(status, {"error": {"code": code, "message": message}})

    def log_message(self, format: str, *args: object) -> None:
        """只记录请求方法和状态等 HTTP 元信息，避免默认日志包含查询或正文。"""

        status = args[1] if len(args) > 1 else "unknown"
        LOGGER.info("HTTP 请求完成：方法=%s，状态=%s", self.command, status)


def create_server(port: int = 8765, service: IntentSdnService | None = None) -> ThreadingHTTPServer:
    """创建回环地址服务器，供测试和命令行入口复用。"""

    if not 0 <= port <= 65535:
        raise ValueError("端口必须在 0 到 65535 之间。")
    IntentSdnRequestHandler.service = service or IntentSdnService()
    return ThreadingHTTPServer(("127.0.0.1", port), IntentSdnRequestHandler)


def main() -> None:
    """解析启动参数并阻塞运行本地服务。"""

    parser = argparse.ArgumentParser(description="启动车联网通信意图转译本地服务。")
    parser.add_argument("--port", type=int, default=8765, help="本地监听端口，默认 8765。")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server = create_server(args.port)
    LOGGER.info("意图转译服务已启动：http://127.0.0.1:%s", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("收到终止信号，正在停止服务。")
    finally:
        server.server_close()
