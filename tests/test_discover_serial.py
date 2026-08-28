"""自动发现 SERIAL 的测试。

背景：WebUI「设置 → 打印机连接」页面新增了「自动获取序列号」按钮，原理是连一次
FTPS（不需要预先知道 SERIAL，登录只需要固定用户名 bblp + Access Code），从
logger/ 目录的文件名里正则提取 SERIAL（格式见 docs/验证记录-通道A.md 第 13 行）。

这里只测 `_parse_serial_from_logger_listing()` 这个不碰网络的纯函数——真正的
正确性风险全在这条正则上，FTPS 握手本身已经在 test_link.py / 真机验证里核实过。
用的全是虚构占位序列号（如 AB12CD34EF5678G），不是真实机器标识。
"""

from __future__ import annotations

from bpq.transport.lan import _parse_serial_from_logger_listing


def test_从正常文件名里提出_serial():
    names = [
        "AB12CD34EF5678G_08-27_13_21_51.653_v01.08.01.00_idx_1.log",
        "AB12CD34EF5678G_08-23_15_28_43.315_v01.08.01.00_idx_2.log",
    ]
    assert _parse_serial_from_logger_listing(names) == "AB12CD34EF5678G"


def test_跳过_latest_不报错():
    """latest 不匹配日志文件名的模式，必须跳过而不是让整个解析失败。"""
    names = [
        "latest",
        "AB12CD34EF5678G_08-27_13_21_51.653_v01.08.01.00_idx_1.log",
    ]
    assert _parse_serial_from_logger_listing(names) == "AB12CD34EF5678G"


def test_乱码文件名混在其中也能挑出正常的那条():
    names = [
        "latest",
        "core.log",
        "readme.txt",
        "AB12CD34EF5678G_08-27_13_21_51.653_v01.08.01.00_idx_1.log",
        "AB12CD34EF5678G_08-23_15_28_43.315_v01.08.01.00_idx_2.log",
    ]
    assert _parse_serial_from_logger_listing(names) == "AB12CD34EF5678G"


def test_目录里全是无法识别的文件名时返回_None():
    """找不到匹配项要返回 None（由 discover_serial() 转成人话报错），不是抛异常。"""
    names = ["latest", "core.log"]
    assert _parse_serial_from_logger_listing(names) is None


def test_空列表返回_None():
    assert _parse_serial_from_logger_listing([]) is None


def test_取第一个匹配到的文件名():
    """目录里通常有多条历史日志，固定取第一个匹配到的即可，不需要挑「最新」的那条
    ——同一台打印机的 SERIAL 在所有日志文件名里都是同一个值。"""
    names = [
        "AB12CD34EF5678G_08-23_15_28_43.315_v01.08.01.00_idx_2.log",
        "AB12CD34EF5678G_08-27_13_21_51.653_v01.08.01.00_idx_1.log",
    ]
    assert _parse_serial_from_logger_listing(names) == "AB12CD34EF5678G"
