"""视觉无损编码：HEVC(hvc1) + 正确色彩标签 + 只裁不缩。

原则：
  - 输出分辨率 = 裁剪窗口分辨率（源原生），绝不放大/缩小，最大限度保留画质。
  - 色彩范围用"直方图判定的真实范围"标注，避免播放器误展开导致发灰/压黑。
  - 色彩空间/原色/传递从源透传（bt709 SDR 或 HDR）。
  - 只保留视频流：无音频、无字幕。
"""
from __future__ import annotations

import os

from . import ffutil, loop as loopmod, probe as probemod
from .crop import CropWindow


def speed_key(sp: float) -> str:
    """倍速的规范字典键（1.0→'1', 0.75→'0.75'）。"""
    return f"{sp:g}"


def _color_args(src: str, actual_range: str | None) -> list[str]:
    """从源透传色彩元数据；范围用真实判定值。"""
    try:
        info = ffutil.stream_info(src)
    except ffutil.FFError:
        info = {}
    prims = info.get("color_primaries") or "bt709"
    trc = info.get("color_transfer") or "bt709"
    cs = info.get("color_space") or "bt709"
    rng = actual_range or probemod.detect_actual_range(src)
    return ["-color_primaries", prims, "-color_trc", trc, "-colorspace", cs,
            "-color_range", rng]


def _bitdepth_args(src: str) -> list[str]:
    """按源位深选择编码位深：10bit 源 → main10/yuv420p10le，否则 8bit。"""
    try:
        bps = ffutil.video_stream(src).get("bits_per_raw_sample")
        pix = ffutil.video_stream(src).get("pix_fmt", "")
    except ffutil.FFError:
        bps, pix = None, ""
    if bps in ("10", "12") or "10le" in pix or "12le" in pix:
        return ["-profile:v", "main10", "-pix_fmt", "yuv420p10le"]
    return ["-profile:v", "main", "-pix_fmt", "yuv420p"]


def encode(master: str, crop: CropWindow, out: str, fps: float,
           loop: str = "palindrome", k: int = 14, crf: int = 12,
           preset: str = "slow", actual_range: str | None = None,
           dwell: int = 3, timeout: float = 7200) -> dict:
    """把母带按 crop + loop 编码成 HEVC 壁纸。返回 {out, frames_expected, frames_actual}。"""
    n = ffutil.frame_count(master)
    if loop == "palindrome":
        fc, _ = loopmod.palindrome_filter(crop)
    elif loop == "palindrome_dwell":
        fc = loopmod.palindrome_dwell_filter(crop, fps, dwell)
    elif loop == "crossfade":
        fc, kk = loopmod.crossfade_filter(crop, n, k)
        kdur = kk / fps
        fc = fc.replace("__KDUR__", f"{kdur:.6f}")
    else:
        raise ValueError(f"未知循环方式: {loop}")

    expected = loopmod.expected_frames(loop, n, k, dwell)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    cmd = [ffutil.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
           "-i", master, "-filter_complex", fc, "-map", "[v]",
           "-c:v", "libx265", "-preset", preset, "-crf", str(crf)]
    cmd += _bitdepth_args(master)
    cmd += ["-tag:v", "hvc1"]
    cmd += _color_args(master, actual_range)
    cmd += ["-fps_mode", "passthrough", out]
    ffutil.run(cmd, timeout=timeout)

    actual = ffutil.frame_count(out)
    return {"out": out, "loop": loop, "frames_expected": expected, "frames_actual": actual,
            "master_frames": n}


def encode_matrix(masters: dict[str, str], crops: list[CropWindow], outdir: str,
                  name_prefix: str, fps: float, speeds: list[float],
                  loops: list[str] = None, crf: int = 12, preset: str = "slow",
                  actual_range: str | None = None, dwell: int = 3,
                  progress=None, stop=None) -> list[dict]:
    """叉乘编码：speeds × crops × loops。

    masters: { '1.0': path, '0.75': path, ... }（各倍速母带）。
    返回每个成片的记录列表。
    """
    from .tasks import JobCancelled
    loops = loops or ["palindrome"]
    results = []
    total = len(speeds) * len(crops) * len(loops)
    done = 0
    for sp in speeds:
        master = masters.get(speed_key(sp))
        if not master:
            continue
        for crop in crops:
            for lp in loops:
                if stop is not None and stop():
                    raise JobCancelled("用户取消")
                suffix = "" if lp == "palindrome" else f"_{lp}"
                fname = f"{name_prefix}_{sp:g}x_{crop.label}{suffix}.mp4"
                out = os.path.join(outdir, fname)
                rec = encode(master, crop, out, fps, loop=lp, crf=crf, preset=preset,
                             actual_range=actual_range, dwell=dwell)
                rec.update({"speed": sp, "crop_label": crop.label, "file": fname})
                results.append(rec)
                done += 1
                if progress:
                    progress(done, total, rec)
    return results
