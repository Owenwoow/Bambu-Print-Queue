"""口令鉴权。不引入用户系统，标准库就够。

设计取舍写在这里，因为每一条都有代价：

- **无状态签名 Cookie，不是内存 session。** 这样 daemon 重启之后浏览器不用重登——
  一个跑在家里的调度器会因为各种原因重启，每次都要在手机上重新输口令太烦。
  代价是签发出去的 token 在过期前无法单独吊销，只能整体失效。

- **口令指纹进签名。** 于是「改口令 = 所有已登录设备立即登出」。这个副作用是想要的。

- **本机免鉴权（可关）。** 为了让 CLI 零配置就能走 HTTP 找 daemon 要数据。
  副作用是这台电脑上任何进程、任何用户都能不经口令操作打印任务——
  多人共用的机器上应该关掉。

- **不设 Secure cookie。** 局域网里跑的是 http，设了 Secure 浏览器就不会带上 cookie。
  这也意味着**口令是明文过局域网的**，文档里得说清楚，别复用别处的口令。

明确不做：用户表、注册、找回密码、OAuth、自签 HTTPS。想要 TLS 的自己套一层反代。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger(__name__)

COOKIE_NAME = "bpq_session"
SECRET_FILE = "bpq.secret"
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})

# 登录限流。局域网里没有这层，一个八位口令是纸糊的。
MAX_FAILURES = 5
LOCKOUT_SECONDS = 30
FAILURE_DELAY = 0.2       # 每次失败都无条件慢一点，压掉暴力尝试的速率


class AuthError(RuntimeError):
    """配置本身有问题，比如把服务暴露到局域网却没设口令。"""


def load_secret(var_dir: Path) -> bytes:
    """读取或生成签名密钥。

    与口令解耦：换口令不必换密钥，反之亦然。
    """
    path = Path(var_dir) / SECRET_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = path.read_bytes().strip()
        if len(data) >= 32:
            return data
    data = secrets.token_bytes(32)
    path.write_bytes(data)
    with_suppress_chmod(path)
    log.info("已生成新的 WebUI 签名密钥 %s", path)
    return data


def with_suppress_chmod(path: Path) -> None:
    """POSIX 上收紧权限。Windows 上 chmod 基本没意义，失败也不该影响启动。"""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def is_loopback(host: str | None) -> bool:
    return (host or "") in LOOPBACK


def check_exposure(host: str, password: str) -> None:
    """启动期硬校验：绑到局域网却没设口令，直接拒绝启动 web。

    这是防「随手把打印机的控制权暴露到内网」的最后一道闸。daemon 本体照常跑，
    定时任务不受影响——只是不开网页。
    """
    if not is_loopback(host) and host != "" and not password:
        raise AuthError(
            f"[web] host = \"{host}\" 会把 WebUI 暴露到局域网，但 password 是空的。\n"
            "同一网络里的任何人都能让打印机动起来。请在 config.toml 的 [web] 段设一个\n"
            "口令（注意局域网里是明文 http，别用你在别处用过的口令），\n"
            "或者把 host 改成 \"127.0.0.1\" 只给本机用。"
        )


def fingerprint(password: str) -> str:
    """口令的指纹。进签名，于是改口令等于让所有已发出的 token 失效。"""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()[:16]


def issue_token(secret: bytes, password: str, *, days: int) -> str:
    exp = int(time.time()) + days * 86400
    return f"{exp}.{_sign(secret, exp, password)}"


def verify_token(secret: bytes, token: str, password: str) -> bool:
    try:
        raw_exp, sig = token.split(".", 1)
        exp = int(raw_exp)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(sig, _sign(secret, exp, password))


def _sign(secret: bytes, exp: int, password: str) -> str:
    msg = f"{exp}|{fingerprint(password)}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


class LoginThrottle:
    """按来源 IP 限流。"""

    def __init__(self, max_failures: int = MAX_FAILURES,
                 lockout: float = LOCKOUT_SECONDS) -> None:
        self.max_failures = max_failures
        self.lockout = lockout
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def locked_for(self, ip: str) -> float:
        """还要锁多少秒。0 表示没锁。"""
        return max(0.0, self._locked_until.get(ip, 0.0) - time.time())

    def record_failure(self, ip: str) -> None:
        now = time.time()
        hits = [t for t in self._failures.get(ip, []) if now - t < self.lockout * 10]
        hits.append(now)
        self._failures[ip] = hits
        if len(hits) >= self.max_failures:
            self._locked_until[ip] = now + self.lockout
            self._failures[ip] = []
            log.warning("来自 %s 的登录失败次数过多，锁定 %.0f 秒", ip, self.lockout)

    def record_success(self, ip: str) -> None:
        self._failures.pop(ip, None)
        self._locked_until.pop(ip, None)
