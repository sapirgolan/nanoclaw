# Kid Photo Finder — matching method eval

Throwaway scripts for The Assignment in the design doc
(`~/.gstack/projects/sapirgolan-nanoclaw/nanoclaw-main-design-20260901-135534.md`,
step 2): hand-score a local face-recognition library (DeepFace) on real
photos, before wiring anything into a live NanoClaw agent. Runs standalone
on your machine — no NanoClaw infra involved.

**Claude vision is no longer part of this plan.** It was originally scored
head-to-head against the local library to pick a winner on accuracy. That's
moot now — the live filter will run continuously against every incoming
image across 4+ WhatsApp groups, and you've decided you don't want the
ongoing Claude API cost that implies, regardless of how it would have
scored. So `claude_vision_eval.py` (step "2b" in the older plan) is optional
— run it only if you're curious for a reference number and don't mind the
small API cost; it is not required. The steps below only need
`local_face_recognition_eval.py` (DeepFace).

## 1. Build the eval folder

Create this structure yourself (photo file names don't matter):

```
eval/
  references/
    <kid1_name>/       5-10 clear, varied photos of that kid (any source)
    <kid2_name>/
  candidates/
    <kid1_name>/       real recent WhatsApp photos YOU have confirmed contain that kid
    <kid2_name>/
    none/              real recent WhatsApp photos confirmed to contain NO target kid
```

Since two of your groups aren't wired into NanoClaw yet, there's no
`data/attachments/` history to pull from for those — just save ~100 recent
photos straight out of WhatsApp (long-press → save, or export chat with
media) across all your groups, then sort them into the folders above. That
sorting step is the "hand-scoring" ground truth both scripts compare against
— you're doing once, deliberately, the same judgment call you make every day
manually. Aim for a realistic mix: different angles, lighting, group shots,
and — if you have more than one kid — some photos where only one kid is
present, to test cross-kid misattribution.

Both scripts run via [uv](https://docs.astral.sh/uv/) — each has its
dependencies declared inline (PEP 723), so `uv run` builds an ephemeral venv
and installs them automatically on first run. No manual `pip install` step,
no shared `requirements.txt` to keep in sync. Install uv once if you don't
have it: `curl -LsSf https://astral.sh/uv/install.sh | sh`.

## 2. (Optional, costs API credits) Run the Claude vision eval

Skip this step entirely unless you want a reference number — it's no
longer part of the plan (see note above).

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run claude_vision_eval.py --eval-dir ./eval
```

Calls the Claude API once per (candidate photo × kid), asking "does this kid
appear in this photo?" against your reference photos, zero-shot. For ~100
photos × 2 kids that's ~200 calls — a few minutes, small API cost.

## 3. Run the local face-recognition eval (the step that matters now)

```bash
uv run local_face_recognition_eval.py --eval-dir ./eval
```

Uses [DeepFace](https://github.com/serengil/deepface) (ArcFace model +
RetinaFace detector) to compute face embeddings locally and compare
distances — no API calls. Chosen over dlib/`face_recognition` because it's
actively maintained, installs with plain pip/uv (no cmake/native build), and
RetinaFace handles off-angle/low-light/group-shot photos — the exact
conditions in real WhatsApp photos — noticeably better than dlib's detector.
First run downloads pretrained model weights (a few hundred MB, cached
under `~/.deepface/weights/` afterward) — needs network access once. Also
reports a second metric using DeepFace's own default distance threshold,
since the confidence-mapping used for the shared schema is a rough heuristic
— read the note at the top of that script.

## 4. Read the summary

`local_face_recognition_eval.py` writes `deepface_summary.txt` and
`deepface_results.jsonl` into `eval/`. The number that matters most:
**false negatives** (kid present, DeepFace missed it) — that's the failure
mode the whole design exists to prevent, since a missed match here is
permanent once the 30-day prune runs. Cross-kid misattribution matters next
if you have more than one kid. Per Success Criteria in the design doc, you
want a near-zero false-negative rate before wiring the live filter to any
group — if it's not there yet, iterate on the reference photo set or
thresholds and re-run.

If you have concrete numbers and want to fold them back into the design
doc, or want to move on to actually wiring the agent, come back to this
conversation.
