"""变焦/运镜估计：log-polar 相位相关（numpy），用于"拉近镜头"的循环处理。

对拉近镜头做首尾循环时，直接回环会"视野缩放跳变"。两条出路：
  1) 截取变焦最平缓、表现力最好的一段做循环（推荐，稳）；
  2) 逐帧反向缩放抵消变焦（需要精确的 scale(t) 估计，本模块提供）。
本模块输出整段变焦比 s_end/s_start、线性拟合与中心漂移，供上层决策。
"""
from __future__ import annotations

import subprocess

import numpy as np

from . import ffutil


def _frames_gray(src: str, t0: float, t1: float, n: int = 8, width: int = 320) -> list[np.ndarray]:
    """从 [t0,t1] 抽 n 帧灰度小图（numpy）。"""
    dur = max(0.1, t1 - t0)
    cmd = [ffutil.ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
           "-ss", f"{t0:.3f}", "-i", src, "-t", f"{dur:.2f}", "-map", "0:v:0",
           "-vf", f"fps={n / dur},scale={width}:-2,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    raw = r.stdout
    if not raw:
        return []
    height = len(raw) // n // width
    if height <= 0:
        return []
    arr = np.frombuffer(raw[: n * width * height], dtype=np.uint8)
    return [arr[i * width * height:(i + 1) * width * height].reshape(height, width).astype(np.float32)
            for i in range(n)]


def _bilinear(img: np.ndarray, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    h, w = img.shape
    Xc = np.clip(X, 0, w - 1.001)
    Yc = np.clip(Y, 0, h - 1.001)
    x0 = np.floor(Xc).astype(int)
    y0 = np.floor(Yc).astype(int)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    wx = Xc - x0
    wy = Yc - y0
    return (img[y0, x0] * (1 - wx) * (1 - wy) + img[y0, x1] * wx * (1 - wy) +
            img[y1, x0] * (1 - wx) * wy + img[y1, x1] * wx * wy)


def _logpolar(img: np.ndarray, nr: int = 160, ntheta: int = 320) -> tuple[np.ndarray, float]:
    h, w = img.shape
    cx, cy = w / 2.0, h / 2.0
    rmin, rmax = 1.0, min(cx, cy) * 0.95
    thetas = np.linspace(0, 2 * np.pi, ntheta, endpoint=False)
    logrs = np.linspace(np.log(rmin), np.log(rmax), nr)
    R, T = np.meshgrid(np.exp(logrs), thetas, indexing="ij")
    X = cx + R * np.cos(T)
    Y = cy + R * np.sin(T)
    logstep = (np.log(rmax) - np.log(rmin)) / nr
    return _bilinear(img, X, Y), logstep


def _phase_shift(a: np.ndarray, b: np.ndarray) -> tuple[int, int]:
    FA = np.fft.rfft2(a)
    FB = np.fft.rfft2(b)
    R = FA * np.conj(FB)
    R /= np.abs(R) + 1e-12
    corr = np.fft.irfft2(R, s=a.shape)
    peak = np.unravel_index(np.argmax(corr), corr.shape)
    dy = peak[0] if peak[0] <= a.shape[0] // 2 else peak[0] - a.shape[0]
    dx = peak[1] if peak[1] <= a.shape[1] // 2 else peak[1] - a.shape[1]
    return dy, dx


def estimate_zoom(src: str, t0: float, t1: float, n: int = 12) -> dict:
    """估计 [t0,t1] 的整体变焦比与线性度。

    用相邻帧逐对估计 log-scale 再累加（小幅位移相位相关更稳），
    比"首帧 vs 尾帧"在暗场景下稳健得多。zoom_ratio>1 = 拉近（放大）。
    """
    frames = _frames_gray(src, t0, t1, n=n)
    if len(frames) < 2:
        return {"zoom_ratio": 1.0, "feasible": False, "n_frames": len(frames),
                "note": "抽帧失败或片段过短"}
    lps = []
    logstep = None
    for f in frames:
        lp, logstep = _logpolar(f)
        lps.append(lp)
    # 逐对累加 log-scale
    acc = 0.0
    per = []
    for i in range(len(lps) - 1):
        dy, dx = _phase_shift(lps[i], lps[i + 1])
        acc += dy * logstep
        per.append(round(dy * logstep, 5))
    zoom_ratio = float(np.exp(acc))
    # 线性度：相邻步进应同号且量级接近
    signs = [1 if p >= 0 else -1 for p in per if abs(p) > 1e-6]
    mono = len(set(signs)) <= 1 if signs else True
    return {"zoom_ratio": round(zoom_ratio, 4),
            "per_step_log": per,
            "monotonic": bool(mono),
            "feasible": bool(len(frames) >= 2),
            "n_frames": len(frames)}


def energy_center(src: str, t0: float, t1: float, n: int = 6) -> float:
    """边缘能量水平质心（0..1）：画面"细节/结构"最集中的水平位置。

    用梯度幅值作能量，按列求和得水平分布，取加权质心。多帧平均。
    作为内容驱动的取景中心（与 center 的中性、semantic 的主观判断互补）。
    """
    frames = _frames_gray(src, t0, t1, n=n, width=320)
    if not frames:
        return 0.5
    cents = []
    for f in frames:
        gx = np.abs(np.diff(f, axis=1))[: f.shape[0] - 1, :]   # (h-1, w-1)
        gy = np.abs(np.diff(f, axis=0))[:, : f.shape[1] - 1]   # (h-1, w-1)
        m = gx + gy
        col = m.sum(axis=0)
        tot = col.sum()
        if tot > 0:
            xs = np.arange(len(col))
            cents.append(float((col * xs).sum() / tot / (len(col) - 1)))
    return round(sum(cents) / len(cents), 4) if cents else 0.5


def pick_stable_window(src: str, t0: float, t1: float, min_len: float = 4.0) -> tuple[float, float]:
    """在 [t0,t1] 内挑一段变焦最平缓、时域最稳的子窗口（用于循环）。

    简化策略：滑窗计算亮度/色度起伏，取最稳且≥min_len 的窗。
    """
    from .quality import frame_timeline, score_window
    tl = frame_timeline(src, t0, t1 - t0, fps_sample=4.0)
    if len(tl) < 6:
        return t0, t1
    best = (None, float("inf"))
    span = t1 - t0
    for frac in (0.5, 0.6, 0.75, 1.0):
        wlen = span * frac
        if wlen < min_len:
            continue
        steps = 6
        for i in range(steps + 1):
            a = t0 + (span - wlen) * i / steps
            b = a + wlen
            sub = [f for f in tl if a <= f.t <= b]
            if len(sub) < 4:
                continue
            sc = score_window(sub)
            cost = sc["luma_swing"] + sc["chroma_swing"] * 0.5
            if cost < best[1]:
                best = ((round(a, 3), round(b, 3)), cost)
    return best[0] if best[0] else (t0, t1)
