"""Command line: python -m cutbot INPUT [-o OUTPUT] [options]"""
import argparse, json, os, sys

from . import engine, planner, retakes

EPILOG = """examples:
  python -m cutbot take.mov                       retakes + dead air cut (AI if OPENAI_API_KEY is set)
  python -m cutbot take.mov --no-ai -o out.mp4    heuristic retake cut only, no network
  python -m cutbot take.mov --captions --pace tight
  python -m cutbot take.mov -i "keep the first take of the intro, make it a bit warmer"
  python -m cutbot take.mov --broll gym.mov --broll latte.jpg -i "gym clip when I talk about training"
  python -m cutbot part1.mov part2.mov --combine -o full.mp4

environment:
  OPENAI_API_KEY         enables the AI planner (otherwise --no-ai is implied)
  CUTBOT_OPENAI_MODEL    model for planning (default %s)
  CUTBOT_WHISPER_MODEL   whisper.cpp ggml model (default models/ggml-base.en.bin)
  CUTBOT_WHISPER_CLI     whisper.cpp binary (default whisper-cli on PATH)
  CUTBOT_FERMION         optional Phonon-2 `fermion` binary (auto-detected on PATH)
  CUTBOT_BIN_DIR         where the native helpers are built (default native/bin)
  CUTBOT_SFX_DIR         folder with the sound effect .wav files (default cutbot/assets/sfx)
""" % planner.DEFAULT_MODEL

def tri(p, name, help_on, help_off):
    g = p.add_mutually_exclusive_group()
    g.add_argument(f"--{name}", dest=name.replace("-", "_"), action="store_true", default=None, help=help_on)
    g.add_argument(f"--no-{name}", dest=name.replace("-", "_"), action="store_false", help=help_off)

def main(argv=None):
    p = argparse.ArgumentParser(prog="cutbot", description="Cut dead air and retakes out of talking-head videos.",
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inputs", nargs="+", metavar="INPUT", help="video file(s) to edit")
    p.add_argument("-o", "--output", help="output file (default: INPUT.cut.mp4 next to the input)")
    p.add_argument("-i", "--instructions", default="", help="free-form editing request for the AI planner")
    p.add_argument("--no-ai", action="store_true", help="don't call the AI: heuristic retake detection only")
    p.add_argument("--model", help="OpenAI model for planning")
    p.add_argument("--combine", action="store_true", help="join several inputs into one video, in order")
    p.add_argument("--pace", choices=["loose", "normal", "tight"], help="how much pause to leave between sentences")
    tri(p, "captions", "burn in captions (needs native/bin/captions)", "no captions")
    p.add_argument("--caption-position", choices=["auto", "top", "middle", "bottom"],
                   help="caption placement (auto keeps them off the face; needs native/bin/faces)")
    tri(p, "fillers", "also cut 'um'/'uh' said on their own", "leave fillers in")
    tri(p, "loudness", "even out the volume (default on)", "leave the audio level untouched")
    tri(p, "graphics", "EXPERIMENTAL: AI-drawn animated graphics (needs AI + native/bin/cards)", "no graphics")
    tri(p, "sfx", "add sound effects (needs AI + sound files, see cutbot/assets/sfx)", "no sound effects")
    p.add_argument("--broll", action="append", default=[], metavar="FILE",
                   help="your own b-roll clip or photo to place over the video (repeatable; needs AI)")
    p.add_argument("--no-phonon", action="store_true", help="don't use Phonon-2 even if `fermion` is installed")
    p.add_argument("--whisper-model", help="path to a whisper.cpp ggml model")
    p.add_argument("--dump", metavar="JSON", help="write runs, transcripts, drops and cut ranges to this file")
    p.add_argument("-q", "--quiet", action="store_true", help="only print the result")
    a = p.parse_args(argv)

    engine.VERBOSE = not a.quiet
    if a.whisper_model: retakes.MODEL = os.path.abspath(os.path.expanduser(a.whisper_model))
    for f in a.inputs + a.broll:
        if not os.path.isfile(f): p.error(f"no such file: {f}")
    if len(a.inputs) > 1 and not a.combine and a.output:
        p.error("several inputs without --combine are written next to each input; drop -o or add --combine")
    if a.combine and a.broll:
        p.error("--broll works on a single input only")

    cfg = engine.load_config(openai_model=a.model)
    use_ai = not a.no_ai
    if use_ai and not planner.enabled(cfg):
        engine.log("OPENAI_API_KEY not set: using the heuristic retake cut (same as --no-ai)")
        use_ai = False
    if problem := retakes.whisper_problem():
        engine.log(f"WARNING: {problem}")
    style = {"pace": a.pace, "captions": a.captions, "caption_position": a.caption_position,
             "fillers": a.fillers, "loudness": a.loudness, "graphics": a.graphics}
    style = {k: v for k, v in style.items() if v is not None}
    phonon = not a.no_phonon

    def default_out(src):
        return os.path.splitext(src)[0] + ".cut.mp4"
    try:
        if a.combine and len(a.inputs) > 1:
            res = engine.edit_combined(a.inputs, a.output or default_out(a.inputs[0]), cfg, a.instructions,
                                       use_ai, style, phonon)
            results = [res]
        else:
            results = []
            for src in a.inputs:
                res = engine.edit(src, a.output or default_out(src), cfg, a.instructions, use_ai, style,
                                  a.broll, a.sfx, phonon)
                if a.dump:
                    engine.dump(res["_clip"], a.dump if len(a.inputs) == 1 else default_out(src) + ".json")
                results.append(res)
    except engine.CutBotError as ex:
        sys.exit(f"cutbot: {ex}")
    for r in results:
        r.pop("_clip", None)
        print(json.dumps(r, indent=2))

if __name__ == "__main__":
    main()
