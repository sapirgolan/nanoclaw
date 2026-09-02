#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "anthropic>=0.40",
#     "pillow>=10.0",
# ]
# ///
"""
Claude vision zero-shot kid-photo matcher eval (Assignment step 2b).

For each candidate photo, this asks Claude vision, per kid, "does this kid
appear in this photo?" using your reference photos as the only context (no
fine-tuning, no embeddings — zero-shot). Results are scored against the
folder you sorted the candidates into, and written out in the same schema
and format as local_face_recognition_eval.py so the two reports are directly
comparable.

Folder layout expected (see README.md in this directory for how to build it):

  eval/
    references/
      <kid_name>/        5-10 clear photos of that kid, any source
      <kid_name>/
    candidates/
      <kid_name>/        real WhatsApp photos YOU have confirmed contain that kid
      none/              real WhatsApp photos confirmed to contain NO target kid

Setup:
  export ANTHROPIC_API_KEY=sk-ant-...
  (uv reads the inline dependency block above and creates an ephemeral venv automatically)

  If you get a 400 error mentioning "anthropic-workspace-id is required" —
  your key is an identity-linked / multi-workspace key (Console > Settings >
  API Keys shows this), which must be told which workspace to act in on
  every request. Either:
    - set ANTHROPIC_WORKSPACE_ID=wrkspc_... (find it under Console >
      Settings > Workspaces, in the workspace's URL/details), or
    - create a new API key scoped to a single workspace instead (Console >
      Settings > API Keys > Create Key > pick a workspace) — no header
      needed with that kind of key.

Run:
  uv run claude_vision_eval.py --eval-dir ./eval

Output (written into --eval-dir):
  claude_vision_results.jsonl   one row per candidate photo
  claude_vision_summary.txt     aggregate metrics
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Missing dependency: run this script with 'uv run claude_vision_eval.py ...'", file=sys.stderr)
    raise

try:
    import anthropic
except ImportError:
    print("Missing dependency: run this script with 'uv run claude_vision_eval.py ...'", file=sys.stderr)
    raise

# Matches the schema agreed in the design doc:
#   { kid, confidence, status: match | uncertain | no-match }
MATCH_THRESHOLD = 0.8
UNCERTAIN_THRESHOLD = 0.5

MODEL = "claude-sonnet-5"
MAX_IMAGE_SIDE = 1024  # downscale before sending, keeps cost/latency sane
REQUEST_SLEEP_S = 0.3  # be gentle on rate limits
MAX_RETRIES = 3


def status_for(confidence: float) -> str:
    if confidence >= MATCH_THRESHOLD:
        return "match"
    if confidence >= UNCERTAIN_THRESHOLD:
        return "uncertain"
    return "no-match"


def load_image_b64(path: Path) -> tuple[str, str]:
    """Returns (base64_data, media_type), downscaled to MAX_IMAGE_SIDE."""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    scale = MAX_IMAGE_SIDE / max(w, h)
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def image_block(path: Path) -> dict:
    data, media_type = load_image_b64(path)
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def collect_images(dir_path: Path) -> list[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p for p in dir_path.iterdir() if p.suffix.lower() in exts)


def ask_claude_is_kid_present(
    client: anthropic.Anthropic, kid_name: str, ref_paths: list[Path], candidate_path: Path
) -> tuple[bool, float]:
    """Returns (present, confidence 0..1). Falls back to (False, 0.0) on parse failure."""
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"The following {len(ref_paths)} photos all show the same child, "
                f'named "{kid_name}". Study their face carefully across all reference photos.'
            ),
        }
    ]
    for p in ref_paths:
        content.append(image_block(p))

    content.append(
        {
            "type": "text",
            "text": (
                f'Now look at this NEW photo. Does it contain "{kid_name}"? '
                "The photo may be low quality, off-angle, poorly lit, or contain multiple "
                "children including similar-looking siblings — judge carefully.\n\n"
                'Respond with ONLY a JSON object, no other text: '
                '{"present": true or false, "confidence": a number from 0.0 to 1.0}'
            ),
        }
    )
    content.append(image_block(candidate_path))

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content": content}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if not m:
                raise ValueError(f"no JSON found in response: {text!r}")
            parsed = json.loads(m.group(0))
            present = bool(parsed.get("present", False))
            confidence = float(parsed.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            return present, confidence
        except Exception as e:  # noqa: BLE001 - eval script, log and retry
            if "anthropic-workspace-id" in str(e):
                print(
                    "\nFATAL: your API key requires ANTHROPIC_WORKSPACE_ID to be set. "
                    "See the setup notes at the top of this script for how to find it, "
                    "or use a single-workspace-scoped key instead.",
                    file=sys.stderr,
                )
                sys.exit(1)
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    print(f"  WARNING: giving up on {candidate_path.name} / {kid_name}: {last_err}", file=sys.stderr)
    return False, 0.0


@dataclass
class Result:
    filename: str
    true_label: str  # kid name, or "none"
    predicted_kid: str | None
    confidence: float
    status: str
    per_kid_confidence: dict[str, float]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval-dir", default="./eval", help="Directory containing references/ and candidates/")
    args = ap.parse_args()

    eval_dir = Path(args.eval_dir)
    ref_dir = eval_dir / "references"
    cand_dir = eval_dir / "candidates"

    if not ref_dir.is_dir() or not cand_dir.is_dir():
        print(f"Expected {ref_dir} and {cand_dir} to exist. See this script's docstring / README.md.", file=sys.stderr)
        sys.exit(1)

    kid_names = sorted(p.name for p in ref_dir.iterdir() if p.is_dir())
    if not kid_names:
        print(f"No kid subfolders found under {ref_dir}", file=sys.stderr)
        sys.exit(1)

    references: dict[str, list[Path]] = {}
    for kid in kid_names:
        imgs = collect_images(ref_dir / kid)
        if not imgs:
            print(f"WARNING: no reference photos for '{kid}', skipping", file=sys.stderr)
            continue
        references[kid] = imgs
        print(f"Loaded {len(imgs)} reference photos for '{kid}'")

    candidate_groups = sorted(p.name for p in cand_dir.iterdir() if p.is_dir())
    if not candidate_groups:
        print(f"No label subfolders found under {cand_dir} (expected kid names + 'none')", file=sys.stderr)
        sys.exit(1)

    import os

    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    client = anthropic.Anthropic(
        # reads ANTHROPIC_API_KEY from env; identity-linked/multi-workspace keys
        # additionally need anthropic-workspace-id on every request (see docstring)
        default_headers={"anthropic-workspace-id": workspace_id} if workspace_id else None,
    )

    results: list[Result] = []
    total = sum(len(collect_images(cand_dir / g)) for g in candidate_groups)
    done = 0

    for true_label in candidate_groups:
        for photo_path in collect_images(cand_dir / true_label):
            done += 1
            print(f"[{done}/{total}] {true_label}/{photo_path.name}")
            per_kid_conf: dict[str, float] = {}
            for kid, ref_paths in references.items():
                present, confidence = ask_claude_is_kid_present(client, kid, ref_paths, photo_path)
                per_kid_conf[kid] = confidence if present else min(confidence, UNCERTAIN_THRESHOLD - 0.01)
                time.sleep(REQUEST_SLEEP_S)

            if per_kid_conf:
                predicted_kid = max(per_kid_conf, key=per_kid_conf.get)
                best_conf = per_kid_conf[predicted_kid]
            else:
                predicted_kid, best_conf = None, 0.0

            status = status_for(best_conf)
            if status == "no-match":
                predicted_kid = None

            results.append(
                Result(
                    filename=f"{true_label}/{photo_path.name}",
                    true_label=true_label,
                    predicted_kid=predicted_kid,
                    confidence=round(best_conf, 3),
                    status=status,
                    per_kid_confidence={k: round(v, 3) for k, v in per_kid_conf.items()},
                )
            )

    write_outputs(eval_dir, "claude_vision", results)


def write_outputs(eval_dir: Path, prefix: str, results: list[Result]) -> None:
    jsonl_path = eval_dir / f"{prefix}_results.jsonl"
    with open(jsonl_path, "w") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")

    false_negatives = []  # kid present, but not matched
    misattributions = []  # wrong kid matched
    false_positives = []  # "none" candidate matched to a kid
    correct_matches = 0
    uncertain_on_real_kid = 0

    for r in results:
        if r.true_label == "none":
            if r.status == "match":
                false_positives.append(r.filename)
            continue
        if r.status == "match" and r.predicted_kid == r.true_label:
            correct_matches += 1
        elif r.status == "match" and r.predicted_kid != r.true_label:
            misattributions.append(r.filename)
        elif r.status == "uncertain":
            uncertain_on_real_kid += 1
            false_negatives.append(r.filename)  # uncertain still requires manual review == missed automation
        else:  # no-match
            false_negatives.append(r.filename)

    total_real_kid_photos = sum(1 for r in results if r.true_label != "none")
    total_none_photos = sum(1 for r in results if r.true_label == "none")

    lines = [
        f"Method: {prefix}",
        f"Total candidate photos: {len(results)}",
        f"  containing a target kid: {total_real_kid_photos}",
        f"  containing none of them: {total_none_photos}",
        "",
        f"Correct matches:         {correct_matches} / {total_real_kid_photos}",
        f"False negatives (CRITICAL — kid present, not matched or only 'uncertain'): "
        f"{len(false_negatives)} / {total_real_kid_photos}",
        f"  of which flagged only 'uncertain' (not fully missed, but not automated either): {uncertain_on_real_kid}",
        f"Cross-kid misattribution (wrong kid matched): {len(misattributions)} / {total_real_kid_photos}",
        f"False positives ('none' photo matched to a kid): {len(false_positives)} / {total_none_photos}",
        "",
    ]
    if false_negatives:
        lines.append("False negative files:")
        lines.extend(f"  - {f}" for f in false_negatives)
        lines.append("")
    if misattributions:
        lines.append("Misattributed files:")
        lines.extend(f"  - {f}" for f in misattributions)
        lines.append("")
    if false_positives:
        lines.append("False positive files:")
        lines.extend(f"  - {f}" for f in false_positives)
        lines.append("")

    summary_path = eval_dir / f"{prefix}_summary.txt"
    summary_path.write_text("\n".join(lines))

    print("\n" + "\n".join(lines))
    print(f"\nWrote {jsonl_path} and {summary_path}")


if __name__ == "__main__":
    main()
