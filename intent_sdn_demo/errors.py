"""领域错误定义：为 API 和核心逻辑提供不泄露敏感信息的失败原因。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntentError(Exception):
    """可预期的输入、模型或决策错误，包含稳定的错误码和 HTTP 状态。"""

    code: str
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        """以面向用户的安全消息呈现异常。"""

        return self.message
