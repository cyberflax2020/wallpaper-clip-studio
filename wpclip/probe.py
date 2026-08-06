"""媒体探测：规格、真实色彩范围（直方图判定）、黑边、HDR 元数据。

关键：容器/流标签（color_range）可能说谎，必须用像素直方图判定真实范围，
否则编码后播放器会按错误的范围展开，导致发灰或黑位被压。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

from . import ffutil


@dataclass
class MediaInfo:
    path: str
    codec: str = ""
    profile: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    pix_fmt: str = ""
    tagged_range: str = ""        # 流标签声称的范围 tv/pc
    actual_range: str = ""        # 直方图判定的真实范围 tv/pc
    color_space: str = ""
    color_transfer: str = ""
    color_primaries: str = ""
    bits_per_raw_sample: str = ""
    has_hdr: bool = False
    hdr_master_display: str = ""
    hdr_max_cll: str = ""
    nb_audio: int = 0
    nb_subtitle: int = 0
    subtitles: list = field(default_factory=list)   # [{index, codec, lang}]

    @property
    def is_hdr(self) -> bool:
        return self.has_hdr or self.color_transfer in ("smpte2084", "arib-std-b67")


def probe(path: str) -> MediaInfo:
    """探测媒体规格 + HDR 元数据（不做直方图，快）。"""
    d = ffutil.probe_json(path)
    vs = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    fmt = d.get("format", {})
    num, _, den = (vs.get("avg_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    subs = [{"index": s.get("index"), "codec": s.get("codec_name"),
             "lang": (s.get("tags") or {}).get("language", "")}
            for s in d.get("streams", []) if s.get("codec_type") == "subtitle"]

    mi = MediaInfo(
        path=path,
        codec=vs.get("codec_name", ""), profile=vs.get("profile", ""),
        width=vs.get("width", 0), height=vs.get("height", 0),
        fps=round(fps, 6), duration=float(fmt.get("duration") or 0.0),
        pix_fmt=vs.get("pix_fmt", ""), tagged_range=vs.get("color_range", ""),
        color_space=vs.get("color_space", ""), color_transfer=vs.get("color_transfer", ""),
        color_primaries=vs.get("color_primaries", ""),
        bits_per_raw_sample=vs.get("bits_per_raw_sample", ""),
        nb_audio=sum(1 for s in d.get("streams", []) if s.get("codec_type") == "audio"),
        nb_subtitle=len(subs), subtitles=subs,
    )
    mi.has_hdr, mi.hdr_master_display, mi.hdr_max_cll = _hdr_side_data(path)
    return mi


def _hdr_side_data(path: str) -> tuple[bool, str, str]:
    """读首几帧 side_data：mastering display / CLL。返回 (has_hdr, master_display, max_cll)。"""
    try:
        r = ffutil.run([ffutil.ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
                        "-read_intervals", "%+#3", "-show_entries", "frame=side_data_list",
                        "-of", "json", path], timeout=120)
        frames = json.loads(r.stdout).get("frames", [])
    except ffutil.FFError:
        return False, "", ""
    master, cll = "", ""
    for fr in frames:
        for sd in (fr.get("side_data_list") or []):
            t = sd.get("side_data_type", "")
            if "Mastering display" in t:
                master = json.dumps({k: v for k, v in sd.items() if k != "side_data_type"})
            if "Content light level" in t:
                cll = f"{sd.get('max_content','')},{sd.get('max_average','')}"
    return bool(master or cll), master, cll


def _luma_histogram(path: str, t: float, bins: int = 256) -> list[int]:
    """抽取 t 秒处灰度帧并统计亮度直方图（numpy）。"""
    import numpy as np
    cmd = [ffutil.ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
           "-ss", f"{t:.3f}", "-i", path, "-map", "0:v:0",
           "-vf", "select='eq(n,0)',format=gray", "-frames:v", "1",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    raw = r.stdout
    if not raw:
        return [0] * bins
    arr = np.frombuffer(raw, dtype=np.uint8)
    hist, _ = np.histogram(arr, bins=bins, range=(0, 256))
    return hist.tolist()


def detect_actual_range(path: str, sample_times: list[float] | None = None) -> str:
    """用像素直方图判定真实色彩范围。

    规则：任一采样帧出现明显 <16 或 >235 的像素 → 'pc'(full)；
    否则（能量集中在 16..235）→ 'tv'(limited)。
    黑位附近 0..15 有连续分布（而非恰好卡在 16）是 full 的强信号。
    """
    if not sample_times:
        d = ffutil.probe_json(path).get("format", {})
        dur = float(d.get("duration") or 0.0)
        sample_times = [dur * f for f in (0.2, 0.5, 0.8)] if dur else [0.0]
    for t in sample_times:
        hist = _luma_histogram(path, t)
        lo = sum(hist[:16])
        hi = sum(hist[236:])
        total = sum(hist) or 1
        if (lo + hi) / total > 0.005:      # >0.5% 像素越出 16..235 → full
            return "pc"
    return "tv"


def _cropdetect_once(path: str, t: float, duration: float = 1.5) -> tuple[int, int, int, int] | None:
    cmd = [ffutil.ffmpeg_bin(), "-hide_banner", "-ss", f"{t:.3f}", "-i", path,
           "-t", f"{duration:.2f}", "-map", "0:v:0",
           "-vf", "cropdetect=24:2:0", "-f", "null", "-"]
    try:
        r = ffutil.run(cmd, timeout=180)
    except ffutil.FFError:
        return None
    import re
    rects = []
    for line in (r.stderr or "").splitlines():
        m = re.search(r"crop=(\d+):(\d+):(\d+):(\d+)", line)
        if m:
            w, h, x, y = map(int, m.groups())
            rects.append((x, y, w, h))
    if not rects:
        return None
    # 段内取"最大面积"那帧（暗帧会低估，亮的帧更接近真实内容区）
    return max(rects, key=lambda r: r[2] * r[3])


def cropdetect(path: str, t: float | None = None,
               sample_fracs: tuple[float, ...] = (0.08, 0.3, 0.5, 0.7, 0.92)) -> tuple[int, int, int, int]:
    """探测整片有效画面区（去黑边），返回 (x, y, w, h)。

    黑边是全片属性；暗场景会让单点 cropdetect 误判。故在全片多点采样，
    取并集（最大外接框），只要有一处采样到亮场景即可得到真实内容区。
    传入 t 时额外在该点加采一次（仍与全片采样合并）。
    """
    mi = probe(path)
    dur = mi.duration or 0.0
    times = [dur * f for f in sample_fracs if dur > 0]
    if t is not None:
        times.append(t)
    rects = [r for r in (_cropdetect_once(path, tt) for tt in times) if r]
    if not rects:
        return (0, 0, mi.width, mi.height)
    x = min(r[0] for r in rects)
    y = min(r[1] for r in rects)
    x2 = max(r[0] + r[2] for r in rects)
    y2 = max(r[1] + r[3] for r in rects)
    # 偶数化、夹取到帧内
    x -= x % 2
    y -= y % 2
    w = min(x2, mi.width) - x
    h = min(y2, mi.height) - y
    w -= w % 2
    h -= h % 2
    return (x, y, w, h)
