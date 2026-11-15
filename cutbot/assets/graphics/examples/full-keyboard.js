// BEAT: "after you type h-e-l-l, the left edge of the P key secretly types an O instead"   DUR 5.0   MODE full   ACCENT #0A84FF
// Full-screen cutaway (MODE full). Everything stays inside SAFE (clear of the app UI zones).
// 0.0 s  the text field is already there with "hel" + caret: readable from the first frame, nothing pops in cold
// 0.4 s  the second "l" types in ("type h-e-l-l"), while the O and P keys rise up from below
// 1.4 s  under each key its touch "bell curve" draws on; P's curve is the bigger one where the finger will land...
// 2.2 s  ..."the left edge of the P key": a finger dot lands on P's left edge
// 2.6 s  "secretly": O's curve swells (the prediction), its region slides into P; the stolen strip of P turns accent
// 3.4 s  "types an O instead": an O flies from the key into the field; "hello", the o in accent
// Ends on the same field + keys layout it started with, so the next full-screen beat can morph from it.
const ACC = P.ACCENT || '#0A84FF', BG = '#0E0E10', INK = '#F2F2F5', MUTED = '#8E8E96', KEY = '#3A3A40', KEYHI = '#4A4A52';
const KW = 128, KH = 150, GAP = 16, KR = 18;
const kx0 = SAFE.cx - KW - GAP / 2, kx1 = SAFE.cx + GAP / 2, ky = SAFE.y0 + 170;
const FY = SAFE.y0 + 70;                        // text field centre line
const GY = ky + KH + 175;                       // curve baseline
const strip = (KW + GAP) / 6;                    // how far into P the O can reach (1/6 of a key pitch)

function key(x, y, ch, a, steal) {
  ctx.save(); ctx.globalAlpha *= a;
  ctx.fillStyle = KEY; rr(x, y, KW, KH, KR); ctx.fill();
  if (steal > 0) {                               // the stolen strip on P's left edge
    ctx.save(); rr(x, y, KW, KH, KR); ctx.clip();
    ctx.fillStyle = ACC; ctx.globalAlpha *= 0.85; ctx.fillRect(x, y, strip * steal, KH); ctx.restore();
  }
  ctx.restore();
  label(ch, x + KW / 2, y + KH / 2 + 2, KW - 20, 64, { weight: 400, color: INK, align: 'center', alpha: a });
}

function bell(cx, sig, amp, color, upto, alpha) {
  const pts = [];
  for (let x = SAFE.x0; x <= SAFE.x1; x += 4) pts.push([x, GY - amp * Math.exp(-((x - cx) ** 2) / (2 * sig * sig))]);
  line(pts, { color, width: 3, upto, alpha });
}

function draw(t) {
  ctx.fillStyle = BG; ctx.fillRect(0, 0, CW, CH);

  // text field (present from frame 0)
  const typed = t < 0.5 ? 'hel' : 'hell';
  const done = t >= 3.7;
  const fx = SAFE.x0 + 20, fw = SAFE.w - 40;
  ctx.strokeStyle = '#2A2A30'; ctx.lineWidth = 2; rr(fx, FY - 38, fw, 76, 22); ctx.stroke();
  ctx.font = font(44, 500); ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
  ctx.fillStyle = INK; ctx.fillText(typed, fx + 26, FY + 2);
  let cx = fx + 26 + ctx.measureText(typed).width;
  if (done) {
    const a = eOut(seg(t, 3.7, 3.95));
    ctx.save(); ctx.globalAlpha = a; ctx.fillStyle = ACC; ctx.fillText('o', cx, FY + 2 - (1 - a) * 10); ctx.restore();
    cx += ctx.measureText('o').width * a;
  }
  if (Math.floor(t * 2) % 2 === 0) { ctx.fillStyle = ACC; ctx.fillRect(cx + 3, FY - 22, 3, 44); }   // caret blink

  // keys rise in
  const kin = spring(t, 0.4, 0.6, 0.2);
  const kyy = ky + (1 - kin) * 60;
  const steal = eIO(seg(t, 2.6, 3.2));
  key(kx0, kyy, 'O', kin, 0);
  key(kx1, kyy, 'P', kin, steal);

  // bell curves: P's is the default winner; O's swells with the prediction
  const draw1 = eOut(seg(t, 1.4, 2.1));
  const oc = kx0 + KW / 2, pc = kx1 + KW / 2;
  bell(pc, 34, 95, MUTED, draw1, 1);
  bell(oc, 34 + 18 * steal, 95 + 55 * steal, steal > 0 ? ACC : MUTED, draw1, 1);
  if (draw1 > 0) { ctx.fillStyle = '#2A2A30'; ctx.fillRect(SAFE.x0, GY, SAFE.w * draw1, 2); }

  // the finger tap on P's left edge, and the dashed line down to the curves
  const tapX = kx1 + strip * 0.55, tapY = kyy + KH * 0.55;
  const tap = spring(t, 2.2, 0.35, 0.4);
  if (t >= 2.2) {
    ctx.save(); ctx.globalAlpha = clamp(tap);
    ctx.fillStyle = 'rgba(255,255,255,0.25)'; ctx.beginPath(); ctx.arc(tapX, tapY, 22 * tap, 0, 7); ctx.fill();
    ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(tapX, tapY, 7, 0, 7); ctx.fill();
    ctx.setLineDash([4, 6]); ctx.strokeStyle = MUTED; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(tapX, tapY + 26); ctx.lineTo(tapX, GY); ctx.stroke();
    ctx.restore();
  }

  // the O flies from its key up into the text field
  const fly = eIO(seg(t, 3.3, 3.75));
  if (fly > 0 && fly < 1) {
    const x = lerp(oc, cx - 10, fly), y = lerp(kyy + KH / 2, FY, fly) - Math.sin(fly * Math.PI) * 40;
    label('o', x, y, 60, lerp(64, 44, fly), { weight: 500, color: ACC, align: 'center' });
  }
}
