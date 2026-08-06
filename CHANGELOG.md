# Changelog

格式：英前中后（English first, Chinese follows）。

## 0.1.0 — 2026-08-07

### English
- Initial release of the wallpaper clipping engine (`wpclip/`): probe, shot locating
  (scene / audio-onset / fade-clean / exact), ratio crop, motion-compensated slow-mo,
  palindromic & crossfade loops, HEVC(hvc1) visually-lossless encode, multi-dim seam QA.
- Device preset database + custom aspect ratios (`wpclip/devices.py`).
- Crop strategies: center / semantic / energy / fused-mean / free-size fixed-ratio.
- True color-range detection via luma histogram (full vs limited).
- Web workbench: FastAPI backend + bilingual (zh/en) single-page frontend (`run.sh`).
- Fine-grained tests (25 assertions) with synthetic-video loop/range/interpolation checks.
- Pre-release privacy check and release build (zip + sha256).

### 中文
- 首版发布：壁纸剪裁引擎（探测、镜头定位、按比例裁剪、运动补偿慢放、回文/交叉淡化
  循环、HEVC 视觉无损编码、多维接缝质检）。
- 设备预设库 + 自定义比例。
- 取景策略：居中 / 语义 / 能量 / 融合均值 / 解锁尺寸固定比例。
- 像素直方图判定真实色彩范围（full/limited）。
- Web 工作台（FastAPI + 中英双语单页前端）。
- 细粒度测试（25 项断言，合成视频）。
- Pre-release privacy check + release build scripts.
- 发布前隐私检查与发布构建脚本。
