"""
Verification script for the analysis pipeline.
Creates synthetic frames with known properties and checks classifier results.

Run from project root:
    python test_analysis.py
    python test_analysis.py --verbose
"""
import sys
import argparse
import tempfile
from pathlib import Path

import cv2
import numpy as np

# Make sure analyzer package is importable
sys.path.insert(0, str(Path(__file__).parent))
from analyzer.local_cv import analyze_frames_cv


def make_frame(path: Path, bgr: np.ndarray):
    cv2.imwrite(str(path), bgr)
    return path


def solid(H, W, b, g, r) -> np.ndarray:
    img = np.zeros((H, W, 3), dtype=np.uint8)
    img[:] = (b, g, r)
    return img


def _run(name: str, frames: list[Path], expect_category: str,
         expect_usable: bool | None = None, verbose: bool = False) -> bool:
    result = analyze_frames_cv(frames)
    if result is None:
        print(f"  FAIL  {name}: analyze_frames_cv returned None")
        return False

    got_cat = result['category']
    got_conf = result['confidence']
    got_usable = result['is_usable']

    ok_cat = got_cat == expect_category
    ok_usable = (expect_usable is None) or (got_usable == expect_usable)
    passed = ok_cat and ok_usable

    status = "PASS" if passed else "FAIL"
    print(f"  {status}  {name}")
    if verbose or not passed:
        print(f"        category: {got_cat!r} (expected {expect_category!r}), "
              f"conf: {got_conf:.2f}, usable: {got_usable}")
        if verbose:
            print(f"        metrics: {result.get('_metrics', {})}")
    return passed


def run_all(verbose: bool = False) -> int:
    H, W = 288, 512
    passed = failed = 0

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)

        def f(name: str, img: np.ndarray) -> list[Path]:
            p = d / f"{name}.jpg"
            make_frame(p, img)
            return [p]

        def with_texture(img: np.ndarray, seed: int = 42) -> np.ndarray:
            """Add mild Gaussian noise so Laplacian variance > 0 (blur > 25)."""
            rng = np.random.default_rng(seed)
            noise = rng.integers(-18, 19, img.shape, dtype=np.int16)
            return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        # ── Pure green → nature ────────────────────────────────────────────────
        # H=65 (pure green hue), S=200, V=180 in HSV → lush green
        nature_img = with_texture(cv2.cvtColor(
            np.full((H, W, 3), (65, 200, 180), dtype=np.uint8), cv2.COLOR_HSV2BGR
        ))
        ok = _run("pure green → nature", f("nature", nature_img), "nature",
                  expect_usable=True, verbose=verbose)
        passed += ok; failed += not ok

        # ── Warm orange + bright → golden-hour ────────────────────────────────
        # H=20 (orange), S=220, V=200 — warm and bright
        golden_img = with_texture(cv2.cvtColor(
            np.full((H, W, 3), (20, 220, 200), dtype=np.uint8), cv2.COLOR_HSV2BGR
        ))
        ok = _run("warm orange → golden-hour", f("golden", golden_img), "golden-hour",
                  expect_usable=True, verbose=verbose)
        passed += ok; failed += not ok

        # ── Sky blue upper half → travel ───────────────────────────────────────
        sky_img = np.zeros((H, W, 3), dtype=np.uint8)
        # upper 40% = sky blue (H=110, S=100, V=200), lower = grey ground
        sky_hsv = np.full((int(H * 0.4), W, 3), (110, 100, 200), dtype=np.uint8)
        ground = np.full((H - int(H * 0.4), W, 3), (50, 50, 50), dtype=np.uint8)
        sky_img[:int(H * 0.4)] = cv2.cvtColor(sky_hsv, cv2.COLOR_HSV2BGR)
        sky_img[int(H * 0.4):] = ground
        ok = _run("blue sky upper → travel", f("sky", sky_img), "travel",
                  verbose=verbose)
        passed += ok; failed += not ok

        # ── Very blurry dark frame → not usable ───────────────────────────────
        dark_img = solid(H, W, 15, 15, 15)  # near-black
        ok = _run("near-black → not usable", f("dark", dark_img), "b-roll",
                  expect_usable=False, verbose=verbose)
        passed += ok; failed += not ok

        # ── High edge density → architecture ─────────────────────────────────
        # Create a grid of high-contrast edges (simulates building facade)
        arch_img = np.zeros((H, W), dtype=np.uint8)
        for i in range(0, H, 20):
            arch_img[i, :] = 200
        for j in range(0, W, 20):
            arch_img[:, j] = 200
        arch_bgr = cv2.cvtColor(arch_img, cv2.COLOR_GRAY2BGR)
        ok = _run("grid lines → architecture", f("arch", arch_bgr), "architecture",
                  verbose=verbose)
        passed += ok; failed += not ok

        # ── Portrait: face present → portrait wins over color signals ─────────
        # Green background (would normally → nature) + a face-like blob
        face_img = nature_img.copy()
        # Draw a skin-toned oval to trigger Haar cascade heuristically
        # (Haar may not fire on a synthetic blob, so we note this is best-effort)
        cv2.ellipse(face_img, (W // 2, H // 2), (60, 80), 0, 0, 360, (80, 130, 200), -1)
        result = analyze_frames_cv([make_frame(d / "face.jpg", face_img)])
        if result is not None and verbose:
            print(f"  INFO  face ellipse on green: category={result['category']!r}, "
                  f"conf={result['confidence']:.2f}")

    total = passed + failed
    print(f"\n{'─'*40}")
    print(f"Results: {passed}/{total} passed", end="")
    print(" ✓" if failed == 0 else f"  ({failed} failed)")
    return failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print("Running analysis verification tests...\n")
    exit_code = run_all(verbose=args.verbose)
    sys.exit(min(exit_code, 1))
