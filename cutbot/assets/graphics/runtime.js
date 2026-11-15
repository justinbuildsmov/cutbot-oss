// CutBot scene runtime. Inline this before the model-written scene code, in one self-contained HTML page:
//   a canvas with id "c", then one inline script holding RUNTIME followed by SCENE.
// The scene must define `function draw(t)`, which paints one frame for time t in seconds (0..DUR).
// It must be pure: no Date/Math.random()/timers. Use rand(seed) for randomness.
// The runtime exposes window.render(t) (clear + draw) and window.ready (a Promise).
//
// MODE "card" (default): CW x CH logical, default 380x200, floats above the speaker.
// MODE "full": a full-screen cutaway, default 540x960 logical (1080x1920 out). SAFE is the area outside the
// app UI ("nono zone"): everything that matters goes inside SAFE.
// Drawn at DPR 2. Params come from location.hash JSON (MODE, DUR, CW, CH, ACCENT, and anything else via P.*).

const P = (() => { try { return JSON.parse(decodeURIComponent(location.hash.slice(1)) || '{}'); } catch (e) { return {}; } })();
const MODE = P.MODE === 'full' ? 'full' : 'card';
const CW = +P.CW || (MODE === 'full' ? 540 : 380), CH = +P.CH || (MODE === 'full' ? 960 : 200), DPR = 2, DUR = +P.DUR || 5;
// safe area: card = 16 px margins; full = clear of the top bar (~11%), bottom caption/UI zone (~28%) and right button column (~80 px)
const SAFE = MODE === 'full'
  ? { x0: 24, y0: Math.round(CH * 0.115), x1: CW - 80, y1: Math.round(CH * 0.72) }
  : { x0: 16, y0: 16, x1: CW - 16, y1: CH - 16 };
SAFE.w = SAFE.x1 - SAFE.x0; SAFE.h = SAFE.y1 - SAFE.y0; SAFE.cx = SAFE.x0 + SAFE.w / 2; SAFE.cy = SAFE.y0 + SAFE.h / 2;
const SANS = P.FONT || '-apple-system, "SF Pro Display", system-ui, sans-serif';
const MONO = 'ui-monospace, "SF Mono", Menlo, monospace';
const MIN_FS = MODE === 'full' ? 22 : 13;

const cv = document.getElementById('c');
cv.width = CW * DPR; cv.height = CH * DPR; cv.style.width = CW + 'px'; cv.style.height = CH + 'px';
const ctx = cv.getContext('2d');

// ---- math / easing ----
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lerp = (a, b, k) => a + (b - a) * k;
const seg = (t, a, b) => clamp((t - a) / (b - a));           // 0..1 progress of t through [a,b]
const eOut = k => 1 - Math.pow(1 - k, 3);
const eIn = k => k * k * k;
const eIO = k => k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
// spring for a 0..1 move starting at t0, settling over `dur` s; bounce 0 = no overshoot, 1 = lively
const spring = (t, t0, dur = 0.5, bounce = 0.3) => { const k = clamp((t - t0) / dur); return k >= 1 ? 1 : 1 - Math.exp(-6 * k) * Math.cos(10 * bounce * k); };
function rand(seed) { let s = (seed >>> 0) || 1; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

// ---- text: always fit, never overflow ----
const font = (size, weight = 500, fam = SANS) => `${weight} ${size}px ${fam}`;
function textW(s, size, weight = 500, fam = SANS) { ctx.font = font(size, weight, fam); return ctx.measureText(String(s)).width; }
function ellipsize(s, maxW, size, weight = 500, fam = SANS) {
  s = String(s); if (textW(s, size, weight, fam) <= maxW) return s;
  let lo = 0, hi = s.length; while (lo < hi) { const m = (lo + hi + 1) >> 1; if (textW(s.slice(0, m).trimEnd() + '…', size, weight, fam) <= maxW) lo = m; else hi = m - 1; }
  return s.slice(0, lo).trimEnd() + '…';
}
// largest size <= max that fits on one line in maxW (ellipsized at MIN_FS)
function fit(s, maxW, max, weight = 500, fam = SANS) {
  for (let f = max; f >= MIN_FS; f -= 0.5) if (textW(s, f, weight, fam) <= maxW) return { text: String(s), size: f };
  return { text: ellipsize(s, maxW, MIN_FS, weight, fam), size: MIN_FS };
}
// draw text fitted into a box; align 'left'|'center'|'right'; y is the vertical CENTER of the line
function label(s, x, y, maxW, max, { weight = 500, color = '#fff', align = 'left', fam = SANS, alpha = 1 } = {}) {
  const f = fit(s, maxW, max, weight, fam);
  ctx.save(); ctx.globalAlpha *= alpha; ctx.font = font(f.size, weight, fam); ctx.fillStyle = color;
  ctx.textAlign = align; ctx.textBaseline = 'middle'; ctx.fillText(f.text, x, y); ctx.restore();
  return f;
}

// ---- shapes ----
function rr(x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x, y, Math.max(0, w), Math.max(0, h), Math.max(0, Math.min(r, h / 2, w / 2))); }
function line(pts, { color = '#fff', width = 2, alpha = 1, upto = 1, cap = 'round' } = {}) {   // polyline, drawn up to fraction `upto` of its length (self-drawing)
  if (pts.length < 2 || upto <= 0) return;
  const L = []; let tot = 0; for (let i = 1; i < pts.length; i++) { const d = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]); L.push(d); tot += d; }
  let left = tot * clamp(upto); ctx.save(); ctx.globalAlpha *= alpha; ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = cap; ctx.lineJoin = 'round';
  ctx.beginPath(); ctx.moveTo(...pts[0]);
  for (let i = 1; i < pts.length && left > 0; i++) { const k = Math.min(1, left / L[i - 1]); ctx.lineTo(lerp(pts[i - 1][0], pts[i][0], k), lerp(pts[i - 1][1], pts[i][1], k)); left -= L[i - 1]; }
  ctx.stroke(); ctx.restore();
}

