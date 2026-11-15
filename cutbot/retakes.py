"""Retake detection: when a line is said more than once, keep the LAST take.

    python -m cutbot.retakes CLIP.mov   print speech runs, transcripts, and what gets dropped

Why not transcribe the whole clip once: whisper normalises disfluencies. Given
two attempts at a line it emits one clean phrase, so the repeat is invisible in
the transcript. Instead, split on the audio's own pauses, transcribe each
speech run as its OWN input (whisper can't merge across inputs), and compare.

A stretch of runs is an abandoned take when the speech right after it opens
with (nearly) the same words. That covers false starts ("so the reason... so
the reason that I...") and full-line redos alike. Chains collapse naturally, so
a line attempted three times keeps only the third.

This module is the no-AI path: its suggestions are used as-is with --no-ai, and
passed to the AI planner as hints otherwise.

Configuration (environment):
  CUTBOT_WHISPER_CLI     whisper.cpp binary (default: whisper-cli on PATH)
  CUTBOT_WHISPER_MODEL   ggml model file (default: models/ggml-base.en.bin in this repo)
  CUTBOT_WHISPER_PROMPT  initial prompt that steers spelling of names (default: VOCAB below)
"""
import json, os, re, shutil, subprocess, sys, tempfile, wave
from difflib import SequenceMatcher

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Names whisper otherwise mishears ("clawed", "chat gbt"): a short initial prompt
# steers its spelling without changing what it hears. Tuned for tech/AI talk;
# override with CUTBOT_WHISPER_PROMPT for other subjects.
VOCAB = os.environ.get("CUTBOT_WHISPER_PROMPT") or (
    "Talking about AI and apps: Claude, Anthropic, ChatGPT, GPT-5, OpenAI, Gemini, Codex, "
    "Cursor, Copilot, Midjourney, CapCut, iMessage, TikTok, Instagram, YouTube.")
