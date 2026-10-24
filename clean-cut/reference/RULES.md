# The rules every finished cut must satisfy

`scripts/audit.py` checks all of these and exits with the number of failures,
so a batch can be gated on it. Nothing here is an opinion — each rule is
checked against the pixels or the report.

Every rule exists because a render broke it. Where a rule has a number in it,
the number came from a measurement, and the measurement is cited.

---

## Frame and runtime

| Rule | Threshold | Why |
|---|---|---|
| Vertical | exactly 1080×1920 **as played** | every delivered video measured was 1080×1920 |
| No rotation flag | no display matrix on the output | phones store portrait as landscape plus a flag; `vidstabtransform` passes that flag through to the output, so the player rotates an already-upright frame and the video plays sideways. Only stabilised clips were affected, which is why it survived three rounds unnoticed |
| Runtime | 15s – 180s | the real distribution is p25 50s, median 57s, p90 87s |

## Audio

| Rule | Threshold | Why |
|---|---|---|
| Loudness | −14 LUFS ±1.5 | what social platforms target; `headliner-cut` uses the same number. The human editors delivered at −20.9, so their cuts get turned up on playback and the noise floor rises with them |
| Level matching | per-segment before the global normalise | unmatched speech is *"the number one reason a cut sounds homemade"* |

## B-roll

| Rule | Threshold | Why |
|---|---|---|
| **No filler** | **every insert carries a written reason** | there is no floor. An insert exists because someone decided it earns its place and said why, in the plan, before the render |
| No adjacent repeats | neighbouring inserts differ in category | a repeat reads as *"she only had three clips"* |
| No clip twice | distinct clips per video | same |
| Hook protected | nothing in the first 3s | the hook is what stops the scroll |
| Name protected | nothing across the speaker's introduction | the one moment a face must be on screen |
| Motion | slow push on every insert, uncapped | stock clips are often near-still; one measured 0.46 units/frame and produced a 0.6s dead stretch |

**There is no floor, deliberately.** A floor is a filler generator: when the
cues produced one insert, two more were placed by position and the report said
so in writing - `"(placed to meet the 3-insert floor)"`. Four of six inserts
across two videos were decoration. An empty plan now renders a talking head,
and nothing in the code can invent an insert to hit a count.

Two earned inserts beat three with one crammed in. On a 48s script that is
about representation rather than objects, two is often the honest answer.

## Captions

| Rule | Threshold | Why |
|---|---|---|
| Clear of the **chin** | typical framing (p75) | *"move the caption, never the face"*. The chin, not the mouth - see below |
| Clear of platform UI | margin ≥ 240px | the bottom 220px is covered by the app's own controls |
| One accent word per phrase | exactly one | more than one and the eye has nowhere to land |
| Fits the frame | at most 32 characters | measured on a render at 86px with 140px margins: 27 sits comfortably, 32 touches both edges, 34 and 36 are cut off at BOTH ends. One cut shipped "get accepted, navigating inspections" and it read "t accepted, navigating inspectio" on screen |
| Reads as a phrase | no one-word caption; never ends on a function word | greedy 3-word chunking broke wherever the phrase happened to fall, leaving "situations" alone on screen and "and protecting your" with its noun on the next card. Captions may run to four words to avoid it |
| Measured on the rendered frame | not the source | the punch-in and the stabiliser both enlarge the speaker; estimating from the source was wrong by 150px |

**It is the chin, not the mouth, and that distinction cost a whole batch.**
Both the placement and the audit used to aim at the mouth line - 78% down the
face box - with a 90px gap below it. The chin is the bottom 22% of that box,
which is about 90px on these framings. The two cancel exactly, so "90px below
the mouth" lands precisely ON the chin every time.

Jerry caught it on a cut that had scored 17/17. Measured against the chin,
every speaker overlapped: 85px, 91px, 155px. Re-auditing five videos that had
all passed, four were overlapping - one on 96 frames out of 103. The audit's
own comment said the intent was "a caption resting UNDER the chin"; it had
never done that.

The placement now targets the chin plus a line height plus a gap, and the
audit checks the same boundary. 11 went from 96/103 overlapping to 0/103.

