# 流水线与配方 / Pipeline & Recipes

Five stages: **probe → locate → crop → master → slow-mo → encode → verify**.
五阶段：**探测 → 定位 → 裁剪 → 母带 → 慢放 → 编码 → 校验**。

## 1. probe / 探测 `wpclip/probe.py`

- Read stream specs: width/height / fps / bit depth / codec / subtitles / audio tracks.
  读流规格：宽高 / fps / 位深 / 编码 / 字幕 / 音轨。
- **True color range / 真实色彩范围**: container `color_range` tags can lie. `detect_actual_range()`
  samples multi-frame luma histograms — `<16` and `>235` pixel ratios determine full(pc)/limited(tv).
  If black levels 0..15 show a continuous distribution (not clamped at 16) → full.
  Encode with the **true** value to prevent player mis-expansion.
  容器标签可能说谎，用多帧灰度直方图统计 `<16` 与 `>235` 像素占比判定 full(pc)/limited(tv)。
  黑位 0..15 有连续分布（而非卡在 16）= full。编码时按**真实值**标注，避免播放器误展开。
- **Consensus bars / 黑边共识** `cropdetect()`: letterbox bars are a film-wide property; dark scenes
  cause false negatives, so sample many timestamps and take the **union**.
  黑边是全片属性，暗场景会误判，故全片多点采样取**并集**。

## 2. locate / 定位 `wpclip/shots.py`

- `scene`：`select=gt(scene,thr)` — find the shot boundary enclosing the target timestamp.
  找包围目标时刻的镜头边界。
- `audio_onset`：center channel `silencedetect` — grab the silence→sound inflection (dialogue onset).
  中心声道，取"静音→有声"拐点（台词出现处）。
- `fade_clean`：when the previous shot is a fade, grab the first clean frame after the fade plateau.
  前一镜头为渐变转场时，取亮度进入末段平台的第一个干净帧。
- `exact`：use the exact supplied boundaries.
  直接给定精确边界。

## 3. crop / 裁剪 `wpclip/crop.py`

- Principle / 原则：**crop only, never scale / 只裁不缩**。Keep full effective height, compute width
  from target ratio (even pixels), position horizontally by framing center.
  保留全部有效高度，按目标比例算宽（偶数），水平按取景中心。
- Strategies / 策略：`center` / `semantic` / `energy`; `fusion_centers` + `fuse_crops` for pairwise
  / full-average fusion (both **position and size** can be fused); `compute_free_crop` for free-size
  fixed-ratio framing (height_frac < 1).
  `fusion_centers`+`fuse_crops` 做两两/全体均值（位置与**尺寸**都可融合）；`compute_free_crop`
  解锁尺寸（height_frac<1）固定比例构图。
- Ratios come from `devices.py` presets or custom `WxH`, never from logical-resolution scaling.
  比例来自 `devices.py` 预设或自定义 `宽x高`，绝不按逻辑分辨率缩放。

### 取景策略说明 / Crop strategies explained

| Strategy / 策略 | Meaning / 含义 |
|---|---|
| `center` | Neutral center-framing at the horizontal midpoint. Most stable, no assumptions. 中性居中：取景中心固定在画面水平中点。最稳、无主观假设。 |
| `semantic` | Subject-aware: place the framing center on the visual subject (person/object/focal point). Can be set manually via the web preview or by human judgement. 语义主体：把取景中心放在"主体"所在的水平位置（人/物/焦点）。可由前端预览手动指定或人工判断。 |
| `energy` | **Edge-energy centroid / 边缘能量质心**: compute gradient magnitude (edge/texture energy) on grayscale frames, sum by column to get horizontal energy distribution, take the weighted centroid (0–1). This is where "detail/structure is most concentrated" — purely content-driven, reproducible, complementary to center's neutrality and semantic's subjectivity. See `wpclip/zoom.energy_center`. 对灰度帧求梯度幅值（边缘/纹理能量），按列求和得水平能量分布，取加权质心（0~1）。即"画面细节/结构最集中的水平位置"，纯内容驱动、可复现，与 center 的中性、semantic 的主观互补。实现见 `wpclip/zoom.energy_center`。 |
| `*_free` | Free-size: instead of using full height, take a tighter fixed-ratio composition window at `height_frac`, around the subject center (horizontal + vertical). 解锁尺寸：不取满全高，按 `height_frac` 取一个固定比例的更紧凑构图窗，围绕主体中心（水平+垂直）。 |
| `fused_*` | Fusion: average (x,y,w,h) of the above crop boxes pairwise or all together for a compromise position and size. 融合：把上述若干裁剪框的 (x,y,w,h) 做两两/全体平均，得到折中的位置与尺寸。 |

