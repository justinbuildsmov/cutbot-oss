"""CutBot editing engine: a talking-head video in, an edited video out.

Pipeline for one clip:
  analyze()   silence detection (ffmpeg), speech runs + per-run transcripts
              (whisper.cpp, or Phonon-2 via `fermion` when installed), and the
              heuristic retake suggestions (retakes.py)
  decide()    which runs to drop, plus look/style: the AI planner (planner.py)
              when OPENAI_API_KEY is set, else the heuristic as-is
  render()    cut the kept ranges (edges snapped out of audible speech), then
              optionally b-roll, sound effects, graphics, captions, loudness and
              colour, in one ffmpeg pass

Everything here shells out to ffmpeg/ffprobe, whisper-cli and (optionally) the
native macOS helpers in native/ (captions, faces, cards). Python stdlib only.
"""
import array, concurrent.futures, hashlib, json, math, os, re, shutil, subprocess, sys, tempfile, time
import urllib.error

from . import planner, retakes

PKG = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG)
ASSETS = os.path.join(PKG, "assets")

NOISE = "-35dB"; MINSIL = 0.45   # silence definition
PAD_IN, PAD_OUT = 0.12, 0.18     # keep-padding around speech
MIN_SPEECH = 1.0                 # refuse to output near-empty clips
CPU_THREADS = os.environ.get("CUTBOT_THREADS") or str(max(2, os.cpu_count() or 4))

VERBOSE = True

def log(msg):
    if VERBOSE:
        print(msg, file=sys.stderr, flush=True)

class CutBotError(Exception):
    """A problem with the input worth reporting to the user as-is."""

class AIError(Exception):
    """The AI planner couldn't be used (no key, network, bad output)."""

def load_config(**over):
    """Settings from the environment; keyword arguments override."""
    cfg = {"openai_api_key": os.environ.get("OPENAI_API_KEY"),
           "openai_model": os.environ.get("CUTBOT_OPENAI_MODEL") or planner.DEFAULT_MODEL}
    cfg.update({k: v for k, v in over.items() if v is not None})
    return cfg

def ai(cfg, fn, *args):
    """Call a planner function, or raise AIError."""
    if not planner.enabled(cfg): raise AIError("no OPENAI_API_KEY set")
    for attempt in (0, 1):
        try:
            out = fn(cfg, *args); break
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as ex:  # a network blip: once more
            if attempt: raise AIError(f"{fn.__name__}: {ex}") from ex
            time.sleep(2)
        except Exception as ex:
            raise AIError(f"{fn.__name__}: {ex}") from ex
    u = planner.LAST_USAGE or {}
    log(f"  llm {fn.__name__}: {u.get('input_tokens', 0)} in, {u.get('output_tokens', 0)} out")
    return out

def _native(name):
    """Path to a built native helper (native/bin/NAME or $CUTBOT_BIN_DIR/NAME), or None."""
    for d in (os.environ.get("CUTBOT_BIN_DIR"), os.path.join(ROOT, "native", "bin")):
        p = d and os.path.join(d, name)
        if p and os.path.isfile(p) and os.access(p, os.X_OK): return p
    return None

CAPTIONS_BIN, FACES_BIN, CARDS_BIN = _native("captions"), _native("faces"), _native("cards")

# ---------- probing ----------

def duration_of(path):
    p = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
                        "-of","csv=p=0", path], capture_output=True, text=True, check=True)
    return float(p.stdout.strip())

def has_audio(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=index", "-of", "csv=p=0", path], capture_output=True, text=True)
    return bool(p.stdout.strip())

def display_size(path):
    """Width/height as shown (phone videos are often stored sideways + a rotation tag)."""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:stream_side_data=rotation", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    st = json.loads(p.stdout)["streams"][0]
    w, h = st["width"], st["height"]
    rot = next((abs(int(d.get("rotation", 0))) for d in st.get("side_data_list", []) if "rotation" in d), 0)
    if rot % 180 == 90: w, h = h, w
    return w - w % 2, h - h % 2

def frame_rate(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        n, d = p.stdout.strip().split("/"); return float(n) / float(d)
    except (ValueError, ZeroDivisionError): return 30.0

# ---------- dead air ----------

# How much pause an edit leaves, by style pace: the shortest pause that gets
# cut, and the padding kept before and after speech.
PACES = {"loose": (1.0, 0.15, 0.25), "normal": (MINSIL, PAD_IN, PAD_OUT), "tight": (0.25, 0.08, 0.14)}

def detect_speech(path):
    """(speech at the normal pace, duration, every pause any pace could cut)."""
    dur = duration_of(path)
    p = subprocess.run(["ffmpeg","-hide_banner","-i",path,"-vn","-af",
                        f"silencedetect=noise={NOISE}:d={min(v[0] for v in PACES.values())}",
                        "-f","null","-"], capture_output=True, text=True)
    txt = p.stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", txt)]
    ends   = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", txt)]
    sil = [[s, e] for s, e in zip(starts, ends + [dur]*(len(starts)-len(ends)))]
    return speech_between(sil, dur), dur, sil

def speech_between(sil, dur, pace="normal"):
    """Speech segments: everything outside pauses long enough to cut at this pace."""
    minsil, pad_in, pad_out = PACES.get(pace, PACES["normal"])
    keeps, pos = [], 0.0
    for s, e in sil:
        if e - s < minsil: continue
        if s - pos > 0.02: keeps.append([pos, s])
        pos = e
    if dur - pos > 0.02: keeps.append([pos, dur])
    # pad speech segments, clamp, merge
    padded = [[max(0, a-pad_in), min(dur, b+pad_out)] for a, b in keeps]
    merged = []
    for seg in padded:
        if merged and seg[0] <= merged[-1][1]: merged[-1][1] = max(merged[-1][1], seg[1])
        else: merged.append(seg)
    return [s for s in merged if s[1]-s[0] > 0.15]

def subtract(keeps, drops, min_len=0.3):
    """Remove drop ranges from keep segments; discard slivers left behind."""
    out = []
    for a, b in keeps:
        cur = [(a, b)]
        for da, db in drops:
            cur = [seg for x, y in cur for seg in
                   ([(x, y)] if db <= x or da >= y else
                    [s for s in ((x, da), (db, y)) if s[1] > s[0]])]
        out += [[x, y] for x, y in cur if y - x >= min_len]
    return out

# ---------- transcription ----------
#
# whisper.cpp (retakes.py) is the default: each speech run is transcribed as its
# own input, which is what lets retakes show up at all. Phonon-2 (the `fermion`
# CLI) is optional: when it's installed it's run once over the whole clip for
# better names/numbers and real word timings, and each run's text is rebuilt
# from its words. If it's missing or fails, whisper's text is used.

FERMION = os.environ.get("CUTBOT_FERMION") or shutil.which("fermion")
FERMION_MODEL = os.environ.get("CUTBOT_FERMION_MODEL") or next(
    (p for p in (os.path.expanduser("~/.cache/fermion/speech/FermionResearch__Phonon-2/model_phonon2_c4c_int6"),)
     if os.path.isfile(os.path.join(p, "config.json"))), "phonon-2")

def tidy_words(ws):
    """Phonon glues numbers to the word before and splits them at the separator
    ("of1" ".9", "motion32" ",000", "a911"): put them back together."""
    out = []
    for w, a, b in ws:
        m = re.fullmatch(r"([A-Za-z']+)(\d[\d.,]*\S*)", w)
        if m:
            cut = a + (b - a) * len(m[1]) / len(w)
            out += [[m[1], a, cut], [m[2], cut, b]]
        elif out and re.match(r"[.,]\d", w) and out[-1][0][-1:].isdigit():
            out[-1][0] += w; out[-1][2] = b
        else:
            out.append([w, a, b])
    return out

def phonon_words(path):
    """[[word, start, end]] in source seconds, or None (Phonon not installed or failed)."""
    if not FERMION: return None
    env = dict(os.environ, FERMION_QUIET_DEPRECATIONS="1")
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "a.wav")
        try:
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000", wav],
                           check=True, capture_output=True, timeout=120)
            p = subprocess.run([FERMION, "transcribe", "--json", "--threads", CPU_THREADS, FERMION_MODEL, wav],
                               capture_output=True, text=True, timeout=600, env=env)
            data = json.loads(p.stdout[p.stdout.index("{"):])
        except Exception as ex:
            log(f"  phonon failed, using whisper only: {str(ex)[-200:]}"); return None
    ws = [(w["text"].strip(), float(w["start"]), max(float(w["end"]), float(w["start"]) + 0.01))
          for w in data.get("words", []) if re.search(r"\w", w.get("text", ""))]
    return tidy_words(ws) or None

