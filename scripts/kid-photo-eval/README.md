# Kid Photo Finder — matching method eval

Throwaway scripts for The Assignment in the design doc
(`~/.gstack/projects/sapirgolan-nanoclaw/nanoclaw-main-design-20260901-135534.md`,
step 2b/2c): hand-score Claude vision zero-shot vs. a local face-recognition
library on real photos, before wiring anything into a live NanoClaw agent.
These run standalone on your machine — no NanoClaw infra involved.

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

## 2. Run the Claude vision eval

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run claude_vision_eval.py --eval-dir ./eval
```

Calls the Claude API once per (candidate photo × kid), asking "does this kid
appear in this photo?" against your reference photos, zero-shot. For ~100
photos × 2 kids that's ~200 calls — a few minutes, small API cost.

## 3. Run the local face-recognition eval

```bash
# Linux: dlib is a native build, uv can't install the system compiler toolchain
sudo apt-get install -y cmake build-essential

uv run local_face_recognition_eval.py --eval-dir ./eval
```

Computes face embeddings locally (no API calls) and compares distances. Also
reports a second metric using the library's own default distance tolerance,
since the confidence-mapping used for the shared schema is a rough heuristic
— read the note at the top of that script.

## 4. Compare the two summaries

Both scripts write `<method>_summary.txt` and `<method>_results.jsonl` into
`eval/`. Read the two `_summary.txt` files side by side. The number that
matters most: **false negatives** (kid present, method missed it) — that's
the failure mode the whole design exists to prevent, since a missed match
here is permanent once the 30-day prune runs. Cross-kid misattribution
matters next if you have more than one kid. Whichever method has the lower
false-negative count wins and becomes the live identity-matcher (see
Premise 2 and Dependencies in the design doc) — API cost/latency vs. local
compute is a secondary tiebreaker only if both come out close.

If you have concrete numbers and want to fold them back into the design
doc, or want to move on to actually wiring the agent, come back to this
conversation.