## 4. master / 无损母带 `wpclip/pipeline.make_master`

- `-ss` before `-i` for frame-accurate re-encode; `ffv1` lossless to disk; `-an -sn` strip audio & subtitles.
  `-ss` 在 `-i` 前、重编码帧精确；`ffv1` 无损落盘；`-an -sn` 去音轨字幕。

## 5. slow-mo / 插帧 `wpclip/interpolate.py`

- `setpts=PTS/speed` slowdown then `minterpolate=mi_mode=mci:mc_mode=aobmc:me_mode=bidir:me=epzs`
  motion-compensated interpolation. **Strictly forbid `mi_mode=blend`** (frame blending → ghosting, loss of sharpness).
  `setpts=PTS/speed` 放慢后运动补偿插帧。**严禁 `mi_mode=blend`**（帧混合→拖影、掉清晰度）。

## 6. encode / 编码 `wpclip/encode.py`

- Output resolution = crop window (source-native), no upscale nor downscale.
  输出分辨率 = 裁剪窗口（源原生），不放大不缩小。
- HEVC `libx265` + `-tag:v hvc1` (required for Apple hardware decode), CRF visually lossless.
  HEVC `libx265` + `-tag:v hvc1`（Apple 硬件解码必需），CRF 视觉无损。
- Bit-depth adaptive (10-bit source → main10). Colorspace/primaries/transfer passed through from source;
  range uses the detected true value.
  位深自适应（10bit 源→main10）。色彩空间/原色/传递从源透传，范围用真实判定值。
- Video-only stream: no audio, no subtitles.
  仅视频流：无音频、无字幕。

## 7. loop & seam / 循环与接缝 `wpclip/loop.py` + `wpclip/quality.py`

- **Palindrome loop / 回文循环** (default): forward + reverse (drop first reverse frame, which is the last
  forward frame). Last frame == first frame → pixel-identical loop point, subject position naturally
  returns → no position/subject jump. Expected frames: `2N-1`.
  正放+倒放（去掉倒放首帧）。末帧==首帧，循环点像素级一致，主体位置天然回位 → 无位置/主体跳变。期望帧数 `2N-1`。
- **Palindrome + dwell / 回文+折返停顿**: insert a brief hold at the reversal point to soften the
  direction/rhythm change.
  在折返点插入短暂停顿，缓解"方向/节奏跳变"的弹跳感。
- **Crossfade / 交叉淡化**: tail k frames `xfade` into head k frames, landing on continuous frames.
  Expected frames: `N-k`.
  尾部 k 帧与头部 k 帧 `xfade` 融合，回落点为连续帧。期望帧数 `N-k`。
- **Multi-dim seam QA / 接缝多维度量** `seam_metrics()`: head/tail PSNR (position), luma diff,
  chroma diff, black-frame check. Acceptance: PSNR ≥ 35 (visually seamless), no black frames,
  low luma/chroma diffs.
  首尾帧 PSNR（位置）、亮度差、色度差、是否含黑帧。验收：PSNR≥35（视觉无缝）、无黑帧、明度/色度差小。

## 8. zoom / 变焦镜头（拉近）`wpclip/zoom.py`

- log-polar phase correlation estimates `zoom_ratio` and linearity. For zoom-in shots: prefer
  **clipping the most stable sub-window** (`pick_stable_window`) for the palindrome loop; only
  consider per-frame reverse scaling to cancel zoom if the estimate is reliable.
  log-polar 相位相关估计 `zoom_ratio` 与线性度。对拉近镜头：首选**截取最稳子窗**
  （`pick_stable_window`）做回文循环；若估计可靠再考虑逐帧反向缩放抵消变焦。
