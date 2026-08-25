"""推送通知：预留接口，v0.1 不实现。

项目框架「明确不做」里写死了：失败/异常推送不实现，摄像头、spaghetti detection 一概不碰。
留这个接口是为了让 daemon 里的调用点固定下来，将来接 Bark / ntfy / Telegram 不用改调度逻辑。
"""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, title: str, body: str) -> None: ...


class NullNotifier:
    """默认实现：只写日志，不外发。"""

    def send(self, title: str, body: str) -> None:
        log.info("[notify] %s: %s", title, body)


def build(enabled: bool = False) -> Notifier:
    if enabled:
        # TODO: v0.2 起接一个真实通道（Bark / ntfy / Telegram Bot）
        log.warning("[notify].enabled=true，但 v0.1 未实现任何推送通道，回退到 NullNotifier")
    return NullNotifier()
