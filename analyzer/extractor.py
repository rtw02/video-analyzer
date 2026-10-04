import subprocess
import os
from pathlib import Path

import cv2
import imageio_ffmpeg

FRAME_TMP_DIR = Path("/tmp/video-analyzer-frames")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".MP4", ".MOV", ".AVI", ".MKV"}


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def find_videos(folder: str) -> list[Path]:
    folder_path = Path(folder)
    videos = []
    for ext in VIDEO_EXTENSIONS:
        videos.extend(folder_path.rglob(f"*{ext}"))
    return sorted(videos)


def get_video_metadata(video_path: str) -> dict:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {}

    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    duration = (frame_count / fps) if fps > 0 else 0
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)

    return {
        "duration_seconds": round(duration, 2),
        "resolution": f"{width}x{height}" if width and height else "",
        "file_size_mb": round(file_size_mb, 2),
    }


def extract_frames(video_path: str, max_frames: int = 8) -> list[Path]:
    FRAME_TMP_DIR.mkdir(parents=True, exist_ok=True)

    meta = get_video_metadata(video_path)
    duration = meta.get("duration_seconds", 0) or 10

    start = duration * 0.05
    end = duration * 0.95
    span = end - start
    count = min(max_frames, max(1, int(duration / 5)))

    if span <= 0:
        timestamps = [duration / 2]
    else:
        step = span / max(count, 1)
        timestamps = [start + i * step for i in range(count)]

    stem = Path(video_path).stem
    frame_paths = []
    for i, ts in enumerate(timestamps):
        out = FRAME_TMP_DIR / f"{stem}_f{i:02d}.jpg"
        cmd = [
            _ffmpeg(), "-y", "-ss", str(ts), "-i", str(video_path),
            "-frames:v", "1", "-q:v", "3",
            "-vf", "scale=1024:-1",
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

    cmd = [
        _ffmpeg(), "-y", "-ss", str(ts), "-i", str(video_path),
        "-frames:v", "1", "-q:v", "4",
        "-vf", "scale=320:-1",
        str(out)
    ]
    result = subprocess.run(cmd, capture_output=True)
    return out if result.returncode == 0 and out.exists() else None


def cleanup_frames(frame_paths: list[Path]):
    for p in frame_paths:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
