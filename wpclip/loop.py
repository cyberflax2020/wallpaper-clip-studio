"""无缝循环构造：回文循环（默认，数学上首尾完全一致）与交叉淡化循环（奖励变体）。

回文循环 = 正放 + 倒放（去掉倒放首帧避免重复停留）。末帧==首帧，循环点像素级一致，
且主体位置天然回到原位（不会"主体位置跳变"）。这是最稳的壁纸循环方式。
"""
from __future__ import annotations

from .crop import CropWindow


def palindrome_filter(crop: CropWindow) -> tuple[str, int]:
    """返回 (filter_complex, 期望输出帧数公式用的参数)。

    filter_complex 里 [0:v] 先 crop，再 split 成 forward / reversed 两支，concat。
    期望帧数 = 2*N - 1（N 为母带帧数）。
    """
    fc = (
        f"[0:v]crop={crop.ffmpeg},split[f][r0];"
        f"[r0]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];"
        f"[f][r]concat=n=2:v=1:a=0[v]"
    )
    return fc, 2  # 2*N-1


def palindrome_dwell_filter(crop: CropWindow, fps: float, dwell_frames: int = 3) -> str:
    """带回旋停顿的回文循环：在折返点用 tpad 克隆末帧 dwell_frames 次。

    回文的"弹跳感"源于折返处速度瞬时反向；插入短暂停顿让方向变化读作
    自然停顿而非跳变。期望帧数 = (N) + dwell + (N-1) = 2N-1+dwell。
    """
    dur = dwell_frames / fps
    return (
        f"[0:v]crop={crop.ffmpeg},split[f][r0];"
        f"[r0]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];"
        f"[f]tpad=stop_mode=clone:stop_duration={dur:.6f}[fd];"
        f"[fd][r]concat=n=2:v=1:a=0[v]"
    )


def crossfade_filter(crop: CropWindow, n_frames: int, k: int = 14) -> tuple[str, int]:
    """交叉淡化循环变体：把尾部 k 帧与头部 k 帧 xfade 融合。

    输出首帧 == 母带第 (N-k) 帧，循环回落点是连续帧，数学上无缝。
    期望帧数 = N - k。k 需 < N/2。
    """
    if k >= n_frames // 2:
        k = max(1, n_frames // 2 - 1)
    nk = n_frames - k
    # xfade 时长按 k 帧换算（调用方给 fps 更准，这里用帧数表达，duration 由 encode 传入）
    fc = (
        f"[0:v]crop={crop.ffmpeg},split=3[t0][h0][m0];"
        f"[t0]trim=start_frame={nk}:end_frame={n_frames},setpts=PTS-STARTPTS[tail];"
        f"[h0]trim=start_frame=0:end_frame={k},setpts=PTS-STARTPTS[head];"
        f"[m0]trim=start_frame={k}:end_frame={nk},setpts=PTS-STARTPTS[mid];"
        f"[tail][head]xfade=transition=fade:duration=__KDUR__:offset=0[blend];"
        f"[blend][mid]concat=n=2:v=1:a=0[v]"
    )
    return fc, k


def expected_frames(loop: str, n_frames: int, k: int = 14, dwell: int = 0) -> int:
    if loop == "palindrome":
        return 2 * n_frames - 1
    if loop == "palindrome_dwell":
        return 2 * n_frames - 1 + dwell
    if loop == "crossfade":
        kk = k if k < n_frames // 2 else max(1, n_frames // 2 - 1)
        return n_frames - kk
    raise ValueError(f"未知循环方式: {loop}")
