"""裁剪窗口计算：保留全部画面高度、只裁宽度，凑出目标屏幕比例。"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Sequence


@dataclass
class CropWindow:
    label: str
    center: float     # 取景中心（0..1，相对有效画面宽度）
    x: int
    y: int
    w: int
    h: int
    ratio: float
    ratio_err: float

    @property
    def ffmpeg(self) -> str:
        """ffmpeg crop 滤镜参数 'w:h:x:y'。"""
        return f"{self.w}:{self.h}:{self.x}:{self.y}"


def compute_crops(frame_w: int, frame_h: int,
                  bars: tuple[int, int, int, int] | None,
                  target_ratio: tuple[int, int] = (1728, 1117),
                  centers: Sequence[float] = (0.5,),
                  labels: Sequence[str] | None = None) -> list[CropWindow]:
    """计算一组裁剪窗口。

    frame_w/h: 编码分辨率；bars: 有效画面区 (x,y,w,h)（cropdetect 结果，无黑边时=全帧）；
    target_ratio: 目标屏幕比例（默认 MBP16 = 1728:1117）；centers: 水平取景中心列表。
    全部尺寸取偶数（yuv420 友好），窗口不越界。
    """
    bx, by, bw, bh = bars if bars else (0, 0, frame_w, frame_h)
    tw, th = target_ratio
    target = tw / th

    h = bh - (bh % 2)
    w = round((h * target) / 2) * 2
    if w > bw:                      # 极少数情况：宽度不足，反向收缩高度
        w = bw - (bw % 2)
        h = round((w / target) / 2) * 2

    labels = list(labels) if labels else [f"crop{i}" for i in range(len(centers))]
    out: list[CropWindow] = []
    for i, c in enumerate(centers):
        c = min(max(float(c), 0.0), 1.0)
        x = bx + round(bw * c - w / 2)
        x = max(bx, min(bx + bw - w, x))
        x -= x % 2
        y = by + (bh - h) // 2
        y -= y % 2
        out.append(CropWindow(
            label=labels[i] if i < len(labels) else f"crop{i}",
            center=c, x=x, y=y, w=w, h=h,
            ratio=round(w / h, 6), ratio_err=round(abs(w / h - target), 6),
        ))
    return out


def compute_free_crop(frame_w: int, frame_h: int,
                       bars: tuple[int, int, int, int] | None,
                       target_ratio: tuple[int, int],
                       center: float = 0.5, y_center: float = 0.5,
                       height_frac: float = 0.8) -> CropWindow:
    """"解锁尺寸、固定比例"取景：不强制取满高度，而是取有效高度的 height_frac，
    按比例算宽，水平/垂直都围绕主体中心。适合语义主体的构图式取景。
    """
    bx, by, bw, bh = bars if bars else (0, 0, frame_w, frame_h)
    target = target_ratio[0] / target_ratio[1]
    h = int((bh * height_frac) // 2) * 2
    w = round((h * target) / 2) * 2
    if w > bw:
        w = bw - (bw % 2)
        h = round((w / target) / 2) * 2
    x = bx + round(bw * center - w / 2)
    x = max(bx, min(bx + bw - w, x)); x -= x % 2
    y = by + round(bh * y_center - h / 2)
    y = max(by, min(by + bh - h, y)); y -= y % 2
    return CropWindow(label="free", center=center, x=x, y=y, w=w, h=h,
                      ratio=round(w / h, 6), ratio_err=round(abs(w / h - target), 6))


def dedupe_centers(centers: dict[str, float], min_px: float, width: int) -> dict[str, float]:
    """合并过于接近的取景中心（<min_px 像素差），保留先出现的标签。"""
    kept: dict[str, float] = {}
    for label, c in centers.items():
        if all(abs(c - k) * width >= min_px for k in kept.values()):
            kept[label] = c
    return kept


def fuse_crops(crops: list["CropWindow"]) -> list["CropWindow"]:
    """把若干裁剪框做两两 + 全体平均（位置与尺寸都可融合），返回新框。

    同比例的框平均后比例不变；不同尺寸的框平均得到中间尺寸。
    结果取偶数并夹回各自来源框的并集范围内。
    """
    def mean_box(a: "CropWindow", b: "CropWindow", label: str) -> "CropWindow":
        x = round((a.x + b.x) / 2); y = round((a.y + b.y) / 2)
        w = round((a.w + b.w) / 2); h = round((a.h + b.h) / 2)
        x -= x % 2; y -= y % 2; w -= w % 2; h -= h % 2
        return CropWindow(label, a.center, x, y, w, h,
                          round(w / h, 6), round(abs(w / h - a.ratio), 6))

    out: list["CropWindow"] = []
    if len(crops) >= 2:
        n = len(crops)
        x = round(sum(c.x for c in crops) / n); y = round(sum(c.y for c in crops) / n)
        w = round(sum(c.w for c in crops) / n); h = round(sum(c.h for c in crops) / n)
        x -= x % 2; y -= y % 2; w -= w % 2; h -= h % 2
        out.append(CropWindow("fused_all", crops[0].center, x, y, w, h,
                              round(w / h, 6), 0.0))
        for i in range(n):
            for j in range(i + 1, n):
                out.append(mean_box(crops[i], crops[j],
                                    f"fused_{crops[i].label}_{crops[j].label}"))
    return out


def fusion_centers(centers: dict[str, float]) -> dict[str, float]:
    """融合策略：在已有若干策略坐标上做两两均值 + 全体均值，作为额外取景。

    返回 {'fused_all': 均值, 'fused_a_b': 两两均值, ...}（不含原策略）。
    """
    out: dict[str, float] = {}
    items = list(centers.items())
    if len(items) >= 2:
        vals = [c for _, c in items]
        out["fused_all"] = sum(vals) / len(vals)
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, ca = items[i]
                b, cb = items[j]
                out[f"fused_{a}_{b}"] = (ca + cb) / 2
    return out
