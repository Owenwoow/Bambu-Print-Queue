"""传输层：上层调度不关心底下走的是哪条通道。

- lan   通道 A：本地 MQTT + FTPS，v0.1 主线，依赖 LAN Only + Developer Mode
- cloud 通道 B：逆向云 API，仅留接口不实现（封号风险 + 证书约 1 年过期）
"""

from __future__ import annotations

from bpq.config import Config
from bpq.transport.base import PrinterTransport


def build(cfg: Config) -> PrinterTransport:
    """按配置选通道。"""
    channel = cfg.transport.channel
    if channel == "lan":
        from bpq.transport.lan import LanTransport

        return LanTransport(cfg)
    if channel == "cloud":
        from bpq.transport.cloud import CloudTransport

        return CloudTransport(cfg)
    raise ValueError(f"未知传输通道: {channel!r}（可选 lan / cloud）")


__all__ = ["PrinterTransport", "build"]
