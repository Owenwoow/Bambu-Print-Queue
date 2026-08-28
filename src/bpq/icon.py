"""绘制 bpq 的视觉标记：圆角方块 + 居中白色粗体 "b"。

这个图形要在三个地方长得一模一样：WebUI 的 favicon
（`web/public/favicon.svg`）、Windows exe 文件本身的图标（`packaging/bpq.ico`，
由 `scripts/gen_icon.py` 生成）、托盘常驻程序运行时在系统托盘显示的图标
（`tray.py` 的 `_make_icon_image`，背景色还要跟着打印机状态变）。三处各画一遍
容易画歪，统一在这里画一次，三处都调用 `draw_mark`。

Pillow 只是 `build` extra 的依赖（打包/托盘才需要，见 pyproject.toml），
daemon/CLI 主流程的机器不一定装了它，所以跟 `tray.py` 一样，把
`from PIL import ...` 放进函数体内延迟导入，`import bpq.icon` 本身不能因为
没装 Pillow 而失败。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

_BG = (6, 124, 56)  # #067c38，与 favicon.svg 一致
_FG = (255, 255, 255)

_FONT_PATH = r"C:\Windows\Fonts\segoeuib.ttf"  # Segoe UI Bold，和 favicon.svg 字体栈第一优先级一致


def draw_mark(
    size: int,
    *,
    bg: tuple[int, int, int] = _BG,
    fg: tuple[int, int, int] = _FG,
) -> Image.Image:
    """画 favicon.svg 同款：圆角方块 + 居中粗体 "b"。

    纯函数：不碰全局状态、不做任何 I/O、不 import 任何 Windows-only 的符号
    （`ctypes`/`winreg` 之类不该出现在这里）——这个模块以后可能在非 Windows
    环境下被 import/测试（比如 CI 的 ubuntu-latest 跑 mypy/pytest）。
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    box = (0, 0, size - 1, size - 1)
    radius = size * 7 / 32  # 圆角比例与 favicon.svg 的 rx="7" / width="32" 一致
    try:
        draw.rounded_rectangle(box, radius=radius, fill=(*bg, 255))
    except AttributeError:
        # 较老的 Pillow 版本没有 rounded_rectangle：退化成直角矩形，装饰性圆角
        # 不值得为它让整个函数抛异常。
        draw.rectangle(box, fill=(*bg, 255))

    font_size = int(size * 0.6)
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype(_FONT_PATH, font_size)
    except (OSError, ImportError):
        # 找不到字体文件（非 Windows 环境，或者这台机器没装 Segoe UI Bold）：
        # 退化成 Pillow 内置默认字体，不能因为一个字体文件不存在就让
        # `import bpq.icon` 本身失败。
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), "b", font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) / 2 - bbox[0]
    y = (size - text_h) / 2 - bbox[1]
    draw.text((x, y), "b", font=font, fill=(*fg, 255))

    return img
