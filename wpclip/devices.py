"""设备 / 屏幕比例预设库。

壁纸比例必须与目标屏幕一致才能"铺满且不裁切"。这里收录常见机型，
也支持 --ratio W:H 自定义任意比例。新机型直接往 DEVICES 里加即可。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Device:
    key: str
    name: str          # 中文名
    name_en: str       # 英文名
    native: tuple[int, int]   # 原生像素分辨率
    ratio: tuple[int, int]    # 约分后的宽高比（用于裁剪计算）

    @property
    def ratio_value(self) -> float:
        return self.ratio[0] / self.ratio[1]


def _mk(key, zh, en, w, h):
    from math import gcd
    g = gcd(w, h)
    return Device(key, zh, en, (w, h), (w // g, h // g))


DEVICES: dict[str, Device] = {d.key: d for d in [
    _mk("mbp16-m4",   "MacBook Pro 16 英寸 (M4 Pro/Max, 2024+)", "MacBook Pro 16-inch (M4 Pro/Max, 2024+)", 3456, 2234),
    _mk("mbp16-2021", "MacBook Pro 16 英寸 (2021-2023)",          "MacBook Pro 16-inch (2021-2023)",          3456, 2234),
    _mk("mbp14",      "MacBook Pro 14 英寸",                      "MacBook Pro 14-inch",                      3024, 1964),
    _mk("mba15",      "MacBook Air 15 英寸",                      "MacBook Air 15-inch",                      2880, 1864),
    _mk("mba13",      "MacBook Air 13 英寸",                      "MacBook Air 13-inch",                      2560, 1664),
    _mk("imac24",     "iMac 24 英寸 (4.5K)",                      "iMac 24-inch (4.5K)",                      4480, 2520),
    _mk("studio27",   "Apple Studio Display (27 英寸 5K)",        "Apple Studio Display (27-inch 5K)",        5120, 2880),
    _mk("proxdr32",   "Apple Pro Display XDR (32 英寸 6K)",       "Apple Pro Display XDR (32-inch 6K)",       6016, 3384),
    _mk("uhd16x9",    "通用 4K 显示器 (16:9)",                    "Generic 4K display (16:9)",                3840, 2160),
    _mk("hd16x9",     "通用 1080p 显示器 (16:9)",                 "Generic 1080p display (16:9)",             1920, 1080),
]}


def resolve(spec: str) -> Device:
    """解析设备参数：预设 key（如 'mbp16-m4'）或自定义 '宽x高' / '宽:高'。"""
    key = spec.strip().lower()
    if key in DEVICES:
        return DEVICES[key]
    for sep in ("x", ":", "×"):
        if sep in key:
            w, h = key.split(sep, 1)
            return _mk("custom", f"自定义 {w}x{h}", f"Custom {w}x{h}", int(w), int(h))
    raise ValueError(f"未知设备/比例: {spec}（可用预设: {', '.join(DEVICES)}，或传 宽x高）")
