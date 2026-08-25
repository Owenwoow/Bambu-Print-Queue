"""通道 B：逆向云 API。v0.1 只占位，不实现。

不实现的理由（见技术报告「通道 B 与厂商风险」）：
- 社区明确警告云连接流可能导致临时封号
- 从 Bambu Connect 提取的证书有效期约 1 年，过期后打印机网络功能可能降级
- 认证/签名机制随固件持续变动

留这个文件是为了让 transport.build() 的分支和接口位置固定下来，
将来真要做时不用改上层。
"""

from __future__ import annotations

from pathlib import Path

from bpq.config import Config
from bpq.models import PrinterState, Task
from bpq.transport.base import PrinterTransport

_MSG = "通道 B（云 API）在 v0.1 不实现，请把 [transport].channel 设为 lan"


class CloudTransport(PrinterTransport):
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def upload(self, local_path: Path, remote_name: str) -> None:
        raise NotImplementedError(_MSG)

    def start(self, task: Task) -> str:
        raise NotImplementedError(_MSG)

    def get_state(self, timeout: float = 10.0) -> PrinterState:
        raise NotImplementedError(_MSG)