def phonon_texts(runs, whisper, words):
    """Each speech run's text from Phonon's words. A word goes to the one run it starts
    in (Phonon stretches a last word into the pause after it, so its end and middle
    can fall outside), else the nearest run within 0.4s. A run Phonon heard nothing
    in keeps whisper's text, so no take goes missing."""
    got = [[] for _ in runs]
    for w, s, e in words:
        dist = [0 if a - .15 <= s <= b else min(abs(s - a), abs(s - b)) for a, b in runs]
        k = min(range(len(runs)), key=dist.__getitem__)
        if dist[k] <= .4: got[k].append(w)
    return [" ".join(ws) if ws else wt for ws, wt in zip(got, whisper)]

def analyze(src, use_phonon=True):
    """Everything about a clip that doesn't depend on edit decisions."""
    if not has_audio(src):
        raise CutBotError(f"{os.path.basename(src)} has no audio track")
    speech, dur, sil = detect_speech(src)
    problem = retakes.whisper_problem()
    if problem:
        log(f"  WARNING: {problem}. Cutting dead air only, no retakes.")
        runs, texts, hint = [], [], []
    else:
        try:
            runs, texts, hint = retakes.analyze(src, dur)
        except Exception as ex:  # whisper broken: still do the dead-air cut
            log(f"  WARNING: transcription failed, cutting dead air only: {ex}")
            runs, texts, hint = [], [], []
    words = phonon_words(src) if use_phonon else None
    if words and runs:  # Phonon hears names and numbers better: the planner, graphics and captions read it
        texts = phonon_texts(runs, texts, words)
        hint = retakes.heuristic_drop(retakes.find_retakes(runs, texts))
    return {"src": src, "dur": dur, "speech": speech, "sil": sil, "runs": runs, "texts": texts, "hint": hint,
            "words": words}

# ---------- deciding the cut ----------

def unheard(text):
    """Whisper wrote only a label ("[MUSIC PLAYING]", "[BLANK_AUDIO]", "(mumbles)")
    for a stretch that has sound in it (real silence is already gone). It's
    usually a word it couldn't make out, so it's never cut as a retake."""
    return not re.sub(r"[\(\[][^)\]]*[\)\]]|[^\w]", "", text or "")

def keep_unheard(drop, texts):
    kept = [i for i in drop if unheard(texts[i])]
    if kept: log(f"  not cutting unclear run(s) {kept}")
    return [i for i in drop if not unheard(texts[i])]

def decide(clip, instructions, cfg, use_ai=True, style=None):
    """Which runs to drop, and the look and style. With AI: the planner decides,
    with the heuristic's suggestions as hints; if it fails, the heuristic is used.
    Without AI: the heuristic's suggestions as-is. Returns (drop, note, look, wants_sfx, style)."""
    base = planner.clean_style(style)
    if not clip["runs"]:  # no speech found: nothing to decide
        return [], "", planner.clean_look(None), False, base
    if use_ai:
        try:
            drop, note, look, sfx, st = ai(cfg, planner.plan_cut, clip["runs"], clip["texts"], clip["hint"],
                                           instructions, base)
            return keep_unheard(drop, clip["texts"]), note, look, sfx, st
        except AIError as ex:
            log(f"  WARNING: AI planner unavailable ({ex}); using the heuristic retake cut")
    return keep_unheard(clip["hint"], clip["texts"]), "", planner.clean_look(None), False, base

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm"}

def effective_drop(clip):
    """Runs cut from this edit: the planned drops, plus runs that are only "um"/"uh"
    when the style asks for fillers to go. (Whisper hides fillers inside a run,
    so only ones said on their own, between pauses, can be cut safely.)"""
    drop = set(clip["drop"])
    if planner.clean_style(clip.get("style"))["fillers"]:
        ums = {i for i, t in enumerate(clip["texts"]) if (w := retakes.norm(t)) and set(w) <= FILLERS}
        if len(drop | ums) < len(clip["runs"]): drop |= ums
    return sorted(drop)

def edit_keeps(clip):
    """Source ranges that survive the cut (speech from the waveform at the style's
    pace, minus drops), with every edge checked so no word gets clipped."""
    pace = planner.clean_style(clip.get("style"))["pace"]
    speech = speech_between(clip["sil"], clip["dur"], pace) if "sil" in clip else clip["speech"]
    keeps = subtract(speech, retakes.run_drop_ranges(clip["runs"], effective_drop(clip), clip["dur"]))
    return snap_edges(clip["src"], keeps, clip["dur"], clip["runs"])

def count_groups(drop):
    """Contiguous dropped runs count as one retake."""
    return sum(1 for i, d in enumerate(drop) if i == 0 or d != drop[i-1] + 1)

# ---------- cut-edge check ----------
#
# A cut where the voice is still audible clips a word: soft s/z/l endings keep
# going 0.15-0.2s past where the silence detector says speech ends. Every in and
# out point is checked against the clip's own room noise and moved out into the
# quiet if needed: a little at a time, never into a neighbouring piece or into
# another speech run (that could pull in part of a retake).

_pcm = {}  # src -> mono 16 kHz int16 samples (one clip at a time)

