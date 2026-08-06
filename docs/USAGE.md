# 使用 / Usage

## 命令行 / CLI

```bash
# 探测源（含真实色彩范围）
# Probe source (including true color range)
python3 -m wpclip.cli probe "sources/xxx.mkv" --range

# 镜头定位
# Shot locating
python3 -m wpclip.cli locate "sources/xxx.mkv" --start 7422 --end 7426 --mode audio_onset

# 计算裁剪窗口（不改视频，先看取景）
# Compute crop windows (preview only, no encoding)
python3 -m wpclip.cli crops --src "sources/xxx.mkv" --device mbp16-m4 \
    --centers 0.5,0.42,0.58 --labels center,left,right

# 完整处理一个片段 → 成片矩阵
# Full processing: one shot → deliverable matrix
python3 -m wpclip.cli process "sources/xxx.mkv" --name my_wall \
    --start 6539.1 --end 6543.9 --device mbp16-m4 --mode exact \
    --crop-centers "semantic=0.48,energy=0.36" \
    --speeds 1.0,0.75,0.5,0.25 --loops palindrome \
    --fusion --free-frac 0.8 \
    --outdir projects/my_wall/deliverables

# 校验成片（接缝/元数据）
# Verify deliverables (seam quality / metadata)
python3 -m wpclip.cli verify projects/my_wall/deliverables/*.mp4
```

参数说明 / Parameters：
- `--device`：设备预设（`mbp16-m4`、`mbp14`、`mba13`、`imac24`、`studio27`、`uhd16x9`…）或自定义 `3440x1440`。Device preset or custom `WxH`.
- `--mode`：`scene` / `audio_onset` / `fade_clean` / `exact`。Shot-locating strategy.
- `--fusion`：追加两两/全体均值融合取景（位置与尺寸都可融合）。Add pairwise / full-average fusion crops.
- `--free-frac 0.8`：为语义策略额外出"解锁尺寸固定比例"版。Also produce `_free` variants at the given height fraction.
- `--keep`：保留中间母带（默认跑完即删以省空间）。Keep intermediate master (deleted by default to save space).

## Web 工作台 / Web Workbench

```bash
bash run.sh        # 无浏览器弹窗，http://127.0.0.1:8799 / no auto-browser, http://127.0.0.1:8799
```

流程 / Flow：选片源 → 探测 → 定位 → 预览取景 → 生成 → 查看成果。右上角切换中/英。
Pick source → probe → locate → preview crops → generate → inspect results. Lang toggle (zh/en) at top right.

## 导入到壁纸 App / Import to Wallpaper Apps

成片为 MP4（HEVC/hvc1、无音轨），比例与目标屏幕一致 / Deliverables are MP4 (HEVC/hvc1, no audio), matching the target screen ratio：

打开 **Dynamic Wallpaper / Wallper / Vidwall** 等 → 导入本地视频 → 选择
`projects/<片名>/deliverables/` 下对应倍速/取景的 mp4 即可。

Open **Dynamic Wallpaper / Wallper / Vidwall** etc. → import local video → pick the mp4
with the desired speed & crop from `projects/<film>/deliverables/`.

## 省空间 / 清理 · Saving space / Cleanup

- 中间母带（`_work/`）默认成片后即删；如需重跑不同取景可加 `--keep`。
  Intermediate masters (`_work/`) are deleted after encoding; use `--keep` to preserve for re-runs.
- 源片放 `sources/`（git 忽略），成片放 `projects/*/deliverables/`。
  Source films go in `sources/` (git-ignored), deliverables in `projects/*/deliverables/`.
