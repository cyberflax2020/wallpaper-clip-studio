"""wpclip Web 后端：把引擎包成人机友好的本地工作台（FastAPI + 单页前端 + 双语）。

本地 127.0.0.1 服务 + 单页模板 + static 资源 + 任务轮询，壁纸剪裁所需的最小子集。
中文为源语言，英文由 static/i18n.js 运行期翻译。
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from wpclip import probe as probemod, ffutil
from wpclip.crop import compute_crops
from wpclip.devices import DEVICES, resolve as resolve_device
from wpclip.pipeline import process_segment, SPEEDS, build_crops
from wpclip import zoom as zoommod
from wpclip.tasks import JobManager, JobCancelled, InvalidTransition

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_DIR = os.path.join(ROOT, "sources")
PROJECTS_DIR = os.path.join(ROOT, "projects")
PREVIEW_DIR = os.path.join(ROOT, "output", "preview")

def _purge_previews(keep: int = 60):
    """预览是临时缓存：超过 keep 个时删除最旧的，避免堆积。"""
    try:
        files = [os.path.join(PREVIEW_DIR, f) for f in os.listdir(PREVIEW_DIR)]
        files.sort(key=os.path.getmtime)
        for f in files[:-keep] if len(files) > keep else []:
            os.remove(f)
    except OSError:
        pass


app = FastAPI(title="wpclip", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "static")), name="static")

MANAGER = JobManager()


def _scan_sources() -> list[dict]:
    out = []
    if os.path.isdir(SOURCES_DIR):
        for d in sorted(os.listdir(SOURCES_DIR)):
            full = os.path.join(SOURCES_DIR, d)
            if os.path.isdir(full):
                for f in os.listdir(full):
                    if f.lower().endswith((".mkv", ".mp4", ".mov", ".m4v", ".ts")):
                        out.append({"label": f"{d}/{f}", "path": os.path.join(full, f),
                                    "size": os.path.getsize(os.path.join(full, f))})
            elif d.lower().endswith((".mkv", ".mp4", ".mov", ".m4v", ".ts")):
                out.append({"label": d, "path": full, "size": os.path.getsize(full)})
    return out


class ProbeReq(BaseModel):
    src: str


class PreviewReq(BaseModel):
    src: str
    start: float
    end: float
    device: str = "mbp16-m4"
    centers: list[float] = [0.5]
    fusion: bool = False
    free_frac: float | None = 0.8
    include_energy: bool = True


class ZoomReq(BaseModel):
    src: str
    start: float
    end: float


class JobReq(BaseModel):
    src: str
    name: str
    start: float
    end: float
    device: str = "mbp16-m4"
    mode: str = "scene"
    crop_centers: dict[str, float] | None = None
    crops: list[dict] | None = None   # 预览勾选的明确裁剪框（优先于 crop_centers）
    speeds: list[float] | None = None
    loops: list[str] | None = None
    crf: int = 12
    preset: str = "slow"


@app.get("/", response_class=HTMLResponse)
def index():
    tpl = os.path.join(ROOT, "templates", "index.html")
    with open(tpl, encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"ok": True, "jobs": len(MANAGER.list())}


@app.get("/api/state")
def state():
    return {
        "sources": _scan_sources(),
        "devices": [{"key": k, "name": v.name, "name_en": v.name_en,
                     "ratio": list(v.ratio), "native": list(v.native)}
                    for k, v in DEVICES.items()],
        "speeds": SPEEDS,
    }


@app.post("/api/probe")
def api_probe(req: ProbeReq):
    if not os.path.exists(req.src):
        raise HTTPException(404, "文件不存在")
    mi = probemod.probe(req.src)
    actual = probemod.detect_actual_range(req.src)
    bars = probemod.cropdetect(req.src)
    return {"info": mi.__dict__, "actual_range": actual, "bars": list(bars)}


@app.post("/api/preview")
def api_preview(req: PreviewReq):
    """枚举全部候选取景框（center/语义/energy/解锁尺寸/融合）并出缩略图，供勾选。"""
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    device = resolve_device(req.device)
    mi = ffutil.stream_info(req.src)
    bars = probemod.cropdetect(req.src)

    labels = {"center": 0.5}
    for i, c in enumerate(req.centers):
        labels.setdefault(f"custom{i}", c)
    if req.include_energy:
        labels["energy"] = zoommod.energy_center(req.src, req.start, req.end)
    crops = build_crops(mi["width"], mi["height"], bars, device,
                        crop_labels=labels, fusion=req.fusion, free_frac=req.free_frac)

    _purge_previews()
    mid = (req.start + req.end) / 2
    results = []
    for cw in crops:
        out = os.path.join(PREVIEW_DIR, f"pv_{uuid.uuid4().hex[:8]}_{cw.label}.jpg")
        ffutil.run([ffutil.ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{mid:.3f}", "-i", req.src, "-frames:v", "1",
                    "-vf", f"crop={cw.ffmpeg},scale=640:-2", out], timeout=120)
        results.append({"label": cw.label, "crop": cw.__dict__,
                        "url": f"/files/{os.path.relpath(out, ROOT)}"})
    return {"previews": results, "bars": list(bars)}


@app.post("/api/zoom")
def api_zoom(req: ZoomReq):
    """对拉近/推远镜头：估计变焦比并推荐最稳子窗（用于循环不跳、少视野缩放）。"""
    z = zoommod.estimate_zoom(req.src, req.start, req.end)
    stable = zoommod.pick_stable_window(req.src, req.start, req.end)
    return {"zoom": z, "stable_window": list(stable)}


@app.post("/api/jobs")
def api_jobs(req: JobReq):
    workdir = os.path.join(PROJECTS_DIR, req.name, "_work")
    outdir = os.path.join(PROJECTS_DIR, req.name, "deliverables")
    job = MANAGER.create(req.name)

    def runner():
        try:
            MANAGER.start(job)
            manifest = process_segment(
                req.src, req.name, req.start, req.end,
                device_spec=req.device, start_mode=req.mode,
                crop_labels=req.crop_centers, explicit_crops=req.crops,
                speeds=req.speeds, loops=req.loops,
                workdir=workdir, outdir=outdir, keep_intermediate=False,
                crf=req.crf, preset=req.preset,
                progress=lambda m: job.log.append(m),
                stop=job.stop_requested)
            MANAGER.finish(job, {"deliverables": len(manifest["deliverables"]),
                                 "outdir": outdir})
        except JobCancelled:
            job.status = "cancelled"
            job.error = "已取消"
        except Exception as e:  # noqa: BLE001
            try:
                MANAGER.fail(job, str(e))
            except InvalidTransition:
                job.status = "failed"; job.error = str(e)

    threading.Thread(target=runner, daemon=True).start()
    return {"job_id": job.id}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": MANAGER.list()}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = MANAGER.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    try:
        MANAGER.cancel(job)
    except InvalidTransition as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "status": job.status}


class SaveReq(BaseModel):
    file: str          # 相对 ROOT 的成片路径
    dest_dir: str | None = None


@app.post("/api/save")
def save_file(req: SaveReq):
    """把成片复制到用户目录（默认 ~/Movies/Wallpapers），实现"保存"。"""
    src = os.path.abspath(os.path.join(ROOT, req.file))
    if not src.startswith(os.path.abspath(ROOT)):
        raise HTTPException(403, "越界")
    if not os.path.exists(src):
        raise HTTPException(404, "文件不存在")
    dest_dir = os.path.expanduser(req.dest_dir or "~/Movies/Wallpapers")
    os.makedirs(dest_dir, exist_ok=True)
    dst = os.path.join(dest_dir, os.path.basename(src))
    import shutil
    shutil.copy2(src, dst)
    return {"saved": dst}


@app.get("/api/results")
def results():
    out = []
    if os.path.isdir(PROJECTS_DIR):
        for proj in sorted(os.listdir(PROJECTS_DIR)):
            d = os.path.join(PROJECTS_DIR, proj, "deliverables")
            if os.path.isdir(d):
                for f in sorted(os.listdir(d)):
                    if f.endswith(".mp4"):
                        out.append({"project": proj, "file": f,
                                    "url": f"/files/{os.path.relpath(os.path.join(d, f), ROOT)}",
                                    "size": os.path.getsize(os.path.join(d, f))})
    return {"results": out}


@app.get("/files/{path:path}")
def serve_file(path: str):
    full = os.path.join(ROOT, path)
    if not os.path.abspath(full).startswith(os.path.abspath(ROOT)):
        raise HTTPException(403, "越界")
    if not os.path.exists(full):
        raise HTTPException(404, "不存在")
    return FileResponse(full)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8799)