def audio_pcm(src):
    if src not in _pcm:
        p = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", "16000",
                            "-f", "s16le", "-"], capture_output=True, check=True)
        a = array.array("h"); a.frombytes(p.stdout[:len(p.stdout) // 2 * 2])
        _pcm.clear(); _pcm[src] = a
    return _pcm[src]

def snap_edges(src, keeps, dur, runs=()):
    try: pcm = audio_pcm(src)
    except Exception: return keeps
    if not pcm or not keeps: return keeps
    rate, n = 16000, len(pcm)
    def db(t):  # level of the 20 ms around t
        i = int(t * rate); w = pcm[max(0, i - 160):min(n, i + 160)]
        if not w: return -120.0
        return 20 * math.log10(max(1e-9, math.sqrt(sum(x * x for x in w) / len(w)) / 32768))
    probes = sorted(db(dur * k / 400) for k in range(400))
    quiet = max(probes[40] + 8, -60.0)  # 8 dB over the room's noise floor
    out, moved = [], 0
    for i, (a, b) in enumerate(keeps):
        lo = max([keeps[i - 1][1] if i else 0.0] + [r[1] + .02 for r in runs if r[1] <= a])
        hi = min([keeps[i + 1][0] if i + 1 < len(keeps) else dur] + [r[0] - .02 for r in runs if r[0] >= b])
        na, nb = a, b
        if a > 0 and db(a) > quiet:
            t = a
            while t - .01 >= max(lo, a - .25) and db(t) > quiet: t -= .01
            na = max(lo, t - .03)
        if b < dur and db(b) > quiet:
            t = b
            while t + .01 <= min(hi, b + .35) and db(t) > quiet: t += .01
            nb = min(hi, t + .06)
        moved += (na < a) + (nb > b)
        out.append([min(na, a), max(nb, b)])
    merged = []
    for seg in out:
        if merged and seg[0] <= merged[-1][1]: merged[-1][1] = max(merged[-1][1], seg[1])
        else: merged.append(seg)
    if moved: log(f"  edge check: moved {moved} cut(s) out of audible speech")
    return merged

# ---------- rendering ----------

def look_filter(look):
    """ffmpeg filters for planner LOOK values (-1..1 each, 0 = unchanged)."""
    l = planner.clean_look(look)
    f = []
    b, c, s = l["brightness"], l["contrast"], l["saturation"]
    if b or c or s:
        sat = 1 + s if s < 0 else 1 + s * .8   # -1 = black and white
        f.append(f"eq=brightness={.12 * b:.3f}:contrast={1 + .35 * c:.3f}:saturation={sat:.3f}")
    w = l["warmth"]
    if w:
        f.append(f"colorbalance=rm={.12 * w:.3f}:bm={-.12 * w:.3f}:rs={.06 * w:.3f}:bs={-.06 * w:.3f}")
    h = l["sharpness"]
    if h:
        f.append(f"unsharp=5:5:{(1.2 if h > 0 else .8) * h:.2f}")
    return ",".join(f)

def render_cut(src, keeps, out, look=None, broll=(), sfx=(), loud=False, captions=None, cards=()):
    """Cut `keeps` from src; optionally lay b-roll over it and grade the result.
    loud: even out the volume (-14 LUFS, peaks under -1.5 dB). captions: an ffmpeg
    concat list of transparent caption frames timed to the output (caption_track).
    broll: [(path, kind, start, end, transition, motion, sound)] in output seconds
    (kind "photo"/"video"; transition "cut"/"fade"; motion "none"/"zoom_in"/"zoom_out";
    sound: mix that clip's audio under the voice).
    sfx: [(wav, start, gain_db, rate)] sound effects mixed under the voice.
    cards: [(concat list of RGBA frames, start, end, x, y, w, placement)] graphics, laid on
    after the grade (so they keep their colors). "card": 0.15s fade + 14px rise in, 0.16s
    fade + 10px drop out. "full": fills the frame with a quick 0.1s crossfade in and out."""
    parts, fc, inputs = [], [], ["-i", src]
    for i, (a, b) in enumerate(keeps):
        fc.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}];"
                  f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}];")
        parts.append(f"[v{i}][a{i}]")
    fc.append(f"{''.join(parts)}concat=n={len(keeps)}:v=1:a=1[cv][a];")
    cur, mixes = "cv", []
    if broll:
        W, H = display_size(src)
        for i, (path, kind, a, b, transition, motion, sound) in enumerate(broll):
            d, n = b - a, i + 1
            if kind == "photo":
                inputs += ["-loop", "1", "-framerate", "30", "-t", f"{d + .1:.3f}", "-i", path]
            else:
                inputs += ["-t", f"{d + .1:.3f}", "-i", path]
            fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            if motion in ("zoom_in", "zoom_out"):
                grow = f"(1+.06*t/{d:.3f})" if motion == "zoom_in" else f"(1.06-.06*t/{d:.3f})"
                fit += f"scale=w='trunc(iw*{grow}/2)*2':h=-2:eval=frame,"
            fit += f"crop={W}:{H},setsar=1"
            if transition == "fade":
                fit += (f",format=yuva420p,fade=t=in:st=0:d=0.25:alpha=1,"
                        f"fade=t=out:st={max(0, d - .25):.3f}:d=0.25:alpha=1")
            fc.append(f"[{n}:v]trim=duration={d:.3f},setpts=PTS-STARTPTS,{fit},"
                      f"setpts=PTS+{a:.3f}/TB[b{i}];"
                      f"[{cur}][b{i}]overlay=eof_action=pass:enable='between(t,{a:.3f},{b:.3f})'[o{i}];")
            cur = f"o{i}"
            if sound:
                ms = int(a * 1000)
                fc.append(f"[{n}:a]atrim=duration={d:.3f},asetpts=PTS-STARTPTS,volume=0.5,"
                          f"afade=t=in:d=0.05,afade=t=out:st={max(0, d - .05):.3f}:d=0.05,"
                          f"adelay={ms}|{ms}[s{i}];")
                mixes.append(f"[s{i}]")
    for j, (wav, t, gain, rate) in enumerate(sfx):
        n = inputs.count("-i")  # index this input will get
        inputs += ["-i", wav]
        ms = int(t * 1000)
        fc.append(f"[{n}:a]aformat=sample_rates=48000:channel_layouts=stereo,asetrate={48000 * rate:.0f},"
                  f"aresample=48000,volume={gain:.1f}dB,adelay={ms}|{ms}[x{j}];")
        mixes.append(f"[x{j}]")
    aout = "a"
    if mixes:
        fc.append(f"[a]{''.join(mixes)}amix=inputs={len(mixes) + 1}:duration=first:normalize=0[am];")
        aout = "am"
    if loud:  # loudnorm works at 192 kHz internally, so resample back before the limiter
        fc.append(f"[{aout}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,"
                  f"alimiter=limit=0.84:level=false:latency=true[al];")
        aout = "al"
    grade = look_filter(look)
    fc.append(f"[{cur}]{grade + ',' if grade else ''}format=yuv420p[g];")
    cur = "g"
    for i, (lst, a, b, x, y, w, kind) in enumerate(cards):
        n, d = inputs.count("-i"), b - a
        inputs += ["-f", "concat", "-safe", "0", "-i", lst]
        if kind == "full":
            W, H = display_size(src)
            fc.append(f"[{n}:v]format=rgba,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
                      f"fade=t=in:st=0:d=0.1:alpha=1,fade=t=out:st={max(0, d - .1):.3f}:d=0.1:alpha=1,"
                      f"setpts=PTS+{a:.3f}/TB[k{i}];"
                      f"[{cur}][k{i}]overlay=x=0:y=0:eof_action=pass:enable='between(t,{a:.3f},{b:.3f})'[kv{i}];")
            cur = f"kv{i}"
            continue
        fc.append(f"[{n}:v]format=rgba,scale={w}:-2,fade=t=in:st=0:d=0.15:alpha=1,"
                  f"fade=t=out:st={max(0, d - .16):.3f}:d=0.16:alpha=1,setpts=PTS+{a:.3f}/TB[k{i}];"
                  f"[{cur}][k{i}]overlay=x={x}:y='{y}+14*max(0,1-(t-{a:.3f})/0.18)+10*max(0,(t-{b - .16:.3f})/0.16)'"
                  f":eof_action=pass:enable='between(t,{a:.3f},{b:.3f})'[kv{i}];")
        cur = f"kv{i}"
    if captions:  # drawn after the grade so the text stays pure white
        n = inputs.count("-i")
        inputs += ["-f", "concat", "-safe", "0", "-i", captions]
        fc.append(f"[{cur}][{n}:v]overlay=eof_action=pass,format=yuv420p[v]")
    else:
        fc.append(f"[{cur}]format=yuv420p[v]")
    p = subprocess.run(["ffmpeg", "-y", "-filter_complex_threads", CPU_THREADS, *inputs,
                        "-filter_complex", "".join(fc),
                        "-map", "[v]", "-map", f"[{aout}]", "-c:v", "libx264", "-threads", CPU_THREADS,
                        "-preset", "fast",
                        "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart", out],
                       capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"ffmpeg failed: {p.stderr[-800:]}")

def caption_placer(face, position, bands):
    """place(cap_h, start, end) for captions: off the face (caption_y), and when a
    graphic card is up at that time and the caption would sit on it, just under it."""
    def place(cap_h, a, b):
        y = caption_y(face, position, cap_h)
        for ca, cb, top, bot in bands:
            if a < cb and b > ca and y + cap_h / 2 > top - .01 and y - cap_h / 2 < bot + .01:
                y = min(bot + .02 + cap_h / 2, SAFE_BOTTOM - cap_h / 2)
        return y
    return place

