# Where this recipe came from

Not invented. Reverse-engineered from a real before/after pair produced by a
human editor, then rebuilt and measured. This file is the provenance, so the
next person to change `autoedit.py` knows which numbers are evidence and which
are guesses.

**Source pair:** one student video, October 2026 — a raw 50.97s phone upload and
the editor's finished 41.87s delivery. Both transcribed with word-level
timestamps and diffed.

> **Everything below is n=1.** It held exactly once. Treat every number as a
> starting point, not a spec, until a batch across several editors says
> otherwise.

---

## What the human editor actually did

| | Raw | Editor |
|---|---|---|
| Duration | 50.97s | 41.87s |
| Resolution | 720×1280 | 1080×1920 |
| Audio | mono, 63 kb/s | stereo, ~258 kb/s |
| Loudness | −24.5 LUFS | −20.9 LUFS |
| Words | 127 | 117 |

The 9.1 seconds came out in three places, and the middle one is the only
interesting one:

| Cut | Span | Length |
|---|---|---|
| Head lead-in | 0 → 0.58s | 0.58s |
| **A fumbled take** | 37.12 → 41.68s | 4.56s |
| Tail dead air | 47.20 → 50.97s | 3.77s |
| | | **8.91s** |

At 37.46 the speaker delivered her closing line, stumbled, and said it again
properly at 41.68. The editor kept the second take. That is the judgement the
whole pipeline has to reproduce, and everything else is mechanical.

### B-roll placement

Three inserts, 16.5s total — about 39% of the finished runtime. Measured by
scene-cut detection plus sampling the outer frame margin for brightness (the
inserts are rounded cards on a near-white plate, so the margin reads > 225).

| Window | Over the line |
|---|---|
| 4.0 – 7.5s | "buying their dream home with just as little as $500" |
| 15.0 – 22.0s | "programs like Idaho Housing, VA loans, USDA" |
| 27.0 – 31.5s | "as little as $5K total out of pocket" |

Every insert sits over a **concrete noun** — a money amount, a named program, a
home. Abstract lines stay on the talking head. That is the whole placement rule.

The card geometry: 940×1660, 56px corner radius, centred on a white full-frame
plate, with captions drawn **on top of** the insert.

---

## What the rebuild scored against it

| | Editor | This pipeline |
|---|---|---|
| Duration | 41.87s | 43.7s |
| Resolution | 1080×1920 | 1080×1920 |
| Loudness | −20.9 LUFS | **−14.8 LUFS** |
| Words kept | 117 | 117 |
| Retake cut | yes | yes, cold |
| B-roll slots | 4.0 / 15.0 / 27.0 | 4.3 / 16.7 / 27.9 |

**Retake detection works cold.** Run on the raw transcript with no reference
edit, `find_retakes` returned exactly one hit at 37.46s, containment 0.909, and
chose the later fuller take — the same call the human made. Run on the editor's
*finished* transcript it returned zero, which is the negative control: there is
no retake left to find because the editor already removed it.

**Slot timing matched within ~1.7s on all three**, derived purely from the
transcript.

**Audio beats the human.** The editor delivered at −20.9 LUFS. Social platforms
target around −14, so their cut gets turned up on playback and the noise floor
comes up with it. Note that `loudnorm` alone cannot reach −14 on peaky phone
audio — its limiter binds around −17. The 4:1 compressor ahead of it is what
makes the target reachable; removing it silently costs ~3 dB.

---

## Where it is still behind

- **B-roll is landscape** when sourced from Mixkit, so it letterboxes inside a
  vertical card. A free Pexels or Pixabay key returns vertical clips and closes
  this gap with no code change.
- **Clip choice within a category is arbitrary** — first unused clip wins. The
  curation pass (`review_library.py`) is the mitigation, not a fix.
- **Trigger words are tuned to one script.** A listing walkthrough or market
  update needs its own entries in `TRIGGERS`.

---

## A finding worth keeping

Running the face screen over the **human editor's own b-roll**, one of their
three clips contains a clearly recognizable face, centre frame, for about seven
seconds. Under `.claude/rules/image-sourcing.md` — *"Recognizable human face?
(right of publicity) → REJECT"* — that clip should not have shipped.

The automated path screens every candidate before use, so on this specific
compliance point it is stricter than the human process it replaces.

(First attempt used an OpenCV Haar cascade, which boxed a kitchen cabinet and
missed the actual face. YuNet gets it right. Do not go back to Haar.)
