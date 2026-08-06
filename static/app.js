/* 前端逻辑：片源/设备选择、取景预览、任务提交与轮询、成果展示。
   动态文案一律走 tr("中文") 以支持双语。 */
(function () {
  const $ = (id) => document.getElementById(id);
  const state = { sources: [], devices: [], previews: [], jobTimer: null };
  const CENTERS = [0.5, 0.42, 0.58];

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error((await r.text()) || r.status);
    return r.json();
  }

  async function loadState() {
    const s = await api("/api/state");
    state.sources = s.sources; state.devices = s.devices;
    const src = $("src"); src.innerHTML = "";
    if (!s.sources.length) {
      src.innerHTML = `<option>${tr("无片源")}</option>`;
    }
    s.sources.forEach((x) => src.add(new Option(x.label, x.path)));
    const dev = $("device"); dev.innerHTML = "";
    // 只显示比例（不显示分辨率，避免"逻辑/物理"口径不准的歧义）
    s.devices.forEach((d) => dev.add(new Option(
      `${d.name} · ${d.ratio.join(":")} (≈${(d.ratio[0] / d.ratio[1]).toFixed(3)})`, d.key)));
    dev.value = "mbp16-m4";
  }

  async function probe() {
    const src = $("src").value; if (!src) return;
    $("probe-out").textContent = tr("探测中…");
    const r = await api("/api/probe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ src }) });
    const i = r.info;
    $("probe-out").textContent =
      `${i.width}×${i.height} · ${i.fps}fps · ${tr("时长")} ${Math.round(i.duration)}s · ` +
      `${tr("黑边")} [${r.bars.join(",")}] · ${tr("真实色彩范围")} ${r.actual_range}`;
  }

  async function preview() {
    const src = $("src").value; if (!src) return;
    $("previews").innerHTML = `<div class="muted">${tr("预览中…")}</div>`;
    const body = { src, start: +$("start").value, end: +$("end").value,
                   device: $("device").value, centers: CENTERS,
                   fusion: true, free_frac: 0.8, include_energy: true };
    const r = await api("/api/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    state.previews = r.previews;
    // 默认勾选基础三策略，其余（free/融合）供勾选
    state.previews.forEach((p) => { if (p.sel === undefined)
      p.sel = !/free|fused/.test(p.label); });
    renderPreviews();
  }
  function renderPreviews() {
    $("previews").innerHTML = state.previews.map((p, i) =>
      `<figure class="pv ${p.sel ? "sel-on" : ""}"><label class="pv-sel">
       <input type="checkbox" data-i="${i}" ${p.sel ? "checked" : ""}>
       <img src="${p.url}" alt="${p.label}"></label>
       <figcaption>${p.label} · ${p.crop.w}×${p.crop.h}</figcaption></figure>`).join("");
    $("previews").querySelectorAll("input[type=checkbox]").forEach((cb) => {
      cb.addEventListener("change", () => {
        state.previews[+cb.dataset.i].sel = cb.checked;
        cb.closest("figure").classList.toggle("sel-on", cb.checked);
      });
    });
  }

  function selectedSpeeds() {
    return [...document.querySelectorAll("#speeds input:checked")].map((c) => +c.value);
  }
  async function run() {
    const src = $("src").value; if (!src) return;
    const selected = state.previews.filter((p) => p.sel).map((p) => p.crop);
    const speeds = selectedSpeeds();
    const body = { src, name: $("name").value || "wallpaper",
      start: +$("start").value, end: +$("end").value,
      device: $("device").value, mode: $("mode").value,
      loops: [$("loop").value], speeds: speeds.length ? speeds : null,
      crops: selected.length ? selected : null };
    const r = await api("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    startPoll(r.job_id);
  }
  function startPoll(id) {
    clearInterval(state.jobTimer);
    $("run-status").textContent = tr("运行中…");
    state.jobTimer = setInterval(async () => {
      const r = await api("/api/jobs");
      const job = r.jobs.find((j) => j.id === id);
      if (!job) return;
      $("job-log").textContent = (job.log || []).slice(-12).join("\n");
      if (job.status === "done") {
        clearInterval(state.jobTimer);
        $("run-status").textContent = `${tr("已完成")} · ${job.result.deliverables} ${tr("个成片")}`;
        loadResults();
      } else if (job.status === "failed") {
        clearInterval(state.jobTimer);
        $("run-status").textContent = `${tr("失败")}: ${job.error}`;
      }
    }, 1200);
  }

  async function loadResults() {
    const r = await api("/api/results");
    $("results").innerHTML = r.results.map((x) =>
      `<div class="result"><video src="${x.url}" muted loop playsinline preload="none"
        onmouseenter="this.play()" onmouseleave="this.pause();this.currentTime=0"></video>
       <div class="r-meta">
         <span class="fname">${x.project}/${x.file}</span>
         <span class="muted">${(x.size / 1e6).toFixed(1)}MB</span>
         <div class="r-actions">
           <a class="btn small" href="${x.url}" target="_blank">${tr("下载")}</a>
           <button class="btn small" data-save="${x.project}/${x.file}">${tr("保存")}</button>
         </div></div></div>`).join("");
    $("results").querySelectorAll("[data-save]").forEach((b) => {
      b.addEventListener("click", async () => {
        const dest = $("save-dir") ? $("save-dir").value : "";
        const r2 = await api("/api/save", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file: b.dataset.save, dest_dir: dest || null }) });
        b.textContent = "✓"; setTimeout(() => { b.textContent = tr("保存"); }, 1200);
        console.log("saved", r2.saved);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", async () => {
    await loadState(); loadResults();
    $("probe-btn").addEventListener("click", probe);
    $("preview-btn").addEventListener("click", preview);
    $("run-btn").addEventListener("click", run);
    $("refresh-btn").addEventListener("click", loadResults);
    window.I18N.onChange(() => { loadState(); renderPreviews(); loadResults(); });
  });
})();
