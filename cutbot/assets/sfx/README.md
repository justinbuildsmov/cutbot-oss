# Sound effects (not included)

`sfx.json` describes the sound library the AI planner picks from: each entry's
role, file name, mix level (`gain_db`) and a "use" note that tells the planner
when that sound fits. **The audio files are not in this repository.**

The original library came from [Mixkit](https://mixkit.co/free-sound-effects/)
under the Mixkit Free License. That license allows the sounds to be used inside
your own projects (like a finished edit) but does not allow redistributing the
files on their own, so they can't ship here.

To use `--sfx`:

1. Download sounds that fit each role. The Mixkit item numbers the original
   library used are listed below (search mixkit.co for them), but any short
   sound of the right kind works, from any source whose license you're happy with.
2. Convert each one to a 48 kHz mono WAV with the leading silence trimmed (so
   the transient is the sync point), named as in the table:

   ```sh
   ffmpeg -i downloaded.mp3 -ac 1 -ar 48000 \
     -af "silenceremove=start_periods=1:start_threshold=-45dB,areverse,afade=t=in:d=0.015,areverse" \
     whoosh-short.wav
   ```

3. Put the WAVs in this folder, or anywhere else and set `CUTBOT_SFX_DIR`.

Roles whose file is missing are simply left out of what the planner can use;
with no files at all, `--sfx` is skipped with a warning. `click-ui.wav`, if
present, is also played softly as each graphic card comes in.

| file | Mixkit item | use |
|---|---|---|
| `whoosh-short.wav` | 1485 | quick graphic/card entrance |
| `whoosh-tight.wav` | 1491 | small element in; alternate with whoosh-short |
| `whoosh-mid.wav` | 1489 | screenshot/panel entrance |
| `whoosh-big.wav` | 1714 | section transition ONLY (big moments) |
| `sweep-up.wav` | 166 | punch-in / zoom-in |
| `shutter-a.wav` | 1430 | freeze-frame reveal, receipts |
| `shutter-b.wav` | 1433 | alternate shutter |
| `click-mouse.wav` | 1133 | cursor actions in screen recordings |
| `click-ui.wav` | 2568 | editor annotations: callouts, small UI |
| `pop-rise.wav` | 2358 | stat/badge build (stackable) |
| `impact-punch.wav` | 498 | THE key claim; max 2 per video |
| `impact-bigboom.wav` | 1143 | hero card landing; has long tail, fade it |
| `riser-short.wav` | 790 | ~1.8s build into payoff; peak on reveal then cut |
| `riser-synth.wav` | 645 | longer synth build (trim to 2-3s at use) |
| `ding.wav` | 933 | list items, tips; alternate pitches |
| `ding-falling.wav` | 601 | alt ding (descending) |
| `downlifter.wav` | 2901 | section close; trim to ~2s |
