import subprocess
import json
import os
import functools
from pathlib import Path

import cv2
import imageio_ffmpeg

FRAME_TMP_DIR = Path("/tmp/video-analyzer-frames")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".MP4", ".MOV", ".AVI", ".MKV"}


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def _ffprobe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe().replace("ffmpeg", "ffprobe")


def find_videos(folder: str) -> list[Path]:
    folder_path = Path(folder)
    videos = []
    for ext in VIDEO_EXTENSIONS:
        videos.extend(folder_path.rglob(f"*{ext}"))
    return sorted(videos)


@functools.lru_cache(maxsize=512)
def get_display_orientation(video_path: str) -> tuple[int, int, bool]:
    """
    Returns (display_width, display_height, is_portrait).
    Uses ffprobe to read the rotation tag that cv2 ignores (common on iPhone footage).
    Result cached by path — called up to 3× per clip during scan, now only once.
    Falls back to raw dims on failure.
    """
    cmd = [
        _ffprobe(), "-v", "quiet", "-print_format", "json",
        "-show_streams", "-select_streams", "v:0", str(video_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        info = json.loads(result.stdout)
        stream = info["streams"][0]
        w = stream["width"]
        h = stream["height"]
        rotate = int(stream.get("tags", {}).get("rotate", 0))
        for sd in stream.get("side_data_list", []):
            if sd.get("side_data_type") == "Display Matrix":
                rotate = int(sd.get("rotation", rotate))
        if abs(rotate) in (90, 270):
            w, h = h, w
        return w, h, h > w
    except Exception:
        return 0, 0, False


def get_video_metadata(video_path: str) -> dict:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {}

    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    raw_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    raw_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    duration = (frame_count / fps) if fps > 0 else 0
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)

    # Use display dims (respects rotation tag) if available, fall back to raw
    disp_w, disp_h, _ = get_display_orientation(video_path)
    w = disp_w or raw_w
    h = disp_h or raw_h

    return {
        "duration_seconds": round(duration, 2),
        "resolution": f"{w}x{h}" if w and h else "",
        "file_size_mb": round(file_size_mb, 2),
    }


def extract_frames(video_path: str, max_frames: int = 3,
                   md5: str | None = None) -> list[Path]:
    """
    Extract frames for analysis. When md5 is supplied, frames are cached under
    FRAME_TMP_DIR/<md5>/ and reused on re-analysis — avoids re-running ffmpeg
    every time confidence thresholds push a clip back for a second pass.
    """
    # Use MD5-keyed subdir when available so re-analysis hits cache
    if md5:
        frame_dir = FRAME_TMP_DIR / md5
        if frame_dir.exists():
            cached = sorted(frame_dir.glob("f*.jpg"))
            if cached:
                return cached
    else:
        frame_dir = FRAME_TMP_DIR

    frame_dir.mkdir(parents=True, exist_ok=True)

    meta = get_video_metadata(video_path)
    duration = meta.get("duration_seconds", 0) or 10

    _, _, is_portrait = get_display_orientation(video_path)
    vf_scale = "scale=-2:512" if is_portrait else "scale=512:-2"

    start = duration * 0.05
    end = duration * 0.95
    span = end - start
    count = min(max_frames, max(1, int(duration / 5)))

    if span <= 0:
        timestamps = [duration / 2]
    else:
        step = span / max(count, 1)
        timestamps = [start + i * step for i in range(count)]

    frame_paths = []
    for i, ts in enumerate(timestamps):
        out = frame_dir / f"f{i:02d}.jpg"
        cmd = [
            _ffmpeg(), "-y", "-ss", str(ts), "-i", str(video_path),
            "-frames:v", "1", "-q:v", "3",
            "-vf", vf_scale,
            str(out)
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0 and out.exists():
            frame_paths.append(out)

    return frame_paths


def extract_thumbnail(video_path: str, out_dir: Path, md5: str) -> Path | None:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{md5}.jpg"
    if out.exists():
        return out

    meta = get_video_metadata(video_path)
    duration = meta.get("duration_seconds", 0)
    ts = max(duration * 0.1, 0.5)

    _, _, is_portrait = get_display_orientation(video_path)
    vf_scale = "scale=-2:320" if is_portrait else "scale=320:-1"

    cmd = [
        _ffmpeg(), "-y", "-ss", str(ts), "-i", str(video_path),
        "-frames:v", "1", "-q:v", "4",
        "-vf", vf_scale,
        str(out)
    ]
    result = subprocess.run(cmd, capture_output=True)
    return out if result.returncode == 0 and out.exists() else None


def cleanup_frames(frame_paths: list[Path], force: bool = False):
    """
    Delete temporary frames. If frames live in a MD5-keyed subdir of
    FRAME_TMP_DIR they are kept (cache hit on re-analysis); pass force=True
    to delete them anyway (e.g. when a clip is removed from the library).
    """
    for p in frame_paths:
        try:
            # Keep MD5-cached frames unless forced
            if not force and p.parent != FRAME_TMP_DIR:
                continue
            p.unlink(missing_ok=True)
        except Exception:
            pass