def render(clip, out, cfg, instructions="", use_ai=True):
    """Render clip['drop'] (+ look, style, b-roll, sfx, graphics, captions) to `out`.
    Sets kept/out/retakes on the clip and returns it."""
    keeps = edit_keeps(clip)
    kept = sum(b-a for a, b in keeps)
    if kept < MIN_SPEECH:
        raise CutBotError("couldn't hear any talking in that clip")
    style = planner.clean_style(clip.get("style"))
    caps_on = style["captions"] and not clip.get("captions_baked")  # a combined video's parts already have them
    if caps_on and not CAPTIONS_BIN:
        log("  WARNING: captions asked for but native/bin/captions isn't built; skipping captions")
        caps_on = False
    if style["graphics"] and "graphics" not in clip:
        if use_ai:
            try: place_graphics(clip, instructions, cfg)
            except Exception as ex: log(f"  graphics skipped: {str(ex)[:200]}"); clip["graphics"] = []
        else:
            log("  graphics need the AI planner; skipped"); clip["graphics"] = []
    W, H = display_size(clip["src"])
    face = None
    if caps_on or (style["graphics"] and clip.get("graphics")):
        if clip.get("face_src") != clip["src"]:
            clip["face"], clip["face_src"] = face_span(clip["src"]), clip["src"]
        face = clip["face"]
    with tempfile.TemporaryDirectory() as td:
        cards, bands = resolve_graphics(clip, keeps, td, W, H, face) if style["graphics"] else ([], [])
        place = caption_placer(face, style["caption_position"], bands)
        caps = caption_track(clip, keeps, td, place) if caps_on else None
        render_cut(clip["src"], keeps, out, look=clip.get("look"), broll=resolve_broll(clip, keeps),
                   sfx=resolve_sfx(clip, keeps) + card_sfx(cards), loud=style["loudness"], cards=cards,
                   captions=caps)
    clip["retakes"] = count_groups(effective_drop(clip))
    clip.update(kept=kept, out=out)
    return clip

def concat_videos(paths, out):
    """Join already-cut clips in order, fit into the first clip's frame."""
    W, H = display_size(paths[0])
    cmd, fc = ["ffmpeg", "-y", "-filter_complex_threads", CPU_THREADS], []
    for i, p in enumerate(paths):
        cmd += ["-i", p]
        fc.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                  f"setsar=1,fps=30,format=yuv420p[v{i}];[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}];")
    fc.append("".join(f"[v{i}][a{i}]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=1:a=1[v][a]")
    subprocess.run(cmd + ["-filter_complex", "".join(fc), "-map", "[v]", "-map", "[a]",
                          "-c:v", "libx264", "-threads", CPU_THREADS, "-preset", "fast", "-crf", "20",
                          "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out],
                   capture_output=True, check=True)
    return out

# ---------- captions ----------
#
# 2-4 words at a time, drawn as transparent PNGs by the native `captions` tool
# and overlaid by ffmpeg. With Phonon, captions use its real word timings;
# otherwise each whisper run's words are spread across the run by length.

# Phone apps cover the top ~11% and bottom ~28% of a vertical video (and a button
# column on the right), so captions live between these, as a fraction of height.
SAFE_TOP, SAFE_BOTTOM = 0.11, 0.72

def face_span(src):
    """(top, bottom) of where the face sits over the video, 0-1 from the top, or None.
    Uses the 10th/90th percentile edges of every sampled face, so one odd frame
    (a hand, a turn) doesn't move the captions."""
    if not FACES_BIN: return None
    try:
        p = subprocess.run([FACES_BIN, src, "24"], capture_output=True, text=True, timeout=120)
        boxes = json.loads(p.stdout)["boxes"]
    except Exception as ex:
        log(f"  faces failed: {str(ex)[-200:]}"); return None
    boxes = [b for b in boxes if b[2] * b[3] > 0.004]  # skip tiny faces in the background
    if not boxes: return None
    tops, bots = sorted(b[1] for b in boxes), sorted(b[1] + b[3] for b in boxes)
    pick = lambda xs, q: xs[min(len(xs) - 1, int(q * len(xs)))]
    return pick(tops, 0.1), pick(bots, 0.9)

def caption_y(face, position="auto", cap_h=0.07):
    """Where the middle of the caption line goes, 0-1 from the top. "auto": just
    under the chin if that's clear of the covered bottom, else just above the
    head, else the clear spot that covers the least face. "top"/"middle"/"bottom"
    are kept inside the uncovered area."""
    lo, hi = SAFE_TOP + cap_h / 2, SAFE_BOTTOM - cap_h / 2
    if position == "top": return lo + 0.02
    if position == "bottom": return hi
    if position == "middle": return 0.5
    if not face: return hi - 0.04
    top, bot = face
    gap = 0.02
    below, above = bot + gap + cap_h / 2, top - gap - cap_h / 2
    if below <= hi: return below
    if above >= lo: return above
    # no clear spot: the edge whose caption overlaps the face least, leaning to the
    # top, since covering hair or forehead is better than covering the mouth
    over_top, over_bottom = (lo + cap_h / 2) - top, bot - (hi - cap_h / 2)
    return lo if over_top < over_bottom + 0.05 else hi

def caption_chunks(clip, keeps):
    """[(text, start, end)] in output seconds."""
    drop, words = set(effective_drop(clip)), []
    if clip.get("words"):  # Phonon: real timings; skip what was said in a dropped run
        gone = [clip["runs"][k] for k in drop if k < len(clip["runs"])]
        ov = lambda a, b, spans: max((min(b, y) - max(a, x) for x, y in spans), default=0)
        words = [(w, a, b) for w, a, b in clip["words"]  # said mostly in a dropped take: it's gone
                 if not ((g := ov(a, b, gone)) > 0 and (g >= 0.4 * (b - a) or g >= ov(a, b, keeps)))]
    for k, ((ra, rb), text) in enumerate([] if clip.get("words") else zip(clip["runs"], clip["texts"])):
        ws = [w for w in re.sub(r"[\(\[][^)\]]*[\)\]]", " ", text).split() if re.search(r"\w", w)]
        if k in drop or not ws: continue
        a, b = ra + retakes.PAD, rb - retakes.PAD
        total, t = sum(len(w) + 1 for w in ws), a
        for w in ws:
            d = (b - a) * (len(w) + 1) / total
            words.append((w, t, t + d)); t += d
    spans, base = [], 0.0
    for ka, kb in keeps:
        spans.append((ka, kb, base)); base += kb - ka
    timed = []  # (word, out start, out end, which kept span): a word stays if most of it, or 0.15s, survives the cut
    for w, a, b in words:
        best = max(((min(b, kb) - max(a, ka), j) for j, (ka, kb, _) in enumerate(spans)), default=(0, None))
        if best[1] is None or best[0] < min(0.15, 0.5 * (b - a)): continue
        ka, kb, b0 = spans[best[1]]
        timed.append((w, b0 + max(a, ka) - ka, b0 + min(b, kb) - ka, best[1]))
    chunks, cur = [], []
    for i, (w, a, b, j) in enumerate(timed):
        cur.append((w, a, b))
        nxt = timed[i + 1] if i + 1 < len(timed) else None
        chars = sum(len(x) + 1 for x, _, _ in cur)
        if (not nxt or len(cur) >= 3 or chars > 16 or w[-1] in ".,?!;:" or nxt[1] - b > 0.35 or nxt[3] != j):
            text = " ".join(x for x, _, _ in cur).rstrip(".,;:")
            chunks.append([text, cur[0][1], cur[-1][2]]); cur = []
    for c, n in zip(chunks, chunks[1:]):  # hold each one until the next (or 0.3s after it's said)
        c[2] = min(n[1], c[2] + 0.3)
    if chunks: chunks[-1][2] = min(base, chunks[-1][2] + 0.3)
    return [(t, a, b) for t, a, b in chunks if b - a > 0.05]

def caption_track(clip, keeps, td, place=None):
    """Draw the captions and return an ffmpeg concat list that shows each one at
    its time (a blank frame in between). None if there's nothing to caption.
    place(cap_h, start, end) -> where each caption's middle goes (0-1 from the top)."""
    chunks = caption_chunks(clip, keeps)
    if not chunks or not CAPTIONS_BIN: return None
    W, H = display_size(clip["src"])
    ys = [round(place(0.07, a, b), 3) if place else None for _, a, b in chunks]
    files = {}  # chunk index -> png; one run of the tool per caption height
    for y in dict.fromkeys(ys):
        idx = [i for i, v in enumerate(ys) if v == y]
        d = os.path.join(td, f"caps{len(files)}"); os.makedirs(d, exist_ok=True)
        cmd = [CAPTIONS_BIN, str(W), str(H), d] + ([str(y)] if y is not None else [])
        p = subprocess.run(cmd, input=json.dumps([""] + [chunks[i][0] for i in idx]), capture_output=True, text=True)
        if p.returncode:
            log(f"  captions tool failed: {p.stderr[-200:]}"); return None
        files[None] = files.get(None) or os.path.join(d, "0000.png")
        for j, i in enumerate(idx, 1): files[i] = os.path.join(d, f"{j:04d}.png")
    lines, t = [], 0.0
    def show(f, d): lines.append(f"file '{f}'\nduration {max(d, 1/30):.3f}")
    for i, (_, a, b) in enumerate(chunks):
        if a - t > 1/30: show(files[None], a - t)
        show(files[i], b - max(a, t)); t = b
    show(files[None], max(1/30, sum(kb - ka for ka, kb in keeps) - t))
    lines.append(f"file '{files[None]}'")  # concat needs the last file twice
    lst = os.path.join(td, "captions.txt")
    with open(lst, "w") as f: f.write("\n".join(lines) + "\n")
    log(f"  captions: {len(chunks)} on screen")
    return lst

# ---------- time mapping ----------
#
# Placements (b-roll, sfx, graphics) are stored by SOURCE time (what was being
# said), so re-cutting doesn't knock them out of sync. In/out points snap to
# where transcript and waveform agree: the gap between two speech runs.

def out_time(t, keeps):
    """Source second -> second in the edited video (a removed moment maps to the next kept one)."""
    c = 0.0
    for a, b in keeps:
        if t < a: return c
        if t <= b: return c + (t - a)
        c += b - a
    return c

def src_time(t, keeps):
    c = 0.0
    for a, b in keeps:
        if t <= c + (b - a): return a + (t - c)
        c += b - a
    return keeps[-1][1] if keeps else 0.0

def edit_lines(clip, keeps):
    """Kept speech runs as (start, end, text) in edited-video seconds."""
    drop = set(clip.get("drop") or [])
    return [(out_time(a, keeps), out_time(b, keeps), t) for i, ((a, b), t)
            in enumerate(zip(clip["runs"], clip["texts"])) if i not in drop and not unheard(t)]

def pause_points(clip, keeps):
    """Edited-video seconds where a transcribed run ends AND the audio pauses."""
    drop = set(clip.get("drop") or [])
    kept = [r for i, r in enumerate(clip["runs"]) if i not in drop]
    pts = {0.0, sum(b - a for a, b in keeps)}
    for (_, e), (s2, _) in zip(kept, kept[1:]):
        pts.add(out_time((e + s2) / 2, keeps))
    return sorted(pts)

def snap(t, pts, within=.8):
    best = min(pts, key=lambda p: abs(p - t)) if pts else t
    return best if abs(best - t) <= within else t

def rand01(key):
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF

# ---------- b-roll (needs the AI planner) ----------

MAX_ASSETS = 10
MAIN_FRAMES = 12   # stills of the talking video for placement (one per line, spread out)

def prep_asset(path, work):
    """A b-roll item from a file: photos become JPEGs ffmpeg can read (HEIC via sips on macOS)."""
    if os.path.splitext(path)[1].lower() in (".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm"):
        return {"path": path, "kind": "video", "dur": duration_of(path), "name": os.path.basename(path),
                "audio": has_audio(path)}
    jpg = os.path.join(work, f"broll-{hashlib.md5(path.encode()).hexdigest()[:10]}.jpg")
    if shutil.which("sips"):
        subprocess.run(["sips", "-s", "format", "jpeg", "-Z", "2160", path, "--out", jpg],
                       capture_output=True, check=True)
    else:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, jpg], capture_output=True, check=True)
    return {"path": jpg, "kind": "photo", "dur": None, "name": os.path.basename(path)}

