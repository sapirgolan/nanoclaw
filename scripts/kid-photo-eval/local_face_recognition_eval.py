#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = [
#     "deepface>=0.0.100",
#     "retina-face>=0.0.17",
#     "numpy>=1.24",
#     "tf-keras",
# ]
# ///
"""
Local DeepFace zero-shot kid-photo matcher eval (Assignment step 2c).

For each candidate photo, this detects faces (RetinaFace) and computes a
face embedding per face (ArcFace) and compares it against your reference
photos' embeddings via cosine distance — no API calls, runs fully locally.
Results are scored against the folder you sorted the candidates into, and
written out in the same schema and format as claude_vision_eval.py so the
two reports are directly comparable.

Why DeepFace over dlib/face_recognition: DeepFace is actively maintained,
offers several detector backends, and RetinaFace in particular handles the
off-angle/low-light/group-shot conditions typical of real WhatsApp photos
much better than dlib's plain HOG detector — which matters more here than
the recognition model itself. Installs with plain pip/uv, no cmake/native
build required (dlib does).

Folder layout: identical to claude_vision_eval.py — see that script's
docstring or README.md in this directory.

Run:
  uv run local_face_recognition_eval.py --eval-dir ./eval

First run downloads pretrained model weights (~a few hundred MB, cached
under ~/.deepface/weights/ afterward) — needs network access once.

Output (written into --eval-dir):
  deepface_results.jsonl   one row per candidate photo
  deepface_summary.txt     aggregate metrics

Note on the confidence mapping: ArcFace + cosine distance ranges roughly
0 (identical) to 2 (opposite), with DeepFace's own verified-match threshold
at 0.68. This script maps confidence = max(0, 1 - distance) to reuse the
same match/uncertain/no-match thresholds as claude_vision_eval.py for a
direct side-by-side comparison, but that mapping is a rough heuristic — a
"good" match at distance ~0.68 gives confidence ~0.32, which may not clear
the 0.8 "match" threshold even when it's actually correct. Because of that,
this script ALSO reports results using DeepFace's own conventional default
threshold as a second, separate metric — read both, don't trust the
mapped-confidence number alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

try:
    from deepface import DeepFace
    import numpy as np
except ImportError as e:
    if "libxcb" in str(e) or "libGL" in str(e) or "libSM" in str(e):
        print(
            "Missing system library for opencv-python (a DeepFace dependency) on a "
            "minimal Linux install. Fix with:\n"
            "  sudo apt-get install -y libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libxcb1",
            file=sys.stderr,
        )
    else:
        print(
            "Missing dependency: run this script with 'uv run local_face_recognition_eval.py ...'",
            file=sys.stderr,
        )
    raise

# Same schema as claude_vision_eval.py: match / uncertain / no-match
MATCH_THRESHOLD = 0.8
UNCERTAIN_THRESHOLD = 0.5

MODEL_NAME = "ArcFace"
DETECTOR_BACKEND = "retinaface"

# DeepFace's own conventional verified-match threshold for ArcFace + cosine distance
LIB_DEFAULT_DISTANCE_TOLERANCE = 0.68


def status_for(confidence: float) -> str:
    if confidence >= MATCH_THRESHOLD:
        return "match"
    if confidence >= UNCERTAIN_THRESHOLD:
        return "uncertain"
    return "no-match"


def collect_images(dir_path: Path) -> list[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p for p in dir_path.iterdir() if p.suffix.lower() in exts)


def cosine_distance(a: list[float], b: list[float]) -> float:
    a_arr, b_arr = np.array(a), np.array(b)
    return float(1 - np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)))


def represent_faces(path: Path) -> list[list[float]]:
    """Embeddings for every face DeepFace detects in the image (0+)."""
    try:
        reps = DeepFace.represent(
            img_path=str(path),
            model_name=MODEL_NAME,
            detector_backend=DETECTOR_BACKEND,
            enforce_detection=True,
        )
    except ValueError:
        return []  # DeepFace's documented "no face detected" signal
    except Exception as e:  # noqa: BLE001 - eval script, log and skip a bad image
        print(f"  WARNING: DeepFace failed on {path}: {e}", file=sys.stderr)
        return []
    return [r["embedding"] for r in reps]


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

    references: dict[str, list[list[float]]] = {}
    for kid in kid_names:
        embeddings: list[list[float]] = []
        for img_path in collect_images(ref_dir / kid):
            found = represent_faces(img_path)
            if not found:
                print(f"  WARNING: no face detected in reference photo {img_path}", file=sys.stderr)
                continue
            if len(found) > 1:
                print(f"  WARNING: multiple faces in reference photo {img_path}, using the first", file=sys.stderr)
            embeddings.append(found[0])
        if not embeddings:
            print(f"WARNING: no usable reference embeddings for '{kid}', skipping", file=sys.stderr)
            continue
        references[kid] = embeddings
        print(f"Encoded {len(embeddings)} reference photos for '{kid}'")

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

            candidate_faces = represent_faces(photo_path)
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
                        min_distance=2.0,
                        lib_default_match=False,
                        per_kid_min_distance={},
                        faces_detected=0,
                    )
                )
                continue

            for kid, ref_embeddings in references.items():
                # best-case: closest reference photo to the closest detected face
                distances = [
                    min(cosine_distance(face_emb, ref_emb) for ref_emb in ref_embeddings)
                    for face_emb in candidate_faces
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

    write_outputs(eval_dir, "deepface", results)


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

    # Secondary metric using DeepFace's own conventional distance threshold
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
        f"Method: {prefix} ({MODEL_NAME} + {DETECTOR_BACKEND})",
        f"Total candidate photos: {len(results)}",
        f"  containing a target kid: {total_real_kid_photos}",
        f"  containing none of them: {total_none_photos}",
        "",
        "--- Using the shared match/uncertain/no-match schema (confidence = 1 - cosine distance) ---",
        f"Correct matches:         {correct_matches} / {total_real_kid_photos}",
        f"False negatives (CRITICAL — kid present, not matched or only 'uncertain'): "
        f"{len(false_negatives)} / {total_real_kid_photos}",
        f"  of which no face detected at all: {no_face_detected_on_real_kid}",
        f"  of which flagged only 'uncertain': {uncertain_on_real_kid}",
        f"Cross-kid misattribution (wrong kid matched): {len(misattributions)} / {total_real_kid_photos}",
        f"False positives ('none' photo matched to a kid): {len(false_positives)} / {total_none_photos}",
        "",
        f"--- Using DeepFace's own default distance threshold (<= {LIB_DEFAULT_DISTANCE_TOLERANCE}) ---",
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
