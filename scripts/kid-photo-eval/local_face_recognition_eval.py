#!/usr/bin/env python3
"""
Local face_recognition (dlib) zero-shot kid-photo matcher eval (Assignment step 2c).

For each candidate photo, this computes a face embedding and compares it
against your reference photos' embeddings via face_recognition's distance
metric — no API calls, runs fully locally. Results are scored against the
folder you sorted the candidates into, and written out in the same schema
and format as claude_vision_eval.py so the two reports are directly
comparable.

Folder layout: identical to claude_vision_eval.py — see that script's
docstring or README.md in this directory.

Setup (Linux):
  sudo apt-get install -y cmake build-essential
  pip install -r requirements.txt

Run:
  python local_face_recognition_eval.py --eval-dir ./eval

Output (written into --eval-dir):
  face_recognition_results.jsonl   one row per candidate photo
  face_recognition_summary.txt     aggregate metrics

Note on the confidence mapping: face_recognition reports a Euclidean
*distance* between face embeddings (lower = more similar), not a 0-1
confidence. This script maps confidence = max(0, 1 - distance) to reuse the
same match/uncertain/no-match thresholds as claude_vision_eval.py for a
direct side-by-side comparison, but that mapping is a rough heuristic — a
"good" match is usually distance ~0.4-0.5, i.e. confidence ~0.5-0.6, which
may not clear the 0.8 "match" threshold even when it's actually correct.
Because of that, this script ALSO reports results using the library's own
conventional default (distance <= 0.6 = same person) as a second, separate
metric — read both, don't trust the mapped-confidence number alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    import face_recognition
    import numpy as np
except ImportError:
    print("Missing dependency: pip install -r requirements.txt (needs cmake + a C compiler for dlib)", file=sys.stderr)
    raise

# Same schema as claude_vision_eval.py: match / uncertain / no-match
MATCH_THRESHOLD = 0.8
UNCERTAIN_THRESHOLD = 0.5

# face_recognition's own conventional default tolerance (distance <= this = same person)
LIB_DEFAULT_DISTANCE_TOLERANCE = 0.6


def status_for(confidence: float) -> str:
    if confidence >= MATCH_THRESHOLD:
        return "match"
    if confidence >= UNCERTAIN_THRESHOLD:
        return "uncertain"
    return "no-match"


def collect_images(dir_path: Path) -> list[Path]:
    exts = {".jpg", ".jpeg", ".png"}  # face_recognition doesn't handle webp
    return sorted(p for p in dir_path.iterdir() if p.suffix.lower() in exts)


def encode_faces(path: Path) -> list[np.ndarray]:
    """All face encodings found in the image (usually 0 or 1 for a reference photo)."""
    img = face_recognition.load_image_file(str(path))
    return face_recognition.face_encodings(img)


@dataclass
class Result:
    filename: str
    true_label: str
    predicted_kid: str | None
    confidence: float
    status: str
    min_distance: float
    lib_default_match: bool
    per_kid_min_distance: dict[str, float]
    faces_detected: int


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

    references: dict[str, list[np.ndarray]] = {}
    for kid in kid_names:
        encodings: list[np.ndarray] = []
        for img_path in collect_images(ref_dir / kid):
            found = encode_faces(img_path)
            if not found:
                print(f"  WARNING: no face detected in reference photo {img_path}", file=sys.stderr)
                continue
            if len(found) > 1:
                print(f"  WARNING: multiple faces in reference photo {img_path}, using the first", file=sys.stderr)
            encodings.append(found[0])
        if not encodings:
            print(f"WARNING: no usable reference encodings for '{kid}', skipping", file=sys.stderr)
            continue
        references[kid] = encodings
        print(f"Encoded {len(encodings)} reference photos for '{kid}'")

    candidate_groups = sorted(p.name for p in cand_dir.iterdir() if p.is_dir())
    if not candidate_groups:
        print(f"No label subfolders found under {cand_dir} (expected kid names + 'none')", file=sys.stderr)
        sys.exit(1)

    results: list[Result] = []
    total = sum(len(collect_images(cand_dir / g)) for g in candidate_groups)
    done = 0

    for true_label in candidate_groups:
        for photo_path in collect_images(cand_dir / true_label):
            done += 1
            print(f"[{done}/{total}] {true_label}/{photo_path.name}")

            candidate_faces = encode_faces(photo_path)
            per_kid_min_dist: dict[str, float] = {}

            if not candidate_faces:
                # No face detected at all — flag it, this is itself useful signal
                # (e.g. off-angle / low light beat the detector, not just the matcher).
                results.append(
                    Result(
                        filename=f"{true_label}/{photo_path.name}",
                        true_label=true_label,
                        predicted_kid=None,
                        confidence=0.0,
                        status="no-match",
                        min_distance=1.0,
                        lib_default_match=False,
                        per_kid_min_distance={},
                        faces_detected=0,
                    )
                )
                continue

            for kid, ref_encodings in references.items():
                # best-case: closest reference photo to the closest detected face
                distances = [
                    min(face_recognition.face_distance(ref_encodings, face_enc))
                    for face_enc in candidate_faces
                ]
                per_kid_min_dist[kid] = float(min(distances))

            predicted_kid = min(per_kid_min_dist, key=per_kid_min_dist.get)
            min_distance = per_kid_min_dist[predicted_kid]
            confidence = max(0.0, 1.0 - min_distance)
            status = status_for(confidence)
            if status == "no-match":
                predicted_kid = None

            results.append(
                Result(
                    filename=f"{true_label}/{photo_path.name}",
                    true_label=true_label,
                    predicted_kid=predicted_kid,
                    confidence=round(confidence, 3),
                    status=status,
                    min_distance=round(min_distance, 3),
                    lib_default_match=min_distance <= LIB_DEFAULT_DISTANCE_TOLERANCE,
                    per_kid_min_distance={k: round(v, 3) for k, v in per_kid_min_dist.items()},
                    faces_detected=len(candidate_faces),
                )
            )

    write_outputs(eval_dir, "face_recognition", results)


def write_outputs(eval_dir: Path, prefix: str, results: list[Result]) -> None:
    jsonl_path = eval_dir / f"{prefix}_results.jsonl"
    with open(jsonl_path, "w") as f:
        for r in results:
            f.write(json.dumps(asdict(r)) + "\n")

    false_negatives = []
    misattributions = []
    false_positives = []
    correct_matches = 0
    uncertain_on_real_kid = 0
    no_face_detected_on_real_kid = 0

    # Secondary metric using the library's own conventional distance threshold
    lib_correct = lib_false_negatives = lib_misattributions = lib_false_positives = 0

    for r in results:
        if r.true_label == "none":
            if r.status == "match":
                false_positives.append(r.filename)
            if r.lib_default_match:
                lib_false_positives += 1
            continue

        if r.faces_detected == 0:
            no_face_detected_on_real_kid += 1
            false_negatives.append(r.filename)
        elif r.status == "match" and r.predicted_kid == r.true_label:
            correct_matches += 1
        elif r.status == "match" and r.predicted_kid != r.true_label:
            misattributions.append(r.filename)
        elif r.status == "uncertain":
            uncertain_on_real_kid += 1
            false_negatives.append(r.filename)
        else:
            false_negatives.append(r.filename)

        lib_predicted = min(r.per_kid_min_distance, key=r.per_kid_min_distance.get) if r.per_kid_min_distance else None
        if r.faces_detected > 0 and r.lib_default_match and lib_predicted == r.true_label:
            lib_correct += 1
        elif r.faces_detected > 0 and r.lib_default_match and lib_predicted != r.true_label:
            lib_misattributions += 1
        else:
            lib_false_negatives += 1

    total_real_kid_photos = sum(1 for r in results if r.true_label != "none")
    total_none_photos = sum(1 for r in results if r.true_label == "none")

    lines = [
        f"Method: {prefix}",
        f"Total candidate photos: {len(results)}",
        f"  containing a target kid: {total_real_kid_photos}",
        f"  containing none of them: {total_none_photos}",
        "",
        "--- Using the shared match/uncertain/no-match schema (confidence = 1 - distance) ---",
        f"Correct matches:         {correct_matches} / {total_real_kid_photos}",
        f"False negatives (CRITICAL — kid present, not matched or only 'uncertain'): "
        f"{len(false_negatives)} / {total_real_kid_photos}",
        f"  of which no face detected at all: {no_face_detected_on_real_kid}",
        f"  of which flagged only 'uncertain': {uncertain_on_real_kid}",
        f"Cross-kid misattribution (wrong kid matched): {len(misattributions)} / {total_real_kid_photos}",
        f"False positives ('none' photo matched to a kid): {len(false_positives)} / {total_none_photos}",
        "",
        f"--- Using face_recognition's own default distance tolerance (<= {LIB_DEFAULT_DISTANCE_TOLERANCE}) ---",
        f"Correct matches:         {lib_correct} / {total_real_kid_photos}",
        f"False negatives:         {lib_false_negatives} / {total_real_kid_photos}",
        f"Misattributions:         {lib_misattributions} / {total_real_kid_photos}",
        f"False positives:         {lib_false_positives} / {total_none_photos}",
        "",
    ]
    if false_negatives:
        lines.append("False negative files (shared-schema view):")
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
