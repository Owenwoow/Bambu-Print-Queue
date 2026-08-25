"""WebUI 的后端。

**它寄生在 daemon 进程里，不是一个独立服务。** 打印机同一时刻只接受一个 MQTT
连接，WebUI 要显示实时状态就只能借 daemon 手里那一条——独立起一个进程就意味着
要么另开连接（把 daemon 踢下线），要么自己造一层 IPC 代理（成本远高于塞进来）。

所以「一个 daemon = 一条连接 = 一个 web 服务」，daemon 那把单实例文件锁顺带
成了 WebUI 的端口守卫。
"""

from bpq.web.app import create_app

__all__ = ["create_app"]
