"""托管前端构建产物。

`web/dist` **不入库**（构建产物进 git 会让每次改前端都拖一堆 diff）。代价是
clone 下来直接跑会没有页面——所以这里必须给一条能照做的提示，而不是一个 404。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

NOT_BUILT_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>bpq — WebUI 还没构建</title>
<style>
  body{background:#0f1113;color:#e8eaed;font:15px/1.7 system-ui,-apple-system,sans-serif;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
  main{max-width:34rem;padding:2rem}
  h1{font-size:1.25rem;margin:0 0 1rem;color:#00ae42}
  code{background:#1f2226;padding:.15rem .4rem;border-radius:4px;font-size:.9em}
  pre{background:#17191c;border:1px solid #2a2e33;border-radius:8px;padding:1rem;overflow-x:auto}
  p{color:#9aa0a6}
</style></head><body><main>
<h1>WebUI 还没构建</h1>
<p>后端已经在跑了（<code>/api/health</code> 可以访问），只是前端的构建产物不在。
前端产物不入库，所以第一次跑需要自己构建一次：</p>
<pre>cd web
npm install
npm run build</pre>
<p>构建完刷新这个页面即可。开发前端时用 <code>npm run dev</code>，
它会把 <code>/api</code> 代理到这个后端。</p>
</main></body></html>
"""


def frontend_dir() -> Path:
    """构建产物的位置：仓库根的 web/dist。

    PyInstaller 打包成单文件后 `__file__` 指向运行期解压目录，不再是仓库布局，
    要改从 `sys._MEIPASS` 找——打包命令必须用 `--add-data` 把 web/dist 放到同一相对路径下。
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "web" / "dist"
    return Path(__file__).resolve().parents[3] / "web" / "dist"


def mount_frontend(app: FastAPI) -> None:
    """把前端挂上去。必须在所有 /api 路由**之后**调用——它注册了一个 catch-all。"""
    dist = frontend_dir()
    index = dist / "index.html"

    if (assets := dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):  # noqa: ANN202
        """SPA 兜底。

        StaticFiles(html=True) 只处理目录索引，不认识 /tasks/abc 这种客户端路由，
        所以深链接必须自己回 index.html 交给前端路由去分发。
        """
        if not index.exists():
            return HTMLResponse(NOT_BUILT_PAGE, status_code=200)

        candidate = (dist / full_path).resolve()
        # 防目录穿越：只允许交出 dist 里面的东西
        if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)

    if not index.exists():
        log.warning(
            "前端还没构建（%s 不存在）。后端 API 可用，网页会显示构建说明。"
            "在 web/ 下执行 npm install && npm run build 即可。", index,
        )
