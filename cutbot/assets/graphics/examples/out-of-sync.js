// BEAT: "each icon starts at a random point, so no two of them are ever in sync"   DUR 5.0   ACCENT #FF9F0A
// 0.0 s  a 4x2 grid of app-icon squares fades up in a quick stagger
// 0.6 s  all start wiggling, each with its own random phase (rand(seed): deterministic)
// 2.4 s  two neighbours get an accent outline so the eye compares them; they visibly never match
// No text. The motion is the explanation.
const ACC = P.ACCENT || '#FF9F0A', BG = '#141416';
const COLS = 4, ROWS = 2, S = 46, GAP = 22;
const r = rand(7);
const icons = Array.from({ length: COLS * ROWS }, () => ({ ph: r() * 6.28, hue: [ '#3A3A44', '#4A4A56', '#2E2E36' ][Math.floor(r() * 3)] }));
const gx = (CW - (COLS * S + (COLS - 1) * GAP)) / 2, gy = (CH - (ROWS * S + (ROWS - 1) * GAP)) / 2;

function draw(t) {
  ctx.fillStyle = BG; ctx.fillRect(0, 0, CW, CH);
  const live = eOut(seg(t, 0.6, 1.0));
  icons.forEach((ic, i) => {
    const c = i % COLS, row = Math.floor(i / COLS);
    const a = eOut(seg(t, i * 0.04, i * 0.04 + 0.3));
    const cx = gx + c * (S + GAP) + S / 2, cy = gy + row * (S + GAP) + S / 2 + (1 - a) * 6;
    const rot = live * 0.05 * Math.sin(t * 2 * Math.PI * 3.8 + ic.ph);
    const dx = live * 0.9 * Math.sin(t * 2 * Math.PI * 3.6 + ic.ph * 1.3);
    ctx.save(); ctx.globalAlpha = a; ctx.translate(cx + dx, cy); ctx.rotate(rot);
    ctx.fillStyle = ic.hue; rr(-S / 2, -S / 2, S, S, S * 0.24); ctx.fill();
    const hi = (i === 1 || i === 2) ? eOut(seg(t, 2.4, 2.8)) : 0;
    if (hi > 0) { ctx.globalAlpha = a * hi; ctx.strokeStyle = ACC; ctx.lineWidth = 2.5; rr(-S / 2 - 5, -S / 2 - 5, S + 10, S + 10, S * 0.3); ctx.stroke(); }
    ctx.restore();
  });
}
