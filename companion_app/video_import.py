from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

try:
    import cv2  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional runtime dependency
    cv2 = None

try:
    import imageio_ffmpeg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional runtime dependency
    imageio_ffmpeg = None


VIDEO_IMPORT_VERSION = 1
_ANALYSIS_EDGE = 480
_FLOW_EDGE = 320
_ORB_FEATURES = 900


@dataclass
class CandidateFrame:
    frame_index: int
    timestamp_ms: int
    sharpness: float
    brightness: float
    contrast: float
    underexposed_ratio: float
    overexposed_ratio: float
    black_ratio: float
    edge_density: float
    hash_value: int
    hist: np.ndarray
    keypoints: np.ndarray
    descriptors: np.ndarray | None
    quality_score: float = 0.0
    motion_from_prev: float = 0.0
    scene_cut: bool = False
    selection_score: float = 0.0


def ffmpeg_runtime_path() -> str | None:
    if imageio_ffmpeg is not None:
        try:
            return str(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception:
            pass
    for candidate in ("ffmpeg.exe", "ffmpeg"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def video_runtime_summary() -> dict[str, Any]:
    return {
        "opencv_available": cv2 is not None,
        "ffmpeg_path": ffmpeg_runtime_path(),
    }


def extract_representative_video_frames(video_path: Path, target_dir: Path) -> dict[str, Any]:
    if cv2 is None:
        raise RuntimeError("Video import requires OpenCV (cv2); it is not available in this runtime.")

    video_info = _probe_video_info(video_path)
    frame_count = max(1, int(video_info["frame_count"]))
    fps = float(video_info["fps"])
    duration_s = float(video_info["duration_s"])

    candidate_indices = _sample_candidate_indices(frame_count, duration_s)
    candidates = _load_candidates_cv2(video_path, candidate_indices, fps)
    if not candidates:
        raise RuntimeError(f"No usable frames were decoded from {video_path.name}.")

    _score_candidates(candidates)
    target_frames = _target_output_frame_count(frame_count, duration_s, len(candidates))
    min_frames = max(8, min(target_frames, 16))

    overlap_cache: dict[tuple[int, int], dict[str, float]] = {}
    selected = _select_keyframes(candidates, target_frames)
    selected = _fill_selection_gaps(selected, candidates, min_frames)
    selected = _prune_adjacent_duplicates(selected, overlap_cache, min_frames)
    selected, bridge_inserts = _bridge_low_overlap(selected, candidates, overlap_cache, target_frames)
    selected = _trim_selection(selected, max(target_frames, min_frames))

    exported_frames = _export_selected_frames_cv2(video_path, selected, target_dir)
    if not exported_frames:
        raise RuntimeError(f"No usable frames were exported from {video_path.name}.")

    overlap_rows = _selected_overlap_rows(selected, overlap_cache)
    export_by_index = {int(row["frame_index"]): row for row in exported_frames}
    for row in overlap_rows:
        frame_index = int(row["frame_index"])
        export = export_by_index.get(frame_index)
        if export is None:
            continue
        export["overlap_prev_score"] = row.get("overlap_prev_score")
        export["overlap_next_score"] = row.get("overlap_next_score")
        export["overlap_prev_inliers"] = row.get("overlap_prev_inliers")
        export["overlap_next_inliers"] = row.get("overlap_next_inliers")

    rejected = max(0, len(candidates) - len(exported_frames))
    sharpness_values = [candidate.sharpness for candidate in candidates]
    blur_cutoff = float(np.percentile(sharpness_values, 20)) if sharpness_values else 0.0
    selected_overlap_scores = [float(row["score"]) for row in overlap_rows if row.get("score") is not None]
    selected_overlap_inliers = [float(row["inliers"]) for row in overlap_rows if row.get("inliers") is not None]

    return {
        "version": VIDEO_IMPORT_VERSION,
        "video_name": video_path.name,
        "video_path": str(video_path),
        "backend": "opencv",
        "ffmpeg_path": ffmpeg_runtime_path(),
        "fps": round(fps, 5),
        "duration_s": round(duration_s, 5),
        "frame_count": frame_count,
        "candidate_count": len(candidates),
        "selected_count": len(exported_frames),
        "rejected_count": rejected,
        "scene_cut_count": int(sum(1 for candidate in candidates if candidate.scene_cut)),
        "bridge_inserts": int(bridge_inserts),
        "black_candidate_count": int(sum(1 for candidate in candidates if candidate.black_ratio >= 0.92)),
        "soft_candidate_count": int(sum(1 for candidate in candidates if candidate.sharpness <= blur_cutoff)),
        "selected_overlap_mean": round(float(np.mean(selected_overlap_scores)), 5) if selected_overlap_scores else None,
        "selected_overlap_min": round(float(np.min(selected_overlap_scores)), 5) if selected_overlap_scores else None,
        "selected_overlap_inliers_mean": (
            round(float(np.mean(selected_overlap_inliers)), 5) if selected_overlap_inliers else None
        ),
        "selected_frames": exported_frames,
    }


def _probe_video_info(video_path: Path) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video file {video_path.name}.")
    try:
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        capture.release()

    if frame_count <= 0:
        frame_count = 1
    if fps <= 1e-6:
        fps = 30.0
    duration_s = float(frame_count) / float(fps)
    return {
        "frame_count": frame_count,
        "fps": fps,
        "duration_s": duration_s,
        "width": width,
        "height": height,
    }


def _sample_candidate_indices(frame_count: int, duration_s: float) -> list[int]:
    target_final = _target_output_frame_count(frame_count, duration_s, frame_count)
    candidate_count = min(frame_count, max(target_final * 5, 96))
    if duration_s > 0.0:
        candidate_count = min(candidate_count, max(96, int(round(duration_s * 8.0))))
    if candidate_count >= frame_count:
        return list(range(frame_count))

    sampled = np.linspace(0, frame_count - 1, num=candidate_count, dtype=np.float64)
    indices = sorted({int(round(float(value))) for value in sampled})
    if indices and indices[-1] != frame_count - 1:
        indices.append(frame_count - 1)
    return indices


def _target_output_frame_count(frame_count: int, duration_s: float, candidate_count: int) -> int:
    if duration_s > 0.0:
        base = int(round(duration_s * 1.4))
    else:
        base = max(12, frame_count // 100)
    base = max(16, min(160, base))
    return max(8, min(base, max(8, candidate_count)))


def _load_candidates_cv2(video_path: Path, indices: list[int], fps: float) -> list[CandidateFrame]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video file {video_path.name}.")

    orb = cv2.ORB_create(nfeatures=_ORB_FEATURES)
    candidates: list[CandidateFrame] = []
    previous_hist: np.ndarray | None = None
    previous_flow_gray: np.ndarray | None = None
    try:
        for frame_index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            timestamp_ms = int(round(capture.get(cv2.CAP_PROP_POS_MSEC) or 0.0))
            if timestamp_ms <= 0 and fps > 1e-6:
                timestamp_ms = int(round((float(frame_index) / float(fps)) * 1000.0))

            candidate, flow_gray = _build_candidate(rgb_frame, frame_index, timestamp_ms, orb)
            if previous_flow_gray is not None:
                candidate.motion_from_prev = _motion_score(previous_flow_gray, flow_gray)
            if previous_hist is not None:
                candidate.scene_cut = _scene_cut(previous_hist, candidate.hist, candidate.black_ratio)
            candidates.append(candidate)
            previous_hist = candidate.hist
            previous_flow_gray = flow_gray
    finally:
        capture.release()
    return candidates


def _build_candidate(
    rgb_frame: np.ndarray,
    frame_index: int,
    timestamp_ms: int,
    orb,
) -> tuple[CandidateFrame, np.ndarray]:
    analysis_rgb = _resize_rgb(rgb_frame, _ANALYSIS_EDGE)
    gray = cv2.cvtColor(analysis_rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(analysis_rgb, cv2.COLOR_RGB2HSV)
    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    sharpness = float(np.var(laplacian))
    brightness = float(gray.mean() / 255.0)
    contrast = float(gray.std() / 255.0)
    underexposed_ratio = float((gray < 20).mean())
    overexposed_ratio = float((gray > 235).mean())
    black_ratio = float((gray < 12).mean())
    edges = cv2.Canny(gray, 80, 160)
    edge_density = float(edges.mean() / 255.0)

    hist = cv2.calcHist([hsv], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256])
    hist = hist.astype(np.float32).reshape(-1)
    hist_sum = float(hist.sum())
    if hist_sum > 0.0:
        hist /= hist_sum

    keypoints, descriptors = orb.detectAndCompute(gray, None)
    keypoint_array = (
        np.asarray([point.pt for point in keypoints], dtype=np.float32)
        if keypoints
        else np.zeros((0, 2), dtype=np.float32)
    )

    flow_gray = _resize_gray(gray, _FLOW_EDGE)
    return (
        CandidateFrame(
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            sharpness=sharpness,
            brightness=brightness,
            contrast=contrast,
            underexposed_ratio=underexposed_ratio,
            overexposed_ratio=overexposed_ratio,
            black_ratio=black_ratio,
            edge_density=edge_density,
            hash_value=_average_hash(gray),
            hist=hist,
            keypoints=keypoint_array,
            descriptors=descriptors,
        ),
        flow_gray,
    )


def _resize_rgb(frame: np.ndarray, max_edge: int) -> np.ndarray:
    height, width = frame.shape[:2]
    longest = max(height, width)
    if longest <= max_edge:
        return frame
    scale = float(max_edge) / float(longest)
    target = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return cv2.resize(frame, target, interpolation=cv2.INTER_AREA)


def _resize_gray(frame: np.ndarray, max_edge: int) -> np.ndarray:
    height, width = frame.shape[:2]
    longest = max(height, width)
    if longest <= max_edge:
        return frame
    scale = float(max_edge) / float(longest)
    target = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return cv2.resize(frame, target, interpolation=cv2.INTER_AREA)


def _motion_score(previous_gray: np.ndarray, current_gray: np.ndarray) -> float:
    if previous_gray.shape != current_gray.shape:
        current_gray = cv2.resize(
            current_gray,
            (previous_gray.shape[1], previous_gray.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
    flow = cv2.calcOpticalFlowFarneback(
        previous_gray,
        current_gray,
        None,
        0.5,
        3,
        21,
        3,
        5,
        1.2,
        0,
    )
    magnitude = np.sqrt((flow[..., 0] ** 2) + (flow[..., 1] ** 2))
    return float(np.percentile(magnitude, 70))


def _scene_cut(previous_hist: np.ndarray, current_hist: np.ndarray, black_ratio: float) -> bool:
    if black_ratio >= 0.95:
        return True
    distance = 0.5 * float(np.abs(previous_hist - current_hist).sum())
    return distance >= 0.58


def _average_hash(gray: np.ndarray, size: int = 8) -> int:
    thumbnail = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    mean_value = float(thumbnail.mean())
    bits = thumbnail >= mean_value
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bool(bit))
    return value


def _score_candidates(candidates: list[CandidateFrame]) -> None:
    if not candidates:
        return

    sharpness = np.log1p(np.asarray([candidate.sharpness for candidate in candidates], dtype=np.float32))
    contrast = np.asarray([candidate.contrast for candidate in candidates], dtype=np.float32)
    edge_density = np.asarray([candidate.edge_density for candidate in candidates], dtype=np.float32)
    clipping = np.asarray(
        [candidate.underexposed_ratio + candidate.overexposed_ratio for candidate in candidates],
        dtype=np.float32,
    )
    brightness = np.asarray([candidate.brightness for candidate in candidates], dtype=np.float32)
    black_ratio = np.asarray([candidate.black_ratio for candidate in candidates], dtype=np.float32)
    brightness_center = float(np.median(brightness))

    sharpness_norm = _robust_normalize(sharpness)
    contrast_norm = _robust_normalize(contrast)
    edge_norm = _robust_normalize(edge_density)
    clipping_norm = _robust_normalize(clipping)

    for index, candidate in enumerate(candidates):
        brightness_penalty = min(1.0, abs(float(brightness[index]) - brightness_center) / 0.25)
        candidate.quality_score = (
            (0.56 * float(sharpness_norm[index]))
            + (0.16 * float(contrast_norm[index]))
            + (0.12 * float(edge_norm[index]))
            - (0.18 * float(clipping_norm[index]))
            - (0.12 * brightness_penalty)
            - (0.44 * float(black_ratio[index]))
        )


def _robust_normalize(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    lower = float(np.percentile(values, 10))
    upper = float(np.percentile(values, 90))
    if not math.isfinite(lower) or not math.isfinite(upper) or abs(upper - lower) < 1e-6:
        return np.full_like(values, 0.5)
    normalized = (values - lower) / (upper - lower)
    return np.clip(normalized, 0.0, 1.0)


def _select_keyframes(candidates: list[CandidateFrame], target_frames: int) -> list[CandidateFrame]:
    if not candidates:
        return []

    motions = np.asarray([candidate.motion_from_prev for candidate in candidates[1:]], dtype=np.float32)
    if motions.size:
        motion_threshold = max(2.0, float(np.percentile(motions, 65)) * 1.2)
    else:
        motion_threshold = 4.0
    max_gap_ms = 1400

    selected: list[CandidateFrame] = []
    segment: list[CandidateFrame] = []
    accumulated_motion = 0.0
    for candidate in candidates:
        if candidate.scene_cut and segment:
            selected.append(_pick_subsequence_frame(segment))
            segment = []
            accumulated_motion = 0.0

        segment.append(candidate)
        accumulated_motion += candidate.motion_from_prev
        if len(segment) <= 1:
            continue

        gap_ms = segment[-1].timestamp_ms - segment[0].timestamp_ms
        if accumulated_motion >= motion_threshold or gap_ms >= max_gap_ms:
            selected.append(_pick_subsequence_frame(segment))
            segment = [segment[-1]]
            accumulated_motion = 0.0

    if segment:
        selected.append(_pick_subsequence_frame(segment))

    unique: dict[int, CandidateFrame] = {}
    for candidate in selected:
        existing = unique.get(candidate.frame_index)
        if existing is None or candidate.selection_score > existing.selection_score:
            unique[candidate.frame_index] = candidate
    ordered = sorted(unique.values(), key=lambda item: item.frame_index)

    if len(ordered) > max(target_frames * 2, target_frames + 12):
        ordered = _trim_selection(ordered, max(target_frames * 2, target_frames + 12))
    return ordered


def _pick_subsequence_frame(segment: list[CandidateFrame]) -> CandidateFrame:
    if len(segment) == 1:
        segment[0].selection_score = segment[0].quality_score
        return segment[0]

    best = segment[0]
    best_score = -1e9
    denominator = max(1, len(segment) - 1)
    for index, candidate in enumerate(segment):
        center_bias = 1.0 - (abs((float(index) / float(denominator)) - 0.5) * 0.65)
        score = candidate.quality_score + (0.16 * center_bias)
        if candidate.black_ratio >= 0.95:
            score -= 1.0
        candidate.selection_score = score
        if score > best_score:
            best = candidate
            best_score = score
    best.selection_score = best_score
    return best


def _fill_selection_gaps(
    selected: list[CandidateFrame],
    candidates: list[CandidateFrame],
    min_frames: int,
) -> list[CandidateFrame]:
    if len(selected) >= min_frames:
        return selected

    selected_by_index = {candidate.frame_index: candidate for candidate in selected}
    remaining = sorted(candidates, key=lambda item: item.quality_score, reverse=True)
    for candidate in remaining:
        if candidate.frame_index in selected_by_index:
            continue
        if _is_too_close_to_selection(candidate, selected_by_index.values()):
            continue
        selected_by_index[candidate.frame_index] = candidate
        if len(selected_by_index) >= min_frames:
            break
    return sorted(selected_by_index.values(), key=lambda item: item.frame_index)


def _is_too_close_to_selection(candidate: CandidateFrame, selected: Any) -> bool:
    for other in selected:
        if abs(candidate.timestamp_ms - other.timestamp_ms) < 250:
            return True
    return False


def _prune_adjacent_duplicates(
    selected: list[CandidateFrame],
    overlap_cache: dict[tuple[int, int], dict[str, float]],
    min_frames: int,
) -> list[CandidateFrame]:
    if len(selected) <= max(2, min_frames):
        return selected

    changed = True
    pruned = list(selected)
    while changed and len(pruned) > min_frames:
        changed = False
        for index in range(1, len(pruned)):
            left = pruned[index - 1]
            right = pruned[index]
            overlap = _overlap_score(left, right, overlap_cache)
            near_duplicate = (
                _hamming_distance(left.hash_value, right.hash_value) <= 2
                or (overlap["score"] >= 0.92 and overlap["median_displacement"] <= 7.0)
            )
            if not near_duplicate:
                continue
            drop_index = index if left.selection_score >= right.selection_score else index - 1
            del pruned[drop_index]
            changed = True
            break
    return pruned


def _bridge_low_overlap(
    selected: list[CandidateFrame],
    candidates: list[CandidateFrame],
    overlap_cache: dict[tuple[int, int], dict[str, float]],
    target_frames: int,
) -> tuple[list[CandidateFrame], int]:
    bridged = list(selected)
    bridge_inserts = 0
    max_frames = max(target_frames + 8, int(round(target_frames * 1.25)))
    changed = True
    while changed and len(bridged) < max_frames:
        changed = False
        for index in range(len(bridged) - 1):
            left = bridged[index]
            right = bridged[index + 1]
            if right.frame_index - left.frame_index <= 1:
                continue
            overlap = _overlap_score(left, right, overlap_cache)
            if overlap["score"] >= 0.22 or overlap["inliers"] >= 12.0:
                continue

            between = [
                candidate
                for candidate in candidates
                if left.frame_index < candidate.frame_index < right.frame_index and candidate.frame_index not in {
                    item.frame_index for item in bridged
                }
            ]
            if not between:
                continue

            best_candidate: CandidateFrame | None = None
            best_score = overlap["score"]
            for candidate in between:
                left_score = _overlap_score(left, candidate, overlap_cache)
                right_score = _overlap_score(candidate, right, overlap_cache)
                combined = min(left_score["score"], right_score["score"]) + (candidate.quality_score * 0.08)
                if left_score["inliers"] < 8.0 or right_score["inliers"] < 8.0:
                    combined -= 0.15
                if combined > best_score + 0.06:
                    best_candidate = candidate
                    best_score = combined
            if best_candidate is None:
                continue

            bridged.insert(index + 1, best_candidate)
            bridge_inserts += 1
            changed = True
            break
    return bridged, bridge_inserts


def _trim_selection(selected: list[CandidateFrame], max_frames: int) -> list[CandidateFrame]:
    if len(selected) <= max_frames:
        return selected
    buckets = np.linspace(0, len(selected), max_frames + 1, dtype=np.float64)
    trimmed: list[CandidateFrame] = []
    for bucket_index in range(max_frames):
        start = int(math.floor(float(buckets[bucket_index])))
        end = int(math.floor(float(buckets[bucket_index + 1])))
        window = selected[start:max(end, start + 1)]
        if not window:
            continue
        best = max(window, key=lambda item: item.selection_score or item.quality_score)
        trimmed.append(best)
    deduped: dict[int, CandidateFrame] = {}
    for candidate in trimmed:
        deduped[candidate.frame_index] = candidate
    return sorted(deduped.values(), key=lambda item: item.frame_index)


def _overlap_score(
    left: CandidateFrame,
    right: CandidateFrame,
    overlap_cache: dict[tuple[int, int], dict[str, float]],
) -> dict[str, float]:
    cache_key = (min(left.frame_index, right.frame_index), max(left.frame_index, right.frame_index))
    cached = overlap_cache.get(cache_key)
    if cached is not None:
        return cached

    if (
        left.descriptors is None
        or right.descriptors is None
        or len(left.keypoints) < 8
        or len(right.keypoints) < 8
    ):
        result = {"score": 0.0, "inliers": 0.0, "ratio": 0.0, "median_displacement": 0.0}
        overlap_cache[cache_key] = result
        return result

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    try:
        knn_matches = matcher.knnMatch(left.descriptors, right.descriptors, k=2)
    except cv2.error:
        result = {"score": 0.0, "inliers": 0.0, "ratio": 0.0, "median_displacement": 0.0}
        overlap_cache[cache_key] = result
        return result

    good_matches = []
    for match_pair in knn_matches:
        if len(match_pair) < 2:
            continue
        best, second = match_pair
        if best.distance < (0.78 * second.distance):
            good_matches.append(best)

    if len(good_matches) < 8:
        result = {"score": 0.0, "inliers": 0.0, "ratio": 0.0, "median_displacement": 0.0}
        overlap_cache[cache_key] = result
        return result

    src_points = np.float32([left.keypoints[match.queryIdx] for match in good_matches]).reshape(-1, 1, 2)
    dst_points = np.float32([right.keypoints[match.trainIdx] for match in good_matches]).reshape(-1, 1, 2)
    displacements = np.linalg.norm(src_points.reshape(-1, 2) - dst_points.reshape(-1, 2), axis=1)
    median_displacement = float(np.median(displacements)) if displacements.size else 0.0

    inliers = 0.0
    if len(good_matches) >= 12:
        _, mask = cv2.findHomography(src_points, dst_points, cv2.RANSAC, 3.5)
        if mask is not None:
            inliers = float(mask.sum())
    if inliers <= 0.0:
        inliers = float(min(len(good_matches), len(left.keypoints), len(right.keypoints))) * 0.35

    ratio = float(inliers / max(1.0, float(len(good_matches))))
    score = (min(1.0, inliers / 30.0) * 0.72) + (min(1.0, ratio / 0.45) * 0.28)
    if median_displacement <= 7.0 and ratio >= 0.68:
        score *= 0.62
    result = {
        "score": float(score),
        "inliers": float(inliers),
        "ratio": float(ratio),
        "median_displacement": float(median_displacement),
    }
    overlap_cache[cache_key] = result
    return result


def _hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _export_selected_frames_cv2(
    video_path: Path,
    selected: list[CandidateFrame],
    target_dir: Path,
) -> list[dict[str, Any]]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not reopen video file {video_path.name} for export.")

    exported: list[dict[str, Any]] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        for output_index, candidate in enumerate(selected):
            capture.set(cv2.CAP_PROP_POS_FRAMES, candidate.frame_index)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            file_name = (
                f"{output_index:03d}_{video_path.stem}_f{candidate.frame_index:06d}_t{candidate.timestamp_ms:09d}.jpg"
            )
            file_path = target_dir / file_name
            Image.fromarray(rgb_frame).save(file_path, format="JPEG", quality=94)
            exported.append(
                {
                    "image_name": file_name,
                    "source_name": file_name,
                    "image_path": str(file_path),
                    "frame_index": int(candidate.frame_index),
                    "timestamp_ms": int(candidate.timestamp_ms),
                    "quality_score": round(float(candidate.quality_score), 6),
                    "selection_score": round(float(candidate.selection_score or candidate.quality_score), 6),
                    "sharpness": round(float(candidate.sharpness), 6),
                    "brightness": round(float(candidate.brightness), 6),
                    "contrast": round(float(candidate.contrast), 6),
                    "underexposed_ratio": round(float(candidate.underexposed_ratio), 6),
                    "overexposed_ratio": round(float(candidate.overexposed_ratio), 6),
                    "black_ratio": round(float(candidate.black_ratio), 6),
                    "motion_from_prev": round(float(candidate.motion_from_prev), 6),
                    "scene_cut": bool(candidate.scene_cut),
                }
            )
    finally:
        capture.release()
    return exported


def _selected_overlap_rows(
    selected: list[CandidateFrame],
    overlap_cache: dict[tuple[int, int], dict[str, float]],
) -> list[dict[str, float | int | None]]:
    rows: list[dict[str, float | int | None]] = []
    for index, candidate in enumerate(selected):
        previous_overlap = (
            _overlap_score(selected[index - 1], candidate, overlap_cache) if index > 0 else None
        )
        next_overlap = (
            _overlap_score(candidate, selected[index + 1], overlap_cache)
            if index + 1 < len(selected)
            else None
        )
        rows.append(
            {
                "frame_index": int(candidate.frame_index),
                "score": float(previous_overlap["score"]) if previous_overlap else None,
                "inliers": float(previous_overlap["inliers"]) if previous_overlap else None,
                "overlap_prev_score": float(previous_overlap["score"]) if previous_overlap else None,
                "overlap_prev_inliers": float(previous_overlap["inliers"]) if previous_overlap else None,
                "overlap_next_score": float(next_overlap["score"]) if next_overlap else None,
                "overlap_next_inliers": float(next_overlap["inliers"]) if next_overlap else None,
            }
        )
    return rows
