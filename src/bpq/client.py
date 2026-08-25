"""CLI 找 daemon 说话的客户端。

为什么需要它：`bpq submit` 要读 AMS 才能配料，`bpq status` 要读打印机状态——
v0.1 里这两条都是自己建 MQTT 连接。但打印机同一时刻只接受一个连接，daemon 常连着的
时候，CLI 一连就把 daemon 踢下线，daemon 重连又把 CLI 踢掉，两边互相打架。
v0.1 没暴露这个问题，是因为那时 daemon 也是用完就断。

所以：daemon 在跑就走 HTTP 问它（它手里有现成的缓存快照，不需要任何新连接），
daemon 没跑才自己直连。

刻意只用标准库 urllib：CLI 的启动开销和依赖面不该因为这个多一分。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from bpq.config import Config

log = logging.getLogger(__name__)

TIMEOUT = 5.0


class DaemonUnavailable(RuntimeError):
    """daemon 没在跑，或者它的 web 服务没开。"""


class DaemonError(RuntimeError):
    """daemon 在，但它拒绝了这个请求。detail 是给人看的中文。"""


class DaemonClient:
    def __init__(self, cfg: Config, *, timeout: float = TIMEOUT) -> None:
        self.cfg = cfg
        self.timeout = timeout
        # host 是 0.0.0.0 时那是「监听哪些网卡」，连接要用回环地址。
        host = cfg.web.host
        if host in ("0.0.0.0", "", "::"):
            host = "127.0.0.1"
        self.base = f"http://{host}:{cfg.web.port}"

    # ------------------------------------------------------------ 探活

    def probe(self) -> dict | None:
        """daemon 在不在。超时或拒连返回 None，不抛——调用方拿它决定走哪条路。"""
        if not self.cfg.web.enabled:
            return None
        try:
            return self._request("GET", "/api/health")
        except (DaemonUnavailable, DaemonError):
            return None

    # ------------------------------------------------------------ 请求

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: dict | None = None) -> Any:
        return self._request("POST", path, body)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        if self._cookie:
            req.add_header("Cookie", self._cookie)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                # 关掉了本机免鉴权。CLI 用 config 里的口令换一次 token 再重试。
                if not self._cookie and self.cfg.web.password:
                    self._login()
                    return self._request(method, path, body)
                raise DaemonError(
                    "daemon 拒绝了请求（未登录）。config.toml 的 [web] 段里，"
                    "要么把 allow_local_no_auth 设回 true，要么填上 password。"
                ) from exc
            detail = _detail(exc)
            raise DaemonError(detail) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise DaemonUnavailable(str(exc)) from exc

    _cookie: str = ""

    def _login(self) -> None:
        req = urllib.request.Request(
            self.base + "/api/auth/login",
            data=json.dumps({"password": self.cfg.web.password}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                cookie = resp.headers.get("Set-Cookie", "")
                self._cookie = cookie.split(";", 1)[0] if cookie else ""
        except urllib.error.HTTPError as exc:
            raise DaemonError(f"用 config.toml 里的口令登录 daemon 失败：{_detail(exc)}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DaemonUnavailable(str(exc)) from exc


def _detail(exc: urllib.error.HTTPError) -> str:
    """把后端的 detail 取出来。那是写给人看的中文，比「HTTP 400」有用得多。"""
    try:
        body = json.loads(exc.read())
        if isinstance(body, dict) and body.get("detail"):
            return str(body["detail"])
    except Exception:  # noqa: BLE001 - 响应体不是 JSON 就退回状态码
        pass
    return f"HTTP {exc.code} {exc.reason}"