# Stills shown to the AI: about one frame per 2s of b-roll, 2 to 8 (one frame
# missed things that were only on screen part of the time; 8 cost a lot more
# for little gain).
def broll_frame_count(dur):
    return max(2, min(8, round((dur or 0) / 2)))

def grab_frame(path, t=None, size=512):
    cmd = ["ffmpeg", "-v", "error", "-y"] + (["-ss", f"{t:.2f}"] if t is not None else [])
    p = subprocess.run(cmd + ["-i", path, "-frames:v", "1", "-vf",
                              f"scale={size}:{size}:force_original_aspect_ratio=decrease",
                              "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", "pipe:1"],
                       capture_output=True, check=True)
    return p.stdout

def asset_stills(a, n=None):
    """Small JPEG frames of a b-roll item (evenly spaced for videos), for the AI to see it."""
    if a["kind"] != "video": return [grab_frame(a["path"])]
    d = a.get("dur") or 0
    n = n or broll_frame_count(d)
    return [grab_frame(a["path"], d * (i + .5) / n) for i in range(n)]

def describe_assets(clip, cfg):
    """Label b-roll items the AI hasn't seen yet (never the talking video)."""
    todo = [a for a in clip.get("assets") or [] if "desc" not in a]
    if not todo: return
    for a, d in zip(todo, ai(cfg, planner.describe_assets, [asset_stills(a) for a in todo])):
        a["desc"] = d
    log(f"  b-roll labels: {[a['desc'] for a in todo]}")

def resolve_broll(clip, keeps):
    """Stored placements -> [(path, kind, start, end, transition, motion, sound)] in edited seconds."""
    assets, total, out, end_prev = clip.get("assets") or [], sum(b - a for a, b in keeps), [], 0.0
    for p in sorted(clip.get("broll") or [], key=lambda p: p["src"]):
        if not 0 <= p["asset"] < len(assets): continue
        a = assets[p["asset"]]
        if not os.path.exists(a["path"]): continue
        s0 = max(out_time(p["src"], keeps), end_prev, 1.0)       # never over the hook
        e0 = min(s0 + p["dur"], total - .05)
        if a["kind"] == "video" and a.get("dur"): e0 = min(e0, s0 + a["dur"])
        if e0 - s0 < .8: continue
        out.append((a["path"], a["kind"], s0, e0, p.get("transition", "cut"), p.get("motion", "none"),
                    p.get("audio", "mute") == "mix" and bool(a.get("audio"))))
        end_prev = e0
    return out

