"""本地 Web 服务：暴露意图解析和策略编译 API，且只绑定回环地址。"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from intent_sdn_demo.errors import IntentError
from intent_sdn_demo.service import IntentSdnService


LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 64 * 1024
STATIC_DIRECTORY = Path(__file__).with_name("web")
STATIC_FILES = {
    "/": "index.html",
    "/app.js": "app.js",
    "/styles.css": "styles.css",
}


class IntentSdnRequestHandler(BaseHTTPRequestHandler):
    """处理受限 JSON API；不记录请求正文，避免原始意图进入服务器日志。"""

    service: ClassVar[IntentSdnService]

    def do_GET(self) -> None:  # noqa: N802
        """返回健康状态、固定拓扑、指标或受控的本地静态页面资源。"""

        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/topology":
            self._send_json(HTTPStatus.OK, self.service.topology_snapshot())
            return
        if path == "/api/metrics":
            self._send_json(HTTPStatus.OK, self.service.metrics_snapshot())
            return
        static_name = STATIC_FILES.get(path)
        if static_name is not None:
            self._send_static_file(static_name)
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
            if path == "/api/policies/apply":
                self._send_json(HTTPStatus.OK, self.service.apply_request(payload))
                return
            if path == "/api/policies/reset":
                self._send_json(HTTPStatus.OK, self.service.reset_request())
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

    def _send_static_file(self, file_name: str) -> None:
        """只从预定义清单读取页面资源，避免 URL 路径穿越到工作目录外。"""

        file_path = STATIC_DIRECTORY / file_name
        try:
            data = file_path.read_bytes()
        except OSError:
            LOGGER.exception("静态页面资源不可读取：%s", file_name)
            self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "请求的资源不存在。")
            return
        content_type, _ = mimetypes.guess_type(file_name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        """只记录请求方法和状态等 HTTP 元信息，避免默认日志包含查询或正文。"""

        status = args[1] if len(args) > 1 else "unknown"
        LOGGER.info("HTTP 请求完成：方法=%s，状态=%s", self.command, status)


def create_server(
    port: int = 8765,
    service: IntentSdnService | None = None,
    *,
    mininet_enabled: bool = False,
) -> ThreadingHTTPServer:
    """创建回环地址服务器，供测试和命令行入口复用。"""

    if not 0 <= port <= 65535:
        raise ValueError("端口必须在 0 到 65535 之间。")
    IntentSdnRequestHandler.service = service or IntentSdnService(mininet_enabled=mininet_enabled)
    return ThreadingHTTPServer(("127.0.0.1", port), IntentSdnRequestHandler)


def main() -> None:
    """解析启动参数并阻塞运行本地服务。"""

    parser = argparse.ArgumentParser(description="启动车联网通信意图转译本地服务。")
    parser.add_argument("--port", type=int, default=8765, help="本地监听端口，默认 8765。")
    parser.add_argument(
        "--enable-mininet",
        action="store_true",
        help="启用临时 Mininet 策略验证；该模式需要 root 权限。",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    server = create_server(args.port, mininet_enabled=args.enable_mininet)
    LOGGER.info("意图转译服务已启动：http://127.0.0.1:%s", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("收到终止信号，正在停止服务。")
    finally:
        server.server_close()
