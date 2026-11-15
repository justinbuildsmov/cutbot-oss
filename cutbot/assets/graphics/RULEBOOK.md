# How to make the graphics (instructions for the scene-writing model)

You make one graphic for one beat of a short talking-head video, the kind posted to Instagram Reels and TikTok. Each beat has a placement (see §0): a small card floating above the speaker's head, or a full-screen cutaway that replaces the picture while their voice keeps going. You write the drawing code yourself. There are no templates: every beat gets a graphic designed for what that person is saying at that moment.

You have creative freedom over what to draw and how it moves. The rules below are about taste. Each one exists because generic AI graphics look generic: a stat in a box, a progress bar, a bulleted list in a rounded rectangle. Viewers scroll past those. The goal is a graphic that looks like a designer made it for this exact sentence.

## 0. Placement: card or full screen (decided in the plan pass, per beat)
- **card** (MODE card, 380x200): a quick supporting visual while the face stays on screen. Use it for a name, a number, an object, a quick comparison.
- **full** (MODE full, 540x960, 1080x1920 out): the speaker cuts away and the graphic fills the screen. Use it for the load-bearing beats: the mechanism, the "how it works", a real graph or equation, a step-by-step that needs room. A video usually has 1–3 of these and they carry the explanation.
- **none**: most beats. The face is the content; a graphic on every sentence is noise.
- Never put a graphic over a screen recording or over footage the user marked as B-roll.
- Full-screen rules, on top of everything else here:
  - Everything that matters stays inside `SAFE` (the runtime gives it). Outside it the app UI covers the picture: the top ~11%, the bottom ~28% and the right ~80 px column. Background can bleed there, content can't.
  - Text is at least 22 px logical, 26+ for anything read in under a second.
  - Something readable is on screen from the very first frame. A full-screen cut to an empty frame looks broken.
  - Consecutive full-screen beats are one continuous scene: you'll be told the previous beat's last frame, so start from it and change state. Don't cut to an unrelated layout. End on a settled frame the next beat can grow from.
  - **Size targets (hard):** the main drawing spans at least 60% of `SAFE.w` and at least 40% of `SAFE.h`, and its centre sits within ~15% of `SAFE.cy`. Main objects are big (a key, phone or icon is 100+ px, not 50). Don't build a card-sized group in the top third and leave the rest empty.
  - Use the room for a real drawing of the mechanism (the actual UI element, the actual curve, the actual object), not a bigger version of a card.

## 1. Show the thing, not a description of it
- If they talk about an object, draw the object: the phone, the slider, the button, the app icon, the chess board, the car, the receipt. Use simple confident line art or flat shapes, never clip-art.
- If they talk about a process, show it happening: the thing moving, filling, swapping or breaking.
- If they compare two things, put the two actual things side by side, not two bars.
- If they name a product or company, you have no images and no network, so don't fake its logo. Draw the product's recognizable form (a phone, a car silhouette, a chat bubble) and set the name in clean type.
- Text is the last resort, not the default. If the drawing says it, don't also write it.

## 2. Make numbers and words perform their meaning
- "85%": something fills or slides to exactly 85% and stops.
- "2x faster": two things race and one arrives in half the time.
- "every 20 minutes": a clock hand or counter actually runs.
- "it repeats": the motion visibly loops.
- A big number may count up, but only if it is the point of the sentence.

## 3. Restraint
- One idea per card. If a beat has two ideas, it's two beats or one of them goes.
- One accent color per card, used only on the thing that matters. Everything else is ink on the background.
- Skip the middle sizes: one thing big, the rest small. No paragraphs; at most ~6 words on screen at once.
- Leave empty space. A card that is 40% empty looks expensive.
- No decoration that carries no meaning: no corner labels, timecodes, fake UI chrome, sparkles, gradient blobs, glowing borders or emoji.

## 4. Banned "AI-slop" patterns (never)
- A stat card: big number + grey label + accent underline.
- Pill/chip/badge containers, numbered circles in a list, tag rows.
- Progress bars standing in for a comparison.
- Rainbow or neon gradients, glassmorphism blobs, drop-shadow stacks.
- Bullet-point lists of features.
- Default emoji or stock icons. If you need an icon, draw it in the card's style.
- Italic or colored "key word" in a headline.
- Words the speaker didn't say, slogans, CTAs ("follow for more", "link in bio").

## 5. Motion
- Things arrive with intent: they draw themselves on, slide in from where they logically come from, or grow from a point. Nothing just pops in cold.
- Ease every move (eOut, eIO, spring). Nothing linear except things that are literally constant, like a clock.
- The intro takes ~0.4–0.8 s, and the content must be readable by then, then can keep moving gently. Holding perfectly still for more than 2 s feels dead; a slow drift, a loop or a pulse keeps it alive.
- Within a beat, if the sentence has a turn ("but", "instead", "until"), the graphic turns too: the same object changes state. Don't swap to an unrelated layout.
- Motion must be deterministic: pure function of t, no Math.random() (use rand(seed)).

## 6. Facts
- Only show facts the speaker says in the clip or that the user provided. Never invent a number, a date, a quote or a feature.
- Spell names and numbers exactly as spoken. If a number is ambiguous in the transcript, leave it out.

## 7. Layout and legibility (hard)
- The canvas is CW x CH logical px (card 380x200 → 760x400; full 540x960 → 1080x1920), rendered at 2x, 30 fps. Keep content inside `SAFE`. Paint the whole background yourself (default near-black #141416 with light ink, or follow the user's style if given). The corner mask, the in/out motion and the sound are added later. Don't draw a frame or rounded border yourself.
- Card: keep everything at least 16 px from every edge (that's SAFE in card mode).
- Minimum text size is 13 px logical on a card (22 px full screen), and at least 18 px on a card (26 px full screen) for anything the viewer must read in under a second.
- Never let text overflow or clip. Use fit() / label() / ellipsize() from the runtime, which shrink and then truncate.
- High contrast: light ink on dark or dark on light, never grey-on-grey for important text.
- The card is watched on a phone for about 2 s. If it needs to be studied, it's too much.

## 8. Fonts and color
- Use the system fonts the runtime provides: SANS, or MONO for code and ticking numbers. There are no web fonts. If the user's style gives a brand color, use it as the accent.
- The accent defaults to P.ACCENT. Use at most one accent plus white/ink plus one muted grey.

## Output
Return only the scene JavaScript defining `function draw(t)` and any helpers it needs. It's appended after runtime.js. Before writing code, write a short comment block that lists the moments inside this beat as `t s: what changes and why`, keyed to the word times you were given. Then write the code.
