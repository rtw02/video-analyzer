"""
Color grade application: .drx (DaVinci Resolve XML) and .cube (3D LUT).

.drx → extracts ASC CDL (Slope/Offset/Power + Saturation) → generates a .cube
.cube → applied directly via ffmpeg lut3d filter
"""
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import imageio_ffmpeg

GRADES_DIR = Path(__file__).parent.parent / "data" / "grades"
CUBE_CACHE_DIR = Path(__file__).parent.parent / "data" / "grades" / "_cube_cache"


def _ffmpeg() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


# ── DRX parsing ───────────────────────────────────────────────────────────────

def parse_drx(drx_path: Path) -> dict:
    """
    Parse a DaVinci Resolve .drx file and return ASC CDL params:
    { slope: [r,g,b], offset: [r,g,b], power: [r,g,b], saturation: float }
    Falls back to identity values if parsing fails.
    """
    identity = {"slope": [1.0, 1.0, 1.0], "offset": [0.0, 0.0, 0.0],
                "power": [1.0, 1.0, 1.0], "saturation": 1.0}
    try:
        tree = ET.parse(drx_path)
        root = tree.getroot()

        def find_text(tag):
            el = root.find(f".//{tag}")
            return el.text.strip() if el is not None and el.text else None

        def parse_triplet(text):
            if not text:
                return None
            parts = text.split()
            if len(parts) == 3:
                return [float(x) for x in parts]
            return None

        slope  = parse_triplet(find_text("Slope"))  or identity["slope"]
        offset = parse_triplet(find_text("Offset")) or identity["offset"]
        power  = parse_triplet(find_text("Power"))  or identity["power"]

        sat_text = find_text("Saturation")
        saturation = float(sat_text) if sat_text else 1.0

        return {"slope": slope, "offset": offset, "power": power, "saturation": saturation}
    except Exception:
        return identity


# ── CDL → .cube generation ────────────────────────────────────────────────────

def cdl_to_cube(cdl: dict, size: int = 33) -> str:
    """
    Generate a .cube LUT string from ASC CDL params.
    Formula: out = clamp(slope * in + offset)^power, then saturation mix.
    """
    slope = cdl["slope"]
    offset = cdl["offset"]
    power = cdl["power"]
    sat = cdl["saturation"]

    # Rec.709 luminance coefficients for saturation
    LUM_R, LUM_G, LUM_B = 0.2126, 0.7152, 0.0722

    lines = [
        "TITLE \"CDL Grade\"",
        f"LUT_3D_SIZE {size}",
        "",
    ]

    step = 1.0 / (size - 1)
    for b_i in range(size):
        for g_i in range(size):
            for r_i in range(size):
                r = r_i * step
                g = g_i * step
                b = b_i * step

                # ASC CDL: slope * in + offset, clamped, then power
                def cdl_ch(v, s, o, p):
                    v = max(0.0, min(1.0, s * v + o))
                    return v ** p if p != 1.0 else v

                r2 = cdl_ch(r, slope[0], offset[0], power[0])
                g2 = cdl_ch(g, slope[1], offset[1], power[1])
                b2 = cdl_ch(b, slope[2], offset[2], power[2])

                # Saturation
                if sat != 1.0:
                    lum = LUM_R * r2 + LUM_G * g2 + LUM_B * b2
                    r2 = lum + sat * (r2 - lum)
                    g2 = lum + sat * (g2 - lum)
                    b2 = lum + sat * (b2 - lum)
                    r2 = max(0.0, min(1.0, r2))
                    g2 = max(0.0, min(1.0, g2))
                    b2 = max(0.0, min(1.0, b2))

                lines.append(f"{r2:.6f} {g2:.6f} {b2:.6f}")

    return "\n".join(lines)


def drx_to_cube_path(drx_path: Path) -> Path:
    """Convert .drx to a cached .cube file; return the cube path."""
    CUBE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cube_path = CUBE_CACHE_DIR / (drx_path.stem + ".cube")
    if not cube_path.exists() or cube_path.stat().st_mtime < drx_path.stat().st_mtime:
        cdl = parse_drx(drx_path)
        cube_path.write_text(cdl_to_cube(cdl))
    return cube_path


# ── Apply grade to a frame ────────────────────────────────────────────────────

def apply_grade_to_frame(video_path: str, cube_path: Path,
                          out_path: Path, timestamp: float = None) -> bool:
    """
    Extract one frame at `timestamp` with lut3d grade applied. Returns True on success.
    """
    from analyzer.extractor import get_video_metadata, get_rotation_degrees, _rotation_vf
    meta = get_video_metadata(video_path)
    duration = meta.get("duration_seconds", 0) or 10
    ts = timestamp if timestamp is not None else duration * 0.1

    rotate = get_rotation_degrees(video_path)
    rot_flags, scale_vf = _rotation_vf(rotate, height=512)

    vf = f"{scale_vf},lut3d='{cube_path}'"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg(), *rot_flags, "-y", "-ss", str(ts), "-i", str(video_path),
        "-frames:v", "1", "-q:v", "3",
        "-vf", vf,
        str(out_path)
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and out_path.exists()


# ── Public helpers ────────────────────────────────────────────────────────────

def get_cube_for_grade(grade_filename: str) -> Path | None:
    """Return the .cube path for a grade file (handles both .cube and .drx)."""
    path = GRADES_DIR / grade_filename
    if not path.exists():
        return None
    if path.suffix.lower() == ".cube":
        return path
    if path.suffix.lower() == ".drx":
        try:
            return drx_to_cube_path(path)
        except Exception:
            return None
    return None


def list_grades() -> list[dict]:
    """List all grade files in GRADES_DIR."""
    GRADES_DIR.mkdir(parents=True, exist_ok=True)
    grades = []
    for f in sorted(GRADES_DIR.iterdir()):
        if f.suffix.lower() in (".cube", ".drx") and not f.name.startswith("_"):
            grades.append({"name": f.name, "type": f.suffix.lower().lstrip(".")})
    return grades
