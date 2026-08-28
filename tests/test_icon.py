"""bpq.icon 纯函数的单元测试。"""

from __future__ import annotations

from PIL import Image

from bpq import icon


class Test_draw_mark:
    """圆角方块 + 白色 "b" 字标生成。"""

    def test_返回_pil_image_对象(self) -> None:
        result = icon.draw_mark(64)
        assert isinstance(result, Image.Image)

    def test_图片尺寸符合_size_参数(self) -> None:
        result = icon.draw_mark(64)
        assert result.size == (64, 64)

    def test_自定义尺寸(self) -> None:
        result = icon.draw_mark(128)
        assert result.size == (128, 128)

    def test_生成的图片是_rgba(self) -> None:
        result = icon.draw_mark(64)
        assert result.mode == "RGBA"

    def test_默认颜色画出的图不是全透明(self) -> None:
        result = icon.draw_mark(64)
        alphas = [px[3] for px in result.getdata()]
        assert any(a > 0 for a in alphas)

    def test_自定义颜色画出的图不是全透明(self) -> None:
        result = icon.draw_mark(64, bg=(10, 20, 30), fg=(200, 200, 200))
        alphas = [px[3] for px in result.getdata()]
        assert any(a > 0 for a in alphas)
