"""ffmpeg / ffprobe 基础工具封装。"""
from __future__ import annotations

import json
import re
import subprocess
import shutil
from typing import Optional


class FFError(RuntimeError):
    """ffmpeg/ffprobe 调用失败。"""


def ffmpeg_bin() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise FFError("未找到 ffmpeg，请先安装（brew install ffmpeg）")
    return p


def ffprobe_bin() -> str:
    p = shutil.which("ffprobe")
    if not p:
        raise FFError("未找到 ffprobe")
    return p


def run(cmd: list[str], timeout: Optional[float] = None, capture: bool = True) -> subprocess.CompletedProcess:
    """运行命令；失败时抛出 FFError（含 stderr 摘要）。"""
    try:
        r = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise FFError(f"命令超时({timeout}s): {' '.join(cmd[:6])}…") from e
    if r.returncode != 0:
        tail = (r.stderr or "")[-1500:]
        raise FFError(f"命令失败 rc={r.returncode}: {' '.join(cmd[:6])}…\n{tail}")
    return r


def probe_json(path: str) -> dict:
    """ffprobe 全量 JSON（流 + 格式）。"""
    r = run([ffprobe_bin(), "-v", "error", "-show_streams", "-show_format", "-of", "json", path], timeout=120)
    return json.loads(r.stdout)


def video_stream(path: str) -> dict:
    d = probe_json(path)
    for s in d.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    raise FFError(f"无视频流: {path}")


def stream_info(path: str) -> dict:
    """常用视频信息：宽高/帧率/时长/色彩标签/范围。"""
    s = video_stream(path)
    num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    d = probe_json(path).get("format", {})
    return {
        "codec": s.get("codec_name"),
        "profile": s.get("profile"),
        "width": s.get("width"),
        "height": s.get("height"),
        "pix_fmt": s.get("pix_fmt"),
        "fps": round(fps, 6),
        "duration": float(d.get("duration") or s.get("duration") or 0.0),
        "color_range": s.get("color_range"),          # 'tv'(limited) / 'pc'(full)
        "color_space": s.get("color_space"),
        "color_transfer": s.get("color_transfer"),
        "color_primaries": s.get("color_primaries"),
        "bits_per_raw_sample": s.get("bits_per_raw_sample"),
        "nb_audio": sum(1 for x in probe_json(path).get("streams", []) if x.get("codec_type") == "audio"),
        "nb_subtitle": sum(1 for x in probe_json(path).get("streams", []) if x.get("codec_type") == "subtitle"),
    }


def frame_count(path: str) -> int:
    """精确帧数（解码计数，慢但准）。"""
    r = run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path], timeout=600)
    return int(r.stdout.strip())


def extract_frame(src: str, out_png: str, n: Optional[int] = None, t: Optional[float] = None,
                  crop: Optional[str] = None, scale: Optional[str] = None, timeout: float = 300) -> None:
    """抽取单帧为 PNG。n=帧序号（从头解码，慢）；t=秒（快速定位）。crop 形如 'w:h:x:y'。"""
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error"]
    if t is not None:
        cmd += ["-ss", f"{t:.6f}"]
    cmd += ["-i", src]
    vf = []
    if n is not None:
        vf.append(f"select='eq(n,{n})'")
    if crop:
        vf.append(f"crop={crop}")
    if scale:
        vf.append(f"scale={scale}")
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-frames:v", "1", out_png]
    run(cmd, timeout=timeout)


def psnr(a: str, b: str) -> str:
    """两图/两视频的 PSNR average（完全相同 → 'inf'）。返回字符串。"""
    r = run([ffmpeg_bin(), "-hide_banner", "-i", a, "-i", b, "-lavfi", "psnr", "-f", "null", "-"], timeout=300)
    m = re.search(r"average:\s*([0-9.inf]+)", r.stderr or "")
    return m.group(1) if m else "?"
