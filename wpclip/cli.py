"""命令行入口：python3 -m wpclip.cli <子命令>。

子命令：
  probe     探测源规格（含真实色彩范围）
  locate    镜头定位（scene/audio_onset/fade_clean）
  crops     计算裁剪窗口
  process   完整处理一个片段 → 壁纸成片矩阵
  verify    校验成片（接缝/元数据）
"""
from __future__ import annotations

import argparse
import json
import sys

from . import ffutil, probe as probemod, shots as shotsmod, quality as qualitymod
from .crop import compute_crops
from .devices import resolve as resolve_device, DEVICES
from .pipeline import process_segment


def cmd_probe(a: argparse.Namespace) -> int:
    mi = probemod.probe(a.src)
    actual = probemod.detect_actual_range(a.src) if a.range else ""
    d = mi.__dict__.copy()
    if actual:
        d["actual_range"] = actual
    print(json.dumps(d, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_locate(a: argparse.Namespace) -> int:
    shot = shotsmod.locate(a.src, a.start, a.end, start_mode=a.mode)
    print(json.dumps(shot.__dict__, ensure_ascii=False, indent=2))
    return 0


def cmd_crops(a: argparse.Namespace) -> int:
    device = resolve_device(a.device)
    bars = tuple(a.bars) if a.bars else None
    centers = [float(x) for x in a.centers.split(",")]
    labels = a.labels.split(",") if a.labels else None
    mi = ffutil.stream_info(a.src) if a.src else {"width": a.width, "height": a.height}
    crops = compute_crops(mi["width"], mi["height"], bars, device.ratio, centers, labels)
    print(json.dumps([c.__dict__ for c in crops], ensure_ascii=False, indent=2))
    return 0


def cmd_process(a: argparse.Namespace) -> int:
    crop_labels = {}
    if a.crop_centers:
        for item in a.crop_centers.split(","):
            label, _, c = item.partition("=")
            crop_labels[label.strip()] = float(c)
    speeds = [float(x) for x in a.speeds.split(",")] if a.speeds else None
    loops = a.loops.split(",") if a.loops else None

    def progress(msg: str):
        print(msg, flush=True)

    manifest = process_segment(
        a.src, a.name, a.start, a.end,
        device_spec=a.device, start_mode=a.mode,
        crop_labels=crop_labels or None,
        include_energy=not a.no_energy,
        fusion=not a.no_fusion,
        free_frac=None if a.no_free else a.free_frac,
        speeds=speeds, loops=loops,
        workdir=a.workdir, outdir=a.outdir,
        keep_intermediate=a.keep, crf=a.crf, preset=a.preset, dwell=a.dwell,
        progress=progress,
    )
    print(f"完成：{len(manifest['deliverables'])} 个成片 → {a.outdir}")
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    for f in a.files:
        m = qualitymod.seam_metrics(f)
        info = ffutil.stream_info(f)
        print(f"\n== {f}")
        print(json.dumps({"seam": m,
                          "codec": info["codec"], "profile": info["profile"],
                          "size": f'{info["width"]}x{info["height"]}',
                          "fps": info["fps"], "color_range": info["color_range"],
                          "audio_streams": info["nb_audio"], "subtitle_streams": info["nb_subtitle"]},
                         ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wpclip", description="壁纸剪裁引擎")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("probe", help="探测源规格")
    sp.add_argument("src")
    sp.add_argument("--range", action="store_true", help="额外做直方图判定真实色彩范围")
    sp.set_defaults(fn=cmd_probe)

    sl = sub.add_parser("locate", help="镜头定位")
    sl.add_argument("src")
    sl.add_argument("--start", type=float, required=True)
    sl.add_argument("--end", type=float, required=True)
    sl.add_argument("--mode", default="scene", choices=["scene", "audio_onset", "fade_clean", "exact"])
    sl.set_defaults(fn=cmd_locate)

    sc = sub.add_parser("crops", help="计算裁剪窗口")
    sc.add_argument("--src", help="源文件（自动读宽高）")
    sc.add_argument("--width", type=int, default=0)
    sc.add_argument("--height", type=int, default=0)
    sc.add_argument("--bars", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    sc.add_argument("--device", default="mbp16-m4", help=f"设备预设或 宽x高。可选: {', '.join(DEVICES)}")
    sc.add_argument("--centers", default="0.5", help="取景中心，逗号分隔")
    sc.add_argument("--labels", help="取景标签，逗号分隔")
    sc.set_defaults(fn=cmd_crops)

    spr = sub.add_parser("process", help="完整处理一个片段")
    spr.add_argument("src")
    spr.add_argument("--name", required=True)
    spr.add_argument("--start", type=float, required=True)
    spr.add_argument("--end", type=float, required=True)
    spr.add_argument("--device", default="mbp16-m4")
    spr.add_argument("--mode", default="scene", choices=["scene", "audio_onset", "fade_clean", "exact"])
    spr.add_argument("--crop-centers", help="额外取景 '标签=中心,...'，如 semantic=0.48,energy=0.36")
    spr.add_argument("--speeds", default=None, help="逗号分隔，默认 1.0,0.75,0.5,0.25")
    spr.add_argument("--loops", default=None, help="逗号分隔，默认 palindrome")
    spr.add_argument("--workdir", default="_work")
    spr.add_argument("--outdir", default="deliverables")
    spr.add_argument("--no-energy", action="store_true", help="关闭默认的边缘能量取景")
    spr.add_argument("--no-fusion", action="store_true", help="关闭默认的融合取景")
    spr.add_argument("--no-free", action="store_true", help="关闭默认的解锁尺寸取景")
    spr.add_argument("--free-frac", type=float, default=0.8, help="解锁尺寸高度比例（默认0.8）")
    spr.add_argument("--keep", action="store_true", help="保留中间母带")
    spr.add_argument("--crf", type=int, default=12)
    spr.add_argument("--preset", default="slow")
    spr.add_argument("--dwell", type=int, default=3, help="回文折返停顿帧数（缓解弹跳感）")
    spr.set_defaults(fn=cmd_process)

    sv = sub.add_parser("verify", help="校验成片")
    sv.add_argument("files", nargs="+")
    sv.set_defaults(fn=cmd_verify)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