**These two rules can conflict and the conflict is physical.** A speaker who
leans into the lens can drop their mouth to 1700px; the only margin that clears
it is 130px, which is inside the UI strip. The UI boundary is hard, the mouth
is soft, so the typical frame wins and the occasional lean is accepted. A rule
that cannot be satisfied is not a rule — it is noise that trains you to ignore
the audit.

## Picture integrity

| Rule | Threshold | Why |
|---|---|---|
| No near-black frames | zero | a black frame mid-cut is a render fault |
| No freeze | no still run ≥ 0.4s **in the talking head** | scoped deliberately: stock b-roll is routinely lower-fps conformed to 30, so stillness inside an insert is normal footage |
| Camera steady | residual under 0.6 px/frame | a tripod reads 0.2, a hand-held phone 1.3. Stabilisation took one from 1.33 to 0.23 |

Stabilisation is **conditional** — applied only above 0.5 px/frame mean or
1.5 px p95. Stabilising steady footage costs a crop and some sharpness for
nothing, and half the videos tested were shot on something solid.

**Captions are not chunked greedily.** Five successive fixes to a
three-words-at-a-time splitter each produced the next fault - orphans, then
overflow, then orphans again, then captions spanning a full stop. Where to
break a sentence is one decision, and it cannot be made one word at a time.
Every possible split of a sentence is now scored and the cheapest wins.

**This rule exists because the audit passed a video that looked wrong.** 15/15
on 04, and watching it frame by frame showed fragments. Every rule here was
added after something got through; this one is written down so the fix cannot
quietly come undone.

## Transcript

| Rule | Threshold | Why |
|---|---|---|
| No hallucinated tail | last sentences not near-identical | Whisper invents speech past the end of real audio, differently each run — one file produced 168 words once and 221 the next, inventing seventy about *"this small box"* |

---

## What is deliberately NOT a rule

- **Any fixed number of inserts.** Three of the ten delivered videos measured
  carry no b-roll at all. Forcing density is how filler gets made.
- **Reproducibility.** Everything downstream of transcription is
  deterministic, but Whisper is not. Same file, different words.
- **Whether an insert is *the right* insert.** The audit can check that a
  reason was written, that the categories don't repeat, that nothing sits over
  the hook. It cannot know that a falling stock chart contradicts a line about
  not fearing down payments. That judgement is made when the plan is written,
  against `shot-briefs.md`, and it is why the plan is written at all.

---

## Verify the checker before you fix the thing it checks

Three of these rules have been wrong at some point, and each one failed a video
that was correct:

- **Freeze** compared frames a third of a second apart in a slow clip and
  called normal stock footage a freeze.
- **Shake** counted the deliberate b-roll push as camera movement, so a
  tripod-steady source read as 1.03 px/frame.
- **Caption placement** took the maximum over every YuNet box, including small
  low-confidence hits on hair and collars at the frame edges. It reported a
  mouth at 1844px in a 1920-tall frame - anatomically impossible for a talking
  head - and the captions it failed were sitting correctly below the chin.

A fourth near-miss, caught before it cost anything: the v2 library measured
0.0004-0.0157 on ffmpeg's `scene_score` and looked like a shelf of still
images, which would have failed the "it must move" brief. `scene_score` scores
**shot cuts**, not motion - near-zero is exactly what an unbroken clip should
read. Measured with the same frame-difference the audit actually uses, the same
clips run 1.1-3.9 with zero near-identical frames. Nothing was wrong.

**Check what the number means before acting on it.** That last one cost six rounds of rewriting measurement code that was already
right. The tell was there early: the computed margin came back as exactly the
clamp ceiling three times across three different implementations, which meant
the changes were not reaching the result at all.

**So: when a rule fails, check the rule first.** The audit exists because a
finished video cannot be trusted to a glance - which is exactly why the audit
itself cannot be trusted without a look at what it is measuring.

## Running it

```bash
python3 scripts/audit.py FINISHED.mp4
```

Exit code is the failure count. A fault the audit cannot see is a missing rule:
add it here and add the check, rather than remembering it.
