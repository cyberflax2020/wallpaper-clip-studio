"""镜头定位：场景切割 + 特殊起点检测。

三种起点模式：
  - scene       : 用 scene 分数找包围目标时刻的镜头边界（常规）。
  - audio_onset : 以某句台词/声音出现处为起点（在中心声道上检测静音→有声的拐点）。
  - fade_clean  : 前一镜头用渐变转场，不能用关键帧；从渐变结束后的"干净帧"开始。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import ffutil


@dataclass
class Shot:
    start: float
    end: float                 # 不含尾（下一镜头首帧）
    fps: float = 23.976
    start_mode: str = "scene"
    evidence: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start


def scene_cuts(path: str, t0: float, t1: float, threshold: float = 0.05) -> list[tuple[float, float]]:
    """返回 [t0, t1] 内所有场景切换 (绝对时间, 分数)。

    ffmpeg 的 scene 分数标注在"新镜头的第一帧"上。
    注意 -ss 放在 -i 前时 showinfo 的 pts_time 从 0 起算，需加回 t0。
    """
    cmd = [ffutil.ffmpeg_bin(), "-hide_banner", "-ss", f"{t0:.3f}", "-i", path,
           "-t", f"{(t1 - t0):.2f}", "-map", "0:v:0", "-an",
           "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"]
    r = ffutil.run(cmd, timeout=600)
    cuts: list[tuple[float, float]] = []
    # showinfo 行含 pts_time；scene 分数在 select 前的日志里，单独再抓一次
    for m in re.finditer(r"pts_time:\s*([\d.]+)", r.stderr or ""):
        cuts.append((float(m.group(1)) + t0, -1.0))
    scores = _scene_scores(path, t0, t1, threshold)
    merged: list[tuple[float, float]] = []
    for (t, _) in cuts:
        best = -1.0
        for st, sc in scores:
            if abs(st - t) < 0.2:
                best = sc
        merged.append((round(t, 3), best))
    return merged


def _scene_scores(path: str, t0: float, t1: float, threshold: float) -> list[tuple[float, float]]:
    cmd = [ffutil.ffmpeg_bin(), "-hide_banner", "-ss", f"{t0:.3f}", "-i", path,
           "-t", f"{(t1 - t0):.2f}", "-map", "0:v:0", "-an",
           "-vf", f"select='gt(scene,{threshold})',metadata=print", "-f", "null", "-"]
    r = ffutil.run(cmd, timeout=600)
    out: list[tuple[float, float]] = []
    t = None
    for line in (r.stderr or "").splitlines():
        mt = re.search(r"pts_time:\s*([\d.]+)", line)
        if mt:
            t = float(mt.group(1)) + t0
        ms = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if ms and t is not None:
            out.append((t, float(ms.group(1))))
            t = None
    return out


def shot_around(path: str, target_start: float, target_end: float,
                search_pad: float = 40.0, threshold: float = 0.05) -> Shot:
    """找包围 [target_start, target_end] 的那个镜头（常规 scene 模式）。"""
    cuts = scene_cuts(path, target_start - search_pad, target_end + search_pad, threshold)
    mid = (target_start + target_end) / 2
    starts = [t for t, _ in cuts if t <= mid]
    ends = [t for t, _ in cuts if t > mid]
    start = max(starts) if starts else target_start
    end = min(ends) if ends else target_end
    fps = ffutil.stream_info(path)["fps"] or 23.976
    return Shot(start=round(start, 3), end=round(end, 3), fps=fps, start_mode="scene",
                evidence=f"cuts={[(round(t,2), round(s,3)) for t, s in cuts]}")


def audio_onset(path: str, t0: float, t1: float,
                silence_db: float = -35.0, min_silence: float = 0.25) -> float | None:
    """在中心声道上找"静音→有声"的拐点（台词出现处），返回绝对秒。

    A.I./Arrival 这类多声道源，台词基本在 FC（c2）。用 silencedetect：
    每段 silence_end 就是一次声音出现；取落在 [t0,t1] 内、最靠近 t0 的那个。
    """
    cmd = [ffutil.ffmpeg_bin(), "-hide_banner", "-ss", f"{t0 - 5:.3f}", "-i", path,
           "-t", f"{(t1 - t0 + 10):.2f}",
           "-af", f"pan=mono|c0=c2,silencedetect=noise={silence_db}dB:d={min_silence}",
           "-f", "null", "-"]
    r = ffutil.run(cmd, timeout=600)
    onsets: list[float] = []
    for m in re.finditer(r"silence_end:\s*([\d.]+)", r.stderr or ""):
        onsets.append(float(m.group(1)) + (t0 - 5))
    cand = [t for t in onsets if t0 - 1.0 <= t <= t1 + 1.0]
    return round(min(cand), 3) if cand else (round(onsets[0], 3) if onsets else None)


def fade_clean_start(path: str, t0: float, t1: float, fps_sample: float = 12.0) -> float:
    """前一镜头是渐变转场时，找渐变结束后的第一个"干净帧"。

    方法：采样亮度时间线，渐变（淡入）期间亮度爬升，进入稳定后视为干净。
    取亮度首次进入"末段平台期"（与窗口后段均值接近）的时刻。
    """
    from .quality import frame_timeline
    tl = frame_timeline(path, t0, t1 - t0, fps_sample=fps_sample)
    if len(tl) < 4:
        return t0
    tail_mean = sum(f.yavg for f in tl[-max(3, len(tl) // 5):]) / max(3, len(tl) // 5)
    thresh = tail_mean * 0.96
    for fs in tl:
        if fs.yavg >= thresh:
            return round(fs.t, 3)
    return round(tl[0].t, 3)


def locate(path: str, target_start: float, target_end: float, start_mode: str = "scene",
           **kw) -> Shot:
    """统一定位入口。start_mode ∈ {scene, audio_onset, fade_clean, exact}。"""
    if start_mode == "exact":
        fps = ffutil.stream_info(path)["fps"] or 23.976
        return Shot(start=round(target_start, 3), end=round(target_end, 3), fps=fps,
                    start_mode="exact", evidence="给定精确边界")
    if start_mode == "audio_onset":
        onset = audio_onset(path, target_start, target_end,
                            silence_db=kw.get("silence_db", -35.0),
                            min_silence=kw.get("min_silence", 0.25))
        s = onset if onset is not None else target_start
        # 终点仍用 scene 找下一个切换
        cuts = scene_cuts(path, s, target_end + 40, 0.05)
        ends = [t for t, _ in cuts if t > s + 0.3]
        e = min(ends) if ends else target_end
        fps = ffutil.stream_info(path)["fps"] or 23.976
        return Shot(start=round(s, 3), end=round(e, 3), fps=fps, start_mode="audio_onset",
                    evidence=f"audio_onset={onset}, next_cut={ends[:3]}")
    if start_mode == "fade_clean":
        s = fade_clean_start(path, target_start, target_end)
        cuts = scene_cuts(path, s, target_end + 40, 0.05)
        ends = [t for t, _ in cuts if t > s + 0.3]
        e = min(ends) if ends else target_end
        fps = ffutil.stream_info(path)["fps"] or 23.976
        return Shot(start=round(s, 3), end=round(e, 3), fps=fps, start_mode="fade_clean",
                    evidence=f"fade_clean_start={s}, next_cut={ends[:3]}")
    return shot_around(path, target_start, target_end,
                       search_pad=kw.get("search_pad", 40.0), threshold=kw.get("threshold", 0.05))