// ---- frame ----
window.render = function (t) {
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.globalAlpha = 1; ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
  ctx.clearRect(0, 0, CW, CH);
  window.__text = { minPx: Infinity, outside: [] };
  draw(clamp(t, 0, DUR));
};
// ---- 3D: three.js is on the page only when the scene uses THREE ----
// GL.renderer draws into its own offscreen canvas; render3d(scene, camera) renders it and paints it into the
// 2D canvas, so 2D text (label/fit) drawn afterwards lands on top and the text audit still sees it.
const GL = window.THREE ? (() => {
  const c = document.createElement('canvas');
  const r = new THREE.WebGLRenderer({ canvas: c, alpha: true, antialias: true, preserveDrawingBuffer: true });
  r.setPixelRatio(DPR); r.setSize(CW, CH, false); r.setClearColor(0x000000, 0);
  r.outputColorSpace = THREE.SRGBColorSpace; r.toneMapping = THREE.ACESFilmicToneMapping;
  r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFShadowMap;
  return { renderer: r, canvas: c };
})() : null;
function render3d(scene, camera) {
  GL.renderer.render(scene, camera);
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.drawImage(GL.canvas, 0, 0); ctx.restore();
}
// real logos / app icons fetched for this beat: ASSETS[name] = {svg?: text, png?: data URL, color?: '#hex', title?}
const ASSETS = window.ASSETS || {};
// an optional async setup() (load SVGs/textures, build meshes) runs once before the first frame
window.ready = (document.fonts ? document.fonts.ready : Promise.resolve())
  .then(() => (typeof setup === 'function' ? setup() : null))
  .then(() => { window.render(0); return true; });
// text audit for the checker: smallest text size drawn, and text drawn outside SAFE (window.__text after render)
window.__text = { minPx: Infinity, outside: [] };
const _fill = ctx.fillText.bind(ctx);
ctx.fillText = function (s, x, y, mw) {
  const m = /(\d+(?:\.\d+)?)px/.exec(ctx.font), px = m ? +m[1] : 0;
  const tr = ctx.getTransform(), w = ctx.measureText(s).width, X = (tr.e + x * tr.a) / DPR, Y = (tr.f + y * tr.d) / DPR;
  const a = ctx.textAlign, x0 = a === 'center' ? X - w / 2 : (a === 'right' || a === 'end') ? X - w : X;
  if (String(s).trim() && ctx.globalAlpha > 0.05) {
    window.__text.minPx = Math.min(window.__text.minPx, px * Math.abs(tr.a) / DPR);
    if (x0 < SAFE.x0 - 1 || x0 + w > SAFE.x1 + 1 || Y - px / 2 < SAFE.y0 - 1 || Y + px / 2 > SAFE.y1 + 1) window.__text.outside.push(String(s).slice(0, 20));
  }
  return _fill(s, x, y, mw);
};
// overflow guard used by the checker: scenes may set window.__warn = '...' when something didn't fit
window.__warn = '';