def place_broll(clip, instructions, cfg):
    """Ask the planner where the b-roll goes. Returns its note ("" if none)."""
    keeps = edit_keeps(clip)
    total = sum(b - a for a, b in keeps)
    assets = clip.get("assets") or []
    lines, pts = edit_lines(clip, keeps), pause_points(clip, keeps)
    describe_assets(clip, cfg)
    current = [(p["asset"] + 1, out_time(p["src"], keeps), out_time(p["src"], keeps) + p["dur"],
                p.get("transition", "cut"), p.get("motion", "none"), p.get("audio", "mute"))
               for p in clip.get("broll") or []]
    # what's on screen during the talking video, so b-roll doesn't hide
    # something they're showing the camera
    pick = sorted({round(i * (len(lines) - 1) / max(1, MAIN_FRAMES - 1)) for i in range(MAIN_FRAMES)}) if lines else []
    shots = []
    for i in pick:
        a, b, _ = lines[i]
        try: shots.append((i, grab_frame(clip["src"], src_time((a + b) / 2, keeps), 384)))
        except Exception: pass
    plc, note = ai(cfg, planner.plan_broll, lines, total, assets, instructions, current, shots)
    placed = []
    for k, a, b, transition, motion, audio in plc:
        a, b = snap(a, pts), snap(b, pts)
        if b - a < .8: b = a + 2.0
        placed.append({"asset": k - 1, "src": src_time(a, keeps), "dur": min(b - a, 6.0),
                       "transition": transition, "motion": motion,
                       "audio": audio if assets[k - 1].get("audio") else "mute"})
    clip["broll"] = placed
    log(f"  b-roll: {[(p['asset'] + 1, round(p['src'], 1), round(p['dur'], 1), p['transition'], p['motion']) for p in placed]}")
    return note

# ---------- sound effects (needs the AI planner and a sound library) ----------
#
# The library is described by assets/sfx/sfx.json; the audio files themselves
# are NOT shipped (see assets/sfx/README.md). Roles whose file is missing are
# left out of the library the planner sees.

SFX_DIR = os.environ.get("CUTBOT_SFX_DIR") or os.path.join(ASSETS, "sfx")

def _load_sfx():
    try: lib = json.load(open(os.path.join(ASSETS, "sfx", "sfx.json")))
    except (OSError, ValueError): return {}
    return {x["role"]: x for x in lib if os.path.isfile(os.path.join(SFX_DIR, x["file"]))}

SFX = _load_sfx()

def sfx_anchors(clip, keeps):
    """Edited-video seconds a hit can land on: where speech starts or ends and where b-roll cuts in."""
    pts = set()
    for a, b, _ in edit_lines(clip, keeps): pts |= {a, b}
    for _, _, a, *_ in resolve_broll(clip, keeps): pts.add(a)
    return sorted(pts)

def place_sfx(clip, instructions, cfg):
    """Ask the planner for sound effects; stored by source time so re-cuts keep them on cue."""
    if not SFX:
        log(f"  no sound effect files in {SFX_DIR}; skipping sound effects"); return ""
    keeps = edit_keeps(clip)
    total = sum(b - a for a, b in keeps)
    assets = clip.get("assets") or []
    shots = [(a, b, next((x.get("desc", "") for x in assets if x["path"] == path), ""))
             for path, _, a, b, *_ in resolve_broll(clip, keeps)]
    current = [(e["role"], out_time(e["src"], keeps), e["level"]) for e in clip.get("sfx") or []]
    lib = [{"role": r, "use": x["use"]} for r, x in SFX.items()]
    events, note = ai(cfg, planner.plan_sfx, edit_lines(clip, keeps), total, shots, lib, instructions, current)
    pts = sfx_anchors(clip, keeps)
    clip["sfx"] = [{"role": r, "src": src_time(snap(t, pts, .5), keeps), "level": lv}
                   for r, t, lv in sorted(events, key=lambda e: e[1]) if 0 <= t < total]
    log(f"  sfx: {[(e['role'], round(out_time(e['src'], keeps), 2), e['level']) for e in clip['sfx']]}")
    return note

def resolve_sfx(clip, keeps):
    """Stored hits -> [(wav, start, gain_db, rate)], with a little pitch/timing
    variation so repeats don't sound looped."""
    total, out = sum(b - a for a, b in keeps), []
    for i, e in enumerate(clip.get("sfx") or []):
        x = SFX.get(e["role"])
        if not x: continue
        r = rand01(f"{e['role']}{i}{e['src']:.2f}")
        t = max(0.0, out_time(e["src"], keeps) + (r - .5) * .04)       # +-20ms
        if t >= total - .1: continue
        out.append((os.path.join(SFX_DIR, x["file"]), t,
                    x["gain_db"] - (4 if e.get("level") == "soft" else 0),
                    .97 + .06 * rand01(f"p{i}{e['role']}")))           # +-3% pitch
    return out

# ---------- graphics (EXPERIMENTAL; needs the AI planner and native/bin/cards) ----------
#
# The planner picks the beats worth seeing (only facts that were said), writes a
# canvas scene for each one against a small runtime and a design rulebook
# (assets/graphics), and a vision check grades stills of the card; failures go
# back with fixes, at most twice, then the beat is dropped (no graphic beats a
# bad one). The native `cards` tool renders each scene in WebKit with all
# network blocked. Cards sit above the speaker's head; on vertical video a few
# beats can be full-screen cutaways. This costs many model calls per video.

GFX_DIR = os.path.join(ASSETS, "graphics")
THREE_JS = os.path.join(ASSETS, "graphics3d", "three.min.js")
GFX_TRIES = 3          # first try + 2 fixes
NUM_WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                             "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUM_WORDS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
                  "ninety": "90", "hundred": "100", "thousand": "1000"})

def said_numbers(text):
    """Every number said, as digits ('3.5', '1200', and 'three' -> '3')."""
    t = text.lower().replace(",", "")
    nums = set(re.findall(r"\d+(?:\.\d+)?", t))
    nums |= {NUM_WORDS[w] for w in re.findall(r"[a-z]+", t) if w in NUM_WORDS}
    return nums

def gfx_kit():
    rd = lambda f: open(os.path.join(GFX_DIR, f)).read()
    ex = sorted(os.listdir(os.path.join(GFX_DIR, "examples")))
    return {"rulebook": rd("RULEBOOK.md"), "runtime": rd("runtime.js"), "check": rd("check.md"),
            "examples": [rd(os.path.join("examples", f)) for f in ex if f.endswith(".js")]}

def scene_page(kit, code, path):
    """One self-contained page: three.js (only if the scene uses it), runtime, scene."""
    code = re.sub(r"</(script)", r"<\\/\1", code, flags=re.I)  # a literal </script> would end the tag
    three = ""
    if os.path.isfile(THREE_JS) and re.search(r"\bTHREE\b|render3d\(", code):
        three = "<script>" + open(THREE_JS).read() + "</script>"
    with open(path, "w") as f:
        f.write('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#141416}</style>'
                '<canvas id="c"></canvas>' + three + '<script>\n' + kit["runtime"] + "\n" + code + "\n</script>\n")