WHISPER_CLI = os.environ.get("CUTBOT_WHISPER_CLI") or "whisper-cli"
MODEL = os.environ.get("CUTBOT_WHISPER_MODEL") or os.path.join(_ROOT, "models", "ggml-base.en.bin")
THREADS = str(max(2, (os.cpu_count() or 4) // 2))
NOISE, MIN_GAP, PAD = "-30dB", 0.18, 0.04   # run splitting (finer than dead-air cutting)
SIM = 0.6                 # word-level similarity for "same line"
MAX_SPAN_WORDS = 45       # longest abandoned take considered
MAX_SPAN_SECS = 25.0
MAX_SKIP_WORDS = 4        # a short aside between takes ("wait", "ugh") goes too

def whisper_problem():
    """Why transcription can't run, or "" if whisper-cli and the model are both there."""
    if not (shutil.which(WHISPER_CLI) or os.access(WHISPER_CLI, os.X_OK)):
        return f"whisper-cli not found ({WHISPER_CLI!r}); install whisper.cpp or set CUTBOT_WHISPER_CLI"
    if not os.path.isfile(MODEL):
        return f"whisper model not found at {MODEL}; download ggml-base.en.bin or set CUTBOT_WHISPER_MODEL"
    return ""

def speech_runs(path, dur):
    p = subprocess.run(["ffmpeg","-hide_banner","-nostats","-i",path,"-vn",
                        "-af",f"silencedetect=noise={NOISE}:d={MIN_GAP}","-f","null","-"],
                       capture_output=True, text=True)
    runs, cursor = [], 0.0
    for kind, t in re.findall(r"silence_(start|end):\s*(-?[\d.]+)", p.stderr):
        t = max(0.0, float(t))
        if kind == "start":
            if t - cursor > 0.25: runs.append([cursor, t])
        else:
            cursor = t
    if dur - cursor > 0.25: runs.append([cursor, dur])
    return [[max(0.0, a - PAD), min(dur, b + PAD)] for a, b in runs]

def transcribe_runs(path, runs):
    """One whisper-cli call (model loads once) over every run as its own file."""
    if not runs: return []
    with tempfile.TemporaryDirectory() as td:
        full = os.path.join(td, "full.wav")
        subprocess.run(["ffmpeg","-y","-v","error","-i",path,"-vn","-ac","1","-ar","16000",
                        "-c:a","pcm_s16le", full], check=True, capture_output=True)
        with wave.open(full) as w:
            rate, pcm = w.getframerate(), w.readframes(w.getnframes())
        files = []
        for i, (a, b) in enumerate(runs):
            f = os.path.join(td, f"run{i:04d}.wav")
            with wave.open(f, "wb") as o:
                o.setnchannels(1); o.setsampwidth(2); o.setframerate(rate)
                o.writeframes(pcm[int(a*rate)*2:int(b*rate)*2])
            files.append(f)
        cmd = [WHISPER_CLI,"-m",MODEL,"-np","-nt","-oj","--prompt",VOCAB,"-t",THREADS]
        for f in files: cmd += ["-f", f]
        subprocess.run(cmd, check=True, capture_output=True)
        texts = []
        for f in files:
            try:
                d = json.load(open(f + ".json"))
                texts.append(" ".join(s["text"].strip() for s in d.get("transcription", [])))
            except FileNotFoundError:
                texts.append("")
        return texts

def norm(s):
    # drop whisper annotations like "(mumbles)", "[BLANK_AUDIO]"
    s = re.sub(r"[\(\[][^)\]]*[\)\]]", " ", s.lower())
    return re.sub(r"[^a-z0-9' ]", "", s).split()

def same_line(span, follow):
    """How strongly does `follow` open with the line in `span`? 0..1"""
    if len(span) < 2 or len(follow) < 2: return 0.0
    head = follow[:len(span)]
    if len(head) < len(span) * 0.8: return 0.0
    score = SequenceMatcher(None, span, head).ratio()
    # a restart sounds like the same opening words; keeps merely-similar
    # sentences ("number one is…" / "number two is…") from matching
    if span[:2] != head[:2] and SequenceMatcher(None, span[:3], head[:3]).ratio() < 0.7:
        score *= 0.5
    return score

def find_retakes(runs, texts):
    """Returns [(first_run, kept_run, score)]: runs first_run..kept_run-1 are
    abandoned attempts at the line that starts at kept_run."""
    W = [norm(t) for t in texts]
    n, out, i = len(runs), [], 0
    while i < n - 1:
        best, span = None, []
        for k in range(i, n - 1):
            span = span + W[k]
            if len(span) > MAX_SPAN_WORDS or runs[k][1] - runs[i][0] > MAX_SPAN_SECS: break
            for j in (k + 1, k + 2):
                if j >= n or (j == k + 2 and len(W[k + 1]) > MAX_SKIP_WORDS): break
                follow = [w for r in W[j:] for w in r][:len(span) + 2]
                s = same_line(span, follow)
                if s >= SIM and (best is None or s > best[2] + 0.05 or
                                 (s >= best[2] - 0.05 and j > best[1])):
                    best = (i, j, s)
        if best:
            out.append(best)
            i = best[1]       # the kept take may itself be redone later
        else:
            i += 1
    return out

def heuristic_drop(found):
    """Run indices the heuristic would remove."""
    return sorted({k for i, j, _ in found for k in range(i, j)})

def run_drop_ranges(runs, drop, dur):
    """Source-time ranges covering the given run indices, cut mid-pause."""
    out = []
    for k in sorted(drop):
        a = 0.0 if k == 0 else (runs[k-1][1] + runs[k][0]) / 2
        b = dur if k == len(runs) - 1 else (runs[k][1] + runs[k+1][0]) / 2
        if out and a <= out[-1][1] + 1e-6: out[-1][1] = b
        else: out.append([a, b])
    return out

def analyze(path, dur):
    """Runs, per-run transcripts, and the heuristic's suggested drops."""
    runs = speech_runs(path, dur)
    texts = transcribe_runs(path, runs)
    return runs, texts, heuristic_drop(find_retakes(runs, texts))

def drop_ranges(runs, found, dur):
    """Source-time ranges to remove, cut at the middle of the surrounding pauses."""
    def gap_mid(j):
        return 0.0 if j == 0 else (runs[j-1][1] + runs[j][0]) / 2
    return [[gap_mid(i), gap_mid(j)] for i, j, _ in found]

def detect(path, dur):
    """Returns (drop ranges, details, runs, texts): the standalone report below."""
    runs = speech_runs(path, dur)
    texts = transcribe_runs(path, runs)
    found = find_retakes(runs, texts)
    details = [{"from": round(runs[i][0], 2), "to": round(runs[j][0], 2),
                "dropped": " | ".join(texts[i:j]), "kept": texts[j], "score": round(s, 2)}
               for i, j, s in found]
    return drop_ranges(runs, found, dur), details, runs, texts

if __name__ == "__main__":
    src = sys.argv[1]
    dur = float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
                                "-of","csv=p=0", src], capture_output=True, text=True).stdout)
    drops, details, runs, texts = detect(src, dur)
    for (a, b), t in zip(runs, texts):
        tag = "DROP" if any(x <= (a+b)/2 < y for x, y in drops) else "    "
        print(f"{tag} {a:6.2f}-{b:6.2f}  {t}")
    print(f"\n{len(details)} retake(s)")
    for d in details:
        print(f"  {d['from']:.2f}-{d['to']:.2f} (score {d['score']})\n    dropped: {d['dropped']!r}\n    kept   : {d['kept']!r}")
