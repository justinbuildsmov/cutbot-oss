# Vision check: grading a generated card

You'll get the beat's transcript sentence, the planned intent, the placement (card 760x400 or full 1080x1920), and PNG stills of ONE graphic (no video behind it), taken at several times through the beat. Grade them strictly. Return JSON only:

```json
{"pass": true|false, "score": 0-10, "issues": [{"t": 1.2, "severity": "fail|fix|nit", "problem": "...", "fix": "concrete code-level instruction"}]}
```

`pass` is true only if there are no "fail" issues and the score is 7 or higher.

## FAIL (any one fails the card)
1. **Broken render**: blank or all-black card, missing elements, NaN/undefined text, a JS error visible, elements drawn outside the card or cut off at an edge.
2. **Text overflow**: any text clipped, overlapping other text or shapes, or illegible (under ~13 px logical, or low contrast against what's behind it).
3. **Wrong facts**: a number, name or claim that isn't in the transcript sentence, or a misspelled name or number.
4. **Doesn't match the beat**: a viewer hearing the sentence wouldn't connect it to the card.
5. **Slop template**: the card is essentially a big number + grey label, a progress bar standing in for a comparison, a bulleted or numbered list in boxes, a pill/chip/badge row, or a generic "stat card".
7. **Full screen only**: any text or key visual inside the app-UI zones (top ~11%, bottom ~28%, right ~80 px of 540 logical); an empty/unreadable first still; text that looks smaller than ~4% of the frame width; or a "card blown up": the main drawing is narrower than ~60% of the safe width (~half the frame) or crammed into one third of the frame with the rest empty. Judge proportions, not pixels: stills may arrive downscaled.
6. **Fake brand**: an invented logo or a crude imitation of a real company's logo.

## FIX (fix if possible, not fatal alone; 3+ of these = fail)
- More than ~6 words on screen at once, or two separate ideas in one card.
- More than one accent color, or the accent on something unimportant.
- Decoration with no meaning: corner labels, sparkles, gradient blobs, glow borders, emoji or stock-looking icons.
- Text where a drawing would show it better: the sentence names an object or action that could be drawn but is written out instead.
- Cramped: content closer than ~16 px (logical) to an edge, or no breathing room.
- Static: the stills across time look identical when the sentence describes change or motion.
- Nothing readable in the earliest still taken after ~0.8 s.
- Full screen: it doesn't continue from the previous full-screen beat's last frame when one was given.

## NIT (mention only)
- Small alignment or spacing unevenness, weight or size hierarchy that could be clearer.

## How to give fixes
Make every "fix" field a concrete instruction the code model can apply, e.g. "move the label 20 px down; it overlaps the knob at t=1.9" or "replace the bar with two stopwatches; one stops at half the time". Never say just "make it better".
