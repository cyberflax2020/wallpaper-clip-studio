/* 双语 i18n：源码以中文撰写，运行期把 DOM 翻译成英文（再切回中文）。
   机制：PAIRS 精确对 + tr() 组合串 + set() 时全量重翻 [data-i18n]/option。
 */
(function () {
  const PAIRS = [
    ["壁纸剪裁", "Wallpaper Clip Studio"],
    ["把电影镜头剪成无缝循环的桌面动态壁纸", "Turn film shots into seamless-looping live wallpapers"],
    ["1 · 选择片源", "1 · Pick a source"],
    ["2 · 定位镜头", "2 · Locate the shot"],
    ["3 · 目标屏幕与取景", "3 · Target screen & framing"],
    ["4 · 生成壁纸", "4 · Generate wallpapers"],
    ["5 · 成果", "5 · Results"],
    ["探测规格", "Probe"],
    ["起点(秒)", "Start (s)"],
    ["终点(秒)", "End (s)"],
    ["起点模式", "Start mode"],
    ["循环", "Loop"],
    ["回文+折返停顿", "Palindrome + dwell"],
    ["回文", "Palindrome"],
    ["交叉淡化", "Crossfade"],
    ["场景切割", "Scene cut"],
    ["台词声音出现处", "Dialogue onset"],
    ["渐变后干净帧", "Clean frame after fade"],
    ["精确边界", "Exact bounds"],
    ["设备", "Device"],
    ["预览取景", "Preview framing"],
    ["任务名", "Job name"],
    ["倍速", "Speeds"],
    ["开始生成", "Generate"],
    ["成果", "Results"],
    // 动态串（app.js 组合用）
    ["探测中…", "Probing…"],
    ["预览中…", "Previewing…"],
    ["运行中…", "Running…"],
    ["已完成", "Done"],
    ["失败", "Failed"],
    ["无片源", "No sources found"],
    ["个成片", "wallpapers"],
    ["下载", "Open"],
    ["保存", "Save"],
    ["保存目录", "Save to"],
    ["刷新", "Refresh"],
    ["黑边", "bars"],
    ["真实色彩范围", "actual range"],
    ["时长", "duration"],
  ];

  const zhToEn = new Map(PAIRS.map(([z, e]) => [z, e]));
  const enToZh = new Map(PAIRS.map(([z, e]) => [e, z]));
  const urlLang = new URLSearchParams(location.search).get("lang");
  let current = urlLang || localStorage.getItem("wpclip-lang") ||
    (navigator.language && navigator.language.startsWith("zh") ? "zh" : "zh");

  function translateString(v) {
    const t = v.trim();
    if (current === "en") return zhToEn.get(t) || (hasCJK(t) ? t : v);
    return enToZh.get(t) || v;
  }
  function hasCJK(s) {
    return /[一-鿿]/.test(s);
  }

  function apply(root) {
    root = root || document;
    root.querySelectorAll("[data-i18n]").forEach((el) => {
      if (el.dataset.zh === undefined) el.dataset.zh = el.textContent;
      const zh = el.dataset.zh;
      el.textContent = current === "en" ? (zhToEn.get(zh.trim()) || zh) : zh;
    });
    root.querySelectorAll("option[data-i18n]").forEach((el) => {
      if (el.dataset.zh === undefined) el.dataset.zh = el.textContent;
      const zh = el.dataset.zh;
      el.textContent = current === "en" ? (zhToEn.get(zh.trim()) || zh) : zh;
    });
  }

  const onChange = [];
  window.I18N = {
    get lang() { return current; },
    t: (v) => translateString(v),
    set(lang) {
      current = lang;
      localStorage.setItem("wpclip-lang", lang);
      document.documentElement.lang = lang === "zh" ? "zh" : "en";
      apply(document);
      onChange.forEach((cb) => cb(lang));
    },
    toggle() { this.set(current === "zh" ? "en" : "zh"); },
    onChange: (cb) => onChange.push(cb),
  };
  window.tr = window.I18N.t;

  document.addEventListener("DOMContentLoaded", () => {
    apply(document);
    const btn = document.getElementById("lang-toggle");
    if (btn) {
      const sync = () => { btn.textContent = current === "zh" ? "EN" : "中文"; };
      sync();
      window.I18N.onChange(sync);
      btn.addEventListener("click", () => window.I18N.toggle());
    }
  });
})();
