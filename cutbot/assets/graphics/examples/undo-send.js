// BEAT: "Gmail actually holds your email for 10 seconds before it sends, that's how undo send works"   DUR 5.0   ACCENT #0A84FF
// 0.0 s  an envelope draws itself on (line art), with "Gmail" set small in type: no fake logo
// 0.5 s  a ring around it starts emptying from 10 to 0 seconds, the "holds it" made literal. Numbers tick in MONO
// 4.0 s  ring empties, envelope lifts up and away ("sends"); the timer becomes "sent"
const ACC = P.ACCENT || '#0A84FF', BG = '#141416', INK = '#F2F2F5', MUTED = '#8E8E96';
const CX = CW / 2 - 60, CY = CH / 2 + 4, RAD = 58;

function draw(t) {
  ctx.fillStyle = BG; ctx.fillRect(0, 0, CW, CH);
  const draw1 = eOut(seg(t, 0, 0.5));
  const run = seg(t, 0.5, 4.0);                 // 0..1 over the "10 seconds" (compressed to beat length)
  const leave = eIO(seg(t, 4.05, 4.6));
  const ex = CX, lift = leave * 46;
  // envelope (self-drawing)
  const w = 52, h = 36, x = ex - w / 2, y = CY - h / 2 - lift;
  line([[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]], { color: INK, width: 2.2, upto: draw1, alpha: 1 - leave });
  line([[x, y], [ex, CY + 3 - lift], [x + w, y]], { color: INK, width: 2.2, upto: seg(draw1, 0.4, 1), alpha: 1 - leave });
  // countdown ring
  if (t >= 0.3 && leave < 1) {
    ctx.save(); ctx.globalAlpha = (1 - leave) * eOut(seg(t, 0.3, 0.6));
    ctx.lineWidth = 4; ctx.lineCap = 'round';
    ctx.strokeStyle = '#26262C'; ctx.beginPath(); ctx.arc(CX, CY, RAD, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = ACC; ctx.beginPath(); ctx.arc(CX, CY, RAD, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (1 - run)); ctx.stroke();
    ctx.restore();
  }
  // right side: the number that ticks + the name in type
  const secs = Math.ceil(10 * (1 - run));
  const tx = CX + RAD + 36, a = eOut(seg(t, 0.4, 0.8));
  label(t < 4.0 ? `${secs}s` : 'sent', tx, CY - 8, CW - tx - 20, 46, { weight: 600, color: t < 4.0 ? INK : ACC, fam: MONO, alpha: a });
  label('Gmail', tx, CY + 28, CW - tx - 20, 16, { weight: 500, color: MUTED, alpha: a });
}