def render_scene(kit, code, params, d, times, timeout=180):
    """Frames of one scene at `times` -> d/0000.png...; returns an error string or ''."""
    os.makedirs(d, exist_ok=True)
    page = os.path.join(d, "scene.html")
    scene_page(kit, code, page)
    try:
        p = subprocess.run([CARDS_BIN, page, json.dumps(params), d, ",".join(f"{t:.4f}" for t in times)],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "rendering timed out"
    return p.stderr.strip()[-400:] or "render failed" if p.returncode else ""

def beat_words(clip, keeps, a, b):
    """[(seconds from the beat start, word)] said in the beat (Phonon timings)."""
    out = []
    for w, ws, we in clip.get("words") or []:
        t = out_time(ws, keeps)
        if a - .05 <= t < b: out.append((round(t - a, 2), w))
    return out

def scene_params(placement, dur, accent):
    p = {"DUR": round(dur, 2), "ACCENT": accent or "#0A84FF"}
    if placement == "full": p["MODE"] = "full"
    return p

def _jpeg(png, jpg, full):
    if shutil.which("sips"):
        subprocess.run(["sips", "-s", "format", "jpeg", *(["-Z", "1280"] if full else []), png, "--out", jpg],
                       capture_output=True)
    else:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", png, jpg], capture_output=True)

def make_scene(cfg, kit, beat, words, dur, accent, td, before=None):
    """Write, render and check one beat's scene; the code, or None if it never passed.
    before: (code, dur) of the full-screen beat this one continues."""
    code, issues = None, []
    full = beat.get("placement") == "full"
    params = scene_params(beat.get("placement"), dur, accent)
    for k in range(GFX_TRIES):
        code = ai(cfg, planner.write_scene, kit, beat, words, dur, accent, code, issues, before)
        if not code or "function draw" not in code:
            issues = [{"t": 0, "severity": "fail", "problem": "no draw(t) function", "fix": "define function draw(t)"}]
            continue
        # a full-screen cut must read from its first frame, so that one is checked too
        times = [0.0 if full else min(.8, dur * .3), dur / 2, max(0, dur - .1)]
        d = os.path.join(td, f"try{k}")
        err = render_scene(kit, code, params, d, times, timeout=90 if full else 60)
        if err:
            issues = [{"t": 0, "severity": "fail", "problem": f"the scene threw an error: {err}",
                       "fix": "fix the JavaScript error"}]
            continue
        # the runtime's own text audit catches tiny or misplaced text before a vision call
        min_fs = 22 if full else 13
        issues = []
        for i, t in enumerate(times):
            try: au = json.load(open(os.path.join(d, f"{i:04d}.json")))
            except (OSError, ValueError): continue
            if au.get("minPx") is not None and au["minPx"] < min_fs - .5:
                issues.append({"t": t, "severity": "fail", "problem": f"text drawn at {au['minPx']:.1f}px, under the {min_fs}px minimum",
                               "fix": f"make every text at least {min_fs}px (use fit()/label())"})
            if au.get("outside"):
                issues.append({"t": t, "severity": "fail", "problem": f"text outside SAFE: {str(au['outside'])[:160]}",
                               "fix": "keep all text inside SAFE"})
        if issues:
            log(f"  graphic at {beat['start']:.1f}s, try {k + 1}: text audit failed"); continue
        stills = []
        for i, t in enumerate(times):
            jpg = os.path.join(d, f"{i:04d}.jpg")
            _jpeg(os.path.join(d, f"{i:04d}.png"), jpg, full)
            stills.append((t, open(jpg, "rb").read()))
        verdict = ai(cfg, planner.check_scene, kit["check"], beat, stills)
        if verdict["pass"]: return code
        issues = [i for i in verdict["issues"] if i["severity"] != "nit"] or verdict["issues"]
        log(f"  graphic at {beat['start']:.1f}s, try {k + 1}: score {verdict['score']}, "
            f"{sum(i['severity'] == 'fail' for i in verdict['issues'])} fail(s)")
    return None

def place_graphics(clip, instructions, cfg):
    """Plan the beats, then write and check a scene for each (in parallel)."""
    if not CARDS_BIN:
        log("  graphics need native/bin/cards (see README); skipped"); clip["graphics"] = []; return ""
    keeps = edit_keeps(clip)
    total = sum(b - a for a, b in keeps)
    lines = edit_lines(clip, keeps)
    current = [dict(g, start=out_time(g["src"], keeps), end=out_time(g["src_end"], keeps))
               for g in clip.get("graphics") or []]
    W, H = display_size(clip["src"])
    full_ok = H >= 1.6 * W  # full-screen scenes are 9:16
    broll = [(a, b) for _, _, a, b, *_ in resolve_broll(clip, keeps)]
    beats, note = ai(cfg, planner.plan_graphics, lines, total, instructions, current, full_ok, broll)
    said = said_numbers(" ".join(t for _, _, t in lines))
    starts = [a for a, _, _ in lines]
    todo, last = [], -1.0
    for bt in sorted(beats, key=lambda x: x["start"]):
        if not said_numbers(bt["show"] + " " + bt["said"]) <= said:
            log(f"  graphic skipped, a number in it wasn't said: {bt['start']:.1f}s"); continue
        a = snap(float(bt["start"]), starts, .6)
        b = min(max(float(bt["end"]), a + 2.0), a + 6.0, total - .2)
        if a < last + .8 or b - a < 1.5: continue
        if any(a < be and b > ba for ba, be in broll):
            log(f"  graphic skipped, b-roll is on screen: {a:.1f}s"); continue
        accent = (bt.get("accent") or "").strip()
        accent = ("#" + accent.lstrip("#")) if re.fullmatch(r"#?[0-9a-fA-F]{6}", accent) else "#0A84FF"
        placement = "full" if bt.get("placement") == "full" and full_ok else "card"
        todo.append((dict(bt, start=a, end=b, placement=placement), accent)); last = b
    kit = gfx_kit()
    def one(item, before=None):
        try: return one_beat(item, before)
        except Exception as ex:  # one beat failing never costs the edit
            log(f"  graphic at {item[0]['start']:.1f}s dropped: {str(ex)[:160]}"); return None
    def one_beat(item, before):
        bt, accent = item
        with tempfile.TemporaryDirectory() as td:
            code = make_scene(cfg, kit, bt, beat_words(clip, keeps, bt["start"], bt["end"]),
                              bt["end"] - bt["start"], accent, td, before)
        if not code:
            log(f"  graphic at {bt['start']:.1f}s dropped: never passed the check"); return None
        return {"show": bt["show"], "said": bt["said"], "accent": accent, "code": code,
                "placement": bt["placement"], "dur": round(bt["end"] - bt["start"], 2),
                "src": src_time(bt["start"], keeps), "src_end": src_time(bt["end"], keeps)}
    def full_chain(items):
        """Full-screen beats in order: one right after another full beat grows out of its last frame."""
        out, before = [], None
        for i, item in items:
            follows = i > 0 and todo[i - 1][0]["placement"] == "full" and item[0]["start"] - todo[i - 1][0]["end"] < 1.5
            g = one(item, before if follows else None)
            out.append((i, g))
            before = (g["code"], g["dur"]) if g else None
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        fulls = [(i, it) for i, it in enumerate(todo) if it[0]["placement"] == "full"]
        chain = pool.submit(full_chain, fulls)
        jobs = [(i, pool.submit(one, it)) for i, it in enumerate(todo) if it[0]["placement"] != "full"]
        got = dict(chain.result())
        got.update({i: f.result() for i, f in jobs})
    made = [got[i] for i in sorted(got) if got[i]]
    clip["graphics"] = made
    log(f"  graphics: {len(made)} of {len(todo)} beats at "
        f"{[(round(out_time(g['src'], keeps), 1), g['placement']) for g in made]}")
    return note

def card_box(face, W, H):
    """(x, y, w) in pixels for a card (760x400 shape): just under the top band phone
    apps cover, ending above the eyebrows; if there's no room there, a lower band
    above the covered bottom when the face ends above it, else the top anyway
    (better the forehead than the mouth). Kept clear of the right button column."""
    vertical = H > W
    w = W * (0.70 if vertical else 0.36)
    top = (SAFE_TOP + 0.01) * H if vertical else 0.06 * H
    if face:
        brows = (face[0] + 0.30 * (face[1] - face[0])) * H
        if top + w * 400 / 760 > brows:
            fit = (brows - top) * 760 / 400
            if fit >= W * 0.5: w = fit
            elif vertical and (face[1] + 0.02) * H <= SAFE_BOTTOM * H - w * 400 / 760:
                top = SAFE_BOTTOM * H - w * 400 / 760
    x = ((W - 0.07 * W) - w) / 2 if vertical else (W - w) / 2
    ev = lambda v: int(v) // 2 * 2
    return ev(x), ev(top), ev(w)

def resolve_graphics(clip, keeps, td, W, H, face):
    """Stored scenes -> ([(concat list, start, end, x, y, w, placement)], [(start, end, top, bottom) as 0-1]).
    Every frame is rendered at 30fps. A full-screen scene fills the frame (x, y, w = 0, 0, W);
    its band is the safe area, so captions drop under the drawing."""
    total, cards, bands = sum(b - a for a, b in keeps), [], []
    if not CARDS_BIN: return cards, bands
    x, y, w = card_box(face, W, H)
    kit = gfx_kit()
    for i, g in enumerate(clip.get("graphics") or []):
        a, b = out_time(g["src"], keeps), min(out_time(g["src_end"], keeps), total - .05)
        if b - a < 1 or not g.get("code"): continue
        full = g.get("placement") == "full" and H >= 1.6 * W
        d = os.path.join(td, f"card{i}")
        n = max(2, int((b - a) * 30) + 1)
        err = render_scene(kit, g["code"], scene_params("full" if full else "card", b - a, g.get("accent")),
                           d, [k / 30 for k in range(n)], timeout=600 if full else 180)
        if err:
            log(f"  graphic {i} failed to render: {err[-200:]}"); continue
        lst = os.path.join(d, "frames.txt")
        with open(lst, "w") as f:
            for k in range(n):
                f.write(f"file '{os.path.join(d, f'{k:04d}.png')}'\nduration {1 / 30:.4f}\n")
            f.write(f"file '{os.path.join(d, f'{n - 1:04d}.png')}'\n")
        if full:
            cards.append((lst, a, b, 0, 0, W, "full"))
            bands.append((a, b, SAFE_TOP, SAFE_BOTTOM - .02))
        else:
            cards.append((lst, a, b, x, y, w, "card"))
            bands.append((a, b, y / H, (y + w * 400 / 760) / H))
    if cards: log(f"  graphics: {len(cards)} on screen ({sum(c[-1] == 'full' for c in cards)} full), cards at x{x} y{y} w{w}")
    return cards, bands

def card_sfx(cards):
    """A soft click as each card comes in (full-screen cutaways leave the audio alone)."""
    x = SFX.get("click-ui")
    if not x: return []
    return [(os.path.join(SFX_DIR, x["file"]), c[1], x["gain_db"] - 6, .97 + .06 * rand01(f"card{i}"))
            for i, c in enumerate(cards) if c[-1] != "full"]

# ---------- top level ----------

def _apply(style, overrides):
    st = planner.clean_style(style)
    st.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return planner.clean_style(st)

def _extras(clip, instructions, cfg, use_ai, broll, wants_sfx, work):
    """B-roll and sound effects on an already-planned clip. Returns notes."""
    notes = []
    clip["assets"] = [prep_asset(os.path.abspath(p), work) for p in (broll or [])[:MAX_ASSETS]]
    clip["broll"], clip["sfx"] = [], []
    if clip["assets"]:
        if use_ai:
            try: notes.append(place_broll(clip, instructions, cfg))
            except AIError as ex: log(f"  b-roll skipped: {ex}")
        else:
            log("  b-roll placement needs the AI planner; skipped")
    if wants_sfx:
        if use_ai:
            try: notes.append(place_sfx(clip, instructions, cfg))
            except AIError as ex: log(f"  sound effects skipped: {ex}")
        else:
            log("  sound effects need the AI planner; skipped")
    return [n for n in notes if n]

def edit(src, out, cfg=None, instructions="", use_ai=True, style=None, broll=(), sfx=None, use_phonon=True):
    """Edit one video file. style: explicit style options (pace, fillers, captions, graphics,
    loudness, caption_position) that override whatever the planner picks; None values are
    left to the planner. sfx: True/False to force sound effects, None to let the planner decide.
    Returns a summary dict."""
    cfg = cfg or load_config()
    use_ai = use_ai and planner.enabled(cfg)
    t0 = time.time()
    src = os.path.abspath(src)
    log(f"analyzing {src}")
    clip = analyze(src, use_phonon)
    log(f"  {len(clip['runs'])} speech runs, heuristic suggests dropping {clip['hint'] or 'none'}")
    drop, note, look, wants_sfx, st = decide(clip, instructions, cfg, use_ai, _apply(None, style))
    clip.update(drop=drop, note=note, look=look, style=_apply(st, style))
    for k in clip["drop"]:
        log(f"  drop run {k}: {clip['texts'][k]!r}")
    with tempfile.TemporaryDirectory() as work:
        notes = [note] if note else []
        notes += _extras(clip, instructions, cfg, use_ai, broll, wants_sfx if sfx is None else sfx, work)
        log("rendering")
        render(clip, os.path.abspath(out), cfg, instructions, use_ai)
    return {"out": clip["out"], "dur": round(clip["dur"], 2), "kept": round(clip["kept"], 2),
            "retakes": clip["retakes"], "drop": effective_drop(clip), "ai": use_ai, "notes": notes,
            "style": clip["style"], "secs": round(time.time() - t0, 1), "_clip": clip}

def edit_combined(srcs, out, cfg=None, instructions="", use_ai=True, style=None, use_phonon=True):
    """Edit several clips as one video, joined in the order given. With AI, retakes are
    found across clips (a line redone in the next clip drops the earlier take)."""
    cfg = cfg or load_config()
    use_ai = use_ai and planner.enabled(cfg)
    t0 = time.time()
    clips = []
    for s in srcs:
        log(f"analyzing {s}")
        clips.append(analyze(os.path.abspath(s), use_phonon))
    runs, texts, raw, owner, t0_clip = [], [], [], [], 0.0
    for ci, c in enumerate(clips):
        runs += [[a + t0_clip, b + t0_clip] for a, b in c["runs"]]  # one shared timeline
        texts += [f"(clip {ci + 1}) {t}" for t in c["texts"]]
        raw += c["texts"]
        owner += [(ci, i) for i in range(len(c["runs"]))]
        t0_clip += c["dur"]
    # the heuristic over every clip at once, so a line redone in the next clip counts as a retake
    hint = retakes.heuristic_drop(retakes.find_retakes(runs, raw))
    base_style = _apply(None, style)
    drop, note, look, st = hint, "", planner.clean_look(None), base_style
    if use_ai and runs:
        try: drop, note, look, _, st = ai(cfg, planner.plan_batch, runs, texts, hint, len(clips), instructions, base_style)
        except AIError as ex: log(f"  WARNING: AI planner unavailable ({ex}); using the heuristic retake cut")
    st = _apply(st, style)
    with tempfile.TemporaryDirectory() as work:
        parts = []
        for ci, c in enumerate(clips):
            c.update(look=look, style=st, broll=[], sfx=[], assets=[], note="",
                     drop=keep_unheard([i for g, (cj, i) in enumerate(owner) if cj == ci and g in drop], c["texts"]))
            part = os.path.join(work, f"part{ci + 1}.mp4")
            log(f"rendering part {ci + 1}/{len(clips)}")
            render(c, part, cfg, instructions, use_ai)
            parts.append(part)
        log("joining parts")
        concat_videos(parts, os.path.abspath(out))
    return {"out": os.path.abspath(out), "dur": round(sum(c["dur"] for c in clips), 2),
            "kept": round(sum(c["kept"] for c in clips), 2), "retakes": sum(c["retakes"] for c in clips),
            "ai": use_ai, "notes": [note] if note else [], "style": st, "secs": round(time.time() - t0, 1)}

def dump(clip, path):
    """Write the run's analysis and decisions as JSON (for debugging cuts and captions)."""
    keeps = edit_keeps(clip)
    data = {k: clip.get(k) for k in ("src", "dur", "kept", "runs", "texts", "hint", "drop", "sil", "words", "style", "look")}
    data["keeps"] = keeps
    data["captions"] = caption_chunks(clip, keeps) if clip["runs"] else []
    with open(path, "w") as f:
        json.dump(data, f, indent=1)
