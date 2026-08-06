"""流水线编排：单个片段从源到壁纸成片的完整链路。

probe → locate → crops → master → slow-mo → encode matrix → verify → manifest
所有中间产物放 workdir，成片放 outdir；跑完可按需清理中间产物省空间。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict

from . import ffutil, probe as probemod, shots as shotsmod, interpolate as interpmod
from . import encode as encodemod, quality as qualitymod, zoom as zoommod
from .encode import speed_key
from .crop import (compute_crops, dedupe_centers, compute_free_crop as cropmod_free,
                   fuse_crops as cropmod_fuse)
from .devices import resolve as resolve_device

SPEEDS = [1.0, 0.75, 0.5, 0.25]


def build_crops(frame_w: int, frame_h: int, bars, device,
                crop_labels: dict[str, float] | None = None,
                fusion: bool = False, free_frac: float | None = None,
                dedupe_px: float = 24.0):
    """按策略组装候选裁剪框：center + 传入中心 + 可选解锁尺寸 + 可选融合。

    dedupe_px 小于该像素差的取景才会被合并；设很小可保留全部策略（含与 center 接近的 energy）。
    """
    centers = {"center": 0.5}
    if crop_labels:
        centers.update(crop_labels)
    full = dict(centers)  # 去重前的完整策略集（free 变体基于它，避免因合并而丢失）
    centers = dedupe_centers(centers, min_px=dedupe_px, width=bars[2])
    crops = compute_crops(frame_w, frame_h, bars, device.ratio,
                          centers=list(centers.values()), labels=list(centers.keys()))
    if free_frac:
        for label, c in full.items():
            if label == "center":
                continue
            fc = cropmod_free(frame_w, frame_h, bars, device.ratio, center=c, height_frac=free_frac)
            fc.label = f"{label}_free"
            crops.append(fc)
    if fusion:
        crops += cropmod_fuse(crops)
    return crops


def make_master(src: str, start: float, end: float, out: str, timeout: float = 1800) -> str:
    """无损剪切 [start,end) → ffv1 母带。-ss 在 -i 前，重编码时帧精确。"""
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    ffutil.run([ffutil.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start:.6f}", "-i", src, "-t", f"{(end - start):.6f}",
                "-map", "0:v:0", "-an", "-sn",
                "-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1",
                "-g", "1", "-slices", "8", "-pix_fmt", "yuv420p",
                "-fps_mode", "passthrough", out], timeout=timeout)
    return out


def process_segment(src: str, name: str, target_start: float, target_end: float,
                    device_spec: str = "mbp16-m4",
                    start_mode: str = "scene",
                    crop_labels: dict[str, float] | None = None,
                    include_energy: bool = True,
                    fusion: bool = True,
                    free_frac: float | None = 0.8,
                    explicit_crops: list[dict] | None = None,
                    speeds: list[float] | None = None,
                    loops: list[str] | None = None,
                    workdir: str = "_work", outdir: str = "deliverables",
                    keep_intermediate: bool = False,
                    crf: int = 12, preset: str = "slow", dwell: int = 3,
                    progress=None, stop=None) -> dict:
    """处理一个片段 → 壁纸成片矩阵。返回 manifest 字典。"""
    def _say(msg: str):
        if progress:
            progress(msg)

    speeds = speeds or SPEEDS
    loops = loops or ["palindrome_dwell"]
    device = resolve_device(device_spec)
    _say(f"[{name}] 探测源…")
    mi = probemod.probe(src)
    actual_range = probemod.detect_actual_range(src, [target_start + 0.5, (target_start + target_end) / 2])

    _say(f"[{name}] 定位镜头（{start_mode}）…")
    shot = shotsmod.locate(src, target_start, target_end, start_mode=start_mode)
    # 裁掉首尾黑/白屏帧，循环接缝落在可见内容上，避免黑闪/白闪
    ts, te = qualitymod.trim_to_content(src, shot.start, shot.end)
    if te - ts >= 1.0:
        shot.start, shot.end = ts, te

    _say(f"[{name}] 探测黑边…")
    bars = probemod.cropdetect(src, shot.start + min(1.0, shot.duration / 3))

    if explicit_crops:
        from .crop import CropWindow
        crops = [CropWindow(**c) for c in explicit_crops]
    else:
        labels = dict(crop_labels or {})
        if include_energy:
            labels.setdefault("energy", zoommod.energy_center(src, shot.start, shot.end))
        crops = build_crops(mi.width, mi.height, bars, device, labels, fusion, free_frac)

    _say(f"[{name}] 无损母带 {shot.start:.3f}→{shot.end:.3f}…")
    os.makedirs(workdir, exist_ok=True)
    m0 = make_master(src, shot.start, shot.end, os.path.join(workdir, f"{name}_M0.mkv"))
    n_master = ffutil.frame_count(m0)

    _say(f"[{name}] 慢放插帧（{', '.join(str(s) for s in speeds if s != 1.0)}）…")
    from .tasks import JobCancelled
    masters = {speed_key(1.0): m0}
    for sp in speeds:
        if sp == 1.0:
            continue
        if stop is not None and stop():
            raise JobCancelled("用户取消")
        spath = os.path.join(workdir, f"{name}_S{int(sp * 100):03d}.mkv")
        interpmod.slow_master(m0, sp, spath, fps=shot.fps)
        masters[speed_key(sp)] = spath

    _say(f"[{name}] 编码 {len(speeds)}倍速 × {len(crops)}取景 × {loops}…")
    os.makedirs(outdir, exist_ok=True)
    recs = encodemod.encode_matrix(masters, crops, outdir, name, shot.fps, speeds,
                                   loops=loops, crf=crf, preset=preset,
                                   actual_range=actual_range, dwell=dwell, progress=None,
                                   stop=stop)

    _say(f"[{name}] 校验接缝与元数据…")
    for rec in recs:
        try:
            rec["seam"] = qualitymod.seam_metrics(rec["out"])
        except Exception as e:  # noqa: BLE001
            rec["seam"] = {"error": str(e)}

    manifest = {
        "name": name,
        "source": src,
        "device": device.key,
        "target_ratio": list(device.ratio),
        "shot": asdict(shot),
        "bars": list(bars),
        "actual_range": actual_range,
        "tagged_range": mi.tagged_range,
        "crops": [asdict(c) for c in crops],
        "n_master_frames": n_master,
        "deliverables": recs,
    }
    with open(os.path.join(outdir, f"{name}_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    if not keep_intermediate:
        _cleanup_intermediate(workdir, name, masters)

    _say(f"[{name}] 完成，共 {len(recs)} 个成片。")
    return manifest


def _cleanup_intermediate(workdir: str, name: str, masters: dict[str, str]) -> None:
    """删除中间母带，省空间（成片已出）。"""
    for path in masters.values():
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
