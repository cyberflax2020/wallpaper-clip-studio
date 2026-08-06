"""平滑变速（慢放）：运动补偿插帧，严禁帧混合（blend）以免拖影/掉清晰度。

默认引擎是 ffmpeg minterpolate 的 MCI 模式（mi_mode=mci + mc_mode=aobmc + 双向运动估计），
它基于运动矢量合成新帧，而非相邻帧加权平均，因此不会产生传统"帧混合"的拖影。
若后续接入 RIFE 等神经插帧（更优），在 INTERPOLATORS 里注册即可。
"""
from __future__ import annotations

from . import ffutil


def slow_master(master: str, speed: float, out: str, fps: float = 23.976,
                method: str = "mci", timeout: float = 3600) -> int:
    """把母带放慢到 speed 倍（speed<1），运动补偿插帧保持 fps，输出 ffv1 无损。

    返回输出帧数。speed=1 时直接复制。
    """
    if abs(speed - 1.0) < 1e-6:
        ffutil.run([ffutil.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", master, "-c", "copy", out], timeout=timeout)
        return ffutil.frame_count(out)

    if method == "mci":
        vf = (f"setpts=PTS/{speed},"
              f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:me=epzs")
    elif method == "dup":
        vf = f"setpts=PTS/{speed},minterpolate=fps={fps}:mi_mode=dup"
    else:
        raise ValueError(f"未知插帧方式: {method}（blend 已被禁用以避免拖影）")

    ffutil.run([ffutil.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                "-i", master, "-vf", vf,
                "-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1",
                "-g", "1", "-slices", "8", "-pix_fmt", "yuv420p",
                "-fps_mode", "passthrough", out], timeout=timeout)
    n = ffutil.frame_count(out)
    # 校验：放慢后帧数应约为 N/speed
    return n


def verify_slowmo(master_frames: int, speed: float, out_frames: int, tol: float = 0.03) -> bool:
    """校验慢放帧数是否符合 N/speed（±tol）。"""
    expect = master_frames / speed
    return abs(out_frames - expect) / expect <= tol
