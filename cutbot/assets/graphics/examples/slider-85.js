// BEAT: "if you let go before about 85% of the way, it just springs back"   DUR 4.0   ACCENT #0A84FF
// 0.0 s  track draws on from the left: the object is the slider itself, not a stat about it
// 0.5 s  knob drags to ~70% (eased, like a finger)
// 1.5 s  "let go": knob springs back to the start; this is the claim, shown
// 2.3 s  knob drags again, crosses the 85% tick, which lights up in the accent; "85%" sits on the tick
const ACC = P.ACCENT || '#0A84FF', BG = '#141416', INK = '#F2F2F5', MUTED = '#5C5C66';
const X0 = 40, X1 = CW - 40, Y = CH / 2 + 14, R = 17;
const at = f => lerp(X0 + R, X1 - R, f);

function knobPos(t) {
  if (t < 0.5) return 0;
  if (t < 1.4) return 0.70 * eIO(seg(t, 0.5, 1.4));
  if (t < 2.2) return 0.70 * (1 - spring(t, 1.5, 0.6, 0.25));       // released -> springs home
  return 0.97 * eIO(seg(t, 2.3, 3.4));                               // second drag clears 85%
}
function draw(t) {
  ctx.fillStyle = BG; ctx.fillRect(0, 0, CW, CH);
  const k = knobPos(t), grow = eOut(seg(t, 0, 0.45));
  // track
  ctx.fillStyle = '#26262C'; rr(X0, Y - R - 4, (X1 - X0) * grow, 2 * R + 8, R + 4); ctx.fill();
  // 85% tick
  const tx = at(0.85), passed = k >= 0.85;
  const tick = eOut(seg(t, 0.25, 0.6));
  line([[tx, Y - R - 14], [tx, Y + R + 14]], { color: passed ? ACC : MUTED, width: 2, upto: tick });
  label('85%', tx, Y - R - 30, 80, 20, { weight: 650, color: passed ? ACC : INK, align: 'center', alpha: tick });
  // knob
  if (grow > 0.9) {
    const kx = at(k);
    ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(kx, Y, R, 0, Math.PI * 2); ctx.fill();
    // power glyph drawn, not an emoji
    ctx.strokeStyle = BG; ctx.lineWidth = 2.2; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.arc(kx, Y + 1, 7, -Math.PI * 0.32, Math.PI * 1.32); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(kx, Y - 9); ctx.lineTo(kx, Y - 1); ctx.stroke();
  }
}
