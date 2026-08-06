"""逐帧质量时间线 + 选窗打分 + 接缝多维度量。

无缝循环不只是"首尾帧位置一致"。这里建模并度量：
  - 明度不连续（首尾平均亮度差）
  - 色彩不连续（首尾色度差）
  - 全黑/淡入淡出帧（黑帧时间线，选窗时避开）
  - 位置/运动跳变（首尾帧 PSNR / 差异）
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from . import ffutil


@dataclass
class FrameStat:
    t: float
    yavg: float
    uavg: float
    vavg: float


def frame_timeline(path: str, start: float, duration: float, fps_sample: float = 5.0) -> list[FrameStat]:
    """用 signalstats 采样 [start, start+duration] 的逐帧 Y/U/V 平均值。

    fps_sample 控制采样帧率（越高越慢越细）。
    """
    cmd = [ffutil.ffmpeg_bin(), "-hide_banner", "-ss", f"{start:.3f}", "-i", path,
           "-t", f"{duration:.2f}", "-map", "0:v:0",
           "-vf", f"fps={fps_sample},signalstats,metadata=print",
           "-f", "null", "-"]
    r = ffutil.run(cmd, timeout=600)
    out: list[FrameStat] = []
    t = yavg = uavg = vavg = None
    for line in (r.stderr or "").splitlines():
        mt = re.search(r"pts_time:\s*([\d.]+)", line)
        if mt:
            if t is not None and yavg is not None:
                out.append(FrameStat(t, yavg, uavg or 128, vavg or 128))
            t = float(mt.group(1)) + start
            yavg = uavg = vavg = None
            continue
        for key, var in (("YAVG", "yavg"), ("UAVG", "uavg"), ("VAVG", "vavg")):
            m = re.search(rf"lavfi\.signalstats\.{key}=([\d.]+)", line)
            if m:
                val = float(m.group(1))
                if var == "yavg":
                    yavg = val
                elif var == "uavg":
                    uavg = val
                else:
                    vavg = val
    if t is not None and yavg is not None:
        out.append(FrameStat(t, yavg, uavg or 128, vavg or 128))
    return out


def _gray_frames(path: str, t0: float, t1: float, fps_sample: float, width: int = 160):
    import subprocess, numpy as np
    dur = max(0.1, t1 - t0)
    cmd = [ffutil.ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
           "-ss", f"{t0:.3f}", "-i", path, "-t", f"{dur:.2f}", "-map", "0:v:0",
           "-vf", f"fps={fps_sample},scale={width}:-2,format=gray",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    raw = r.stdout
    if not raw:
        return [], 0
    n = int(round(dur * fps_sample)) or 1
    height = len(raw) // n // width
    if height <= 0:
        return [], 0
    arr = np.frombuffer(raw[: n * width * height], dtype=np.uint8)
    frames = [arr[i * width * height:(i + 1) * width * height]
              .reshape(height, width).astype(np.float32) for i in range(n)]
    return frames, fps_sample


def motion_timeline(path: str, start: float, duration: float,
                    fps_sample: float = 6.0) -> list[tuple[float, float]]:
    """逐帧运动量（相邻帧平均绝对差）。用于挑选低运动的循环/折返点。"""
    frames, fs = _gray_frames(path, start, start + duration, fps_sample)
    out = []
    for i in range(1, len(frames)):
        mad = float(abs(frames[i] - frames[i - 1]).mean())
        out.append((start + i / fs, mad))
    return out


def trim_to_content(path: str, start: float, end: float,
                    lo: float = 25.0, hi: float = 230.0,
                    fps_sample: float = 12.0) -> tuple[float, float]:
    """裁掉首尾的黑屏/白屏帧，让循环接缝落在可见内容上，避免黑闪/白闪。

    返回收紧后的 [start', end']；若整段都不可见则原样返回。
    """
    tl = frame_timeline(path, start, end - start, fps_sample=fps_sample)
    if not tl:
        return start, end
    good = [fs for fs in tl if lo <= fs.yavg <= hi]
    if not good:
        return start, end
    # 取首尾"可见"帧，并向内留一帧余量，避免擦边暗帧
    s = good[0].t
    e = good[-1].t
    step = 1.0 / fps_sample
    s = min(s + step, e)
    e = max(e - step, s)
    return round(s, 3), round(e, 3)


def motion_at_ends(motion: list[tuple[float, float]], tail: int = 2) -> float:
    """首尾平均运动量（越小越适合做循环接缝/折返点）。"""
    if not motion:
        return 0.0
    head = motion[:tail] or motion
    tailn = motion[-tail:] or motion
    return (sum(m for _, m in head) / len(head) + sum(m for _, m in tailn) / len(tailn)) / 2


def detect_black_ranges(timeline: list[FrameStat], y_thresh: float = 20.0,
                        min_gap: float = 0.2) -> list[tuple[float, float]]:
    """找出时间线里的"近黑"区间 [(t0, t1), ...]（淡入淡出/黑帧）。"""
    blacks: list[list[float]] = []
    cur: list[float] | None = None
    for fs in timeline:
        if fs.yavg < y_thresh:
            if cur is None:
                cur = [fs.t, fs.t]
            else:
                cur[1] = fs.t
        else:
            if cur is not None:
                blacks.append(cur)
                cur = None
    if cur is not None:
        blacks.append(cur)
    return [(a, b) for a, b in blacks if (b - a) >= 0 or True]


def score_window(timeline: list[FrameStat]) -> dict:
    """给一个窗口的时域稳定性打分：亮度/色度起伏越小越稳定（倒放回放不"呼吸"）。"""
    if len(timeline) < 2:
        return {"luma_swing": 0.0, "chroma_swing": 0.0, "stability": 1.0}
    ys = [f.yavg for f in timeline]
    us = [f.uavg for f in timeline]
    vs = [f.vavg for f in timeline]
    luma_swing = max(ys) - min(ys)
    chroma_swing = (max(us) - min(us)) + (max(vs) - min(vs))
    # 归一化稳定度（越小越稳）
    stability = 1.0 / (1.0 + luma_swing / 40.0 + chroma_swing / 60.0)
    return {"luma_swing": round(luma_swing, 2),
            "chroma_swing": round(chroma_swing, 2),
            "stability": round(stability, 4)}


def seam_metrics(path: str) -> dict:
    """度量成片首尾接缝的多维质量。

    返回：psnr(位置差异, inf=完全一致)、亮度差、色度差、是否含黑帧。
    """
    n = ffutil.frame_count(path)
    import tempfile, os
    d = tempfile.mkdtemp(prefix="wpclip_seam_")
    f0 = os.path.join(d, "f0.png")
    f1 = os.path.join(d, "fl.png")
    try:
        ffutil.extract_frame(path, f0, n=0)
        ffutil.extract_frame(path, f1, n=n - 1)
        psnr = ffutil.psnr(f0, f1)
    finally:
        pass
    tl = frame_timeline(path, 0.0, 0.5, fps_sample=10) + \
         frame_timeline(path, max(0.0, (n / max(_fps(path), 1)) - 0.5), 0.5, fps_sample=10)
    head = tl[:len(tl) // 2] or tl
    tail = tl[len(tl) // 2:] or tl
    luma_diff = abs((statistics.mean([f.yavg for f in head]) if head else 0) -
                    (statistics.mean([f.yavg for f in tail]) if tail else 0))
    chroma_diff = abs((statistics.mean([f.uavg for f in head]) if head else 128) -
                      (statistics.mean([f.uavg for f in tail]) if tail else 128)) + \
                  abs((statistics.mean([f.vavg for f in head]) if head else 128) -
                      (statistics.mean([f.vavg for f in tail]) if tail else 128))
    has_black = any(f.yavg < 20 for f in tl)
    return {"frames": n, "seam_psnr": psnr, "luma_diff": round(luma_diff, 2),
            "chroma_diff": round(chroma_diff, 2), "has_black_frame": has_black}


def _fps(path: str) -> float:
    try:
        return ffutil.stream_info(path)["fps"]
    except Exception:
        return 23.976
