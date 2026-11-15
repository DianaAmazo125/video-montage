"""Общие утилиты движка: пути, поиск программ, ffprobe, аудио, файлы.

Модуль намеренно не зависит от сторонних библиотек, чтобы движок запускался
даже до установки остальных зависимостей.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENMONTAGE = ROOT / "vendor" / "OpenMontage"
COMPOSER = OPENMONTAGE / "remotion-composer"
VENV_PY = OPENMONTAGE / ".venv" / "Scripts" / "python.exe"
PROJECTS = ROOT / "projects"

_FFMPEG: str | None = None
_FFPROBE: str | None = None
_NODE_DIR: str | None = None


def log(message: str = "") -> None:
    print(message, flush=True)


def _prepend_path(directory: str | Path) -> None:
    directory = str(directory)
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if directory and directory not in parts:
        os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")


def _find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found

    local = Path(os.environ.get("LOCALAPPDATA", ""))
    winget = local / "Microsoft" / "WinGet"
    candidates = [
        winget / "Links" / "ffmpeg.exe",
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    packages = winget / "Packages"
    if packages.is_dir():
        candidates.extend(sorted(packages.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe")))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "nodejs" / "node.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs" / "node.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def ensure_tool_paths() -> dict[str, str]:
    """Находит ffmpeg/ffprobe/node и добавляет их в PATH текущего процесса.

    Возвращает словарь с путями. Нужен потому, что после установки через winget
    уже запущенная оболочка ещё не видит новые переменные PATH.
    """
    global _FFMPEG, _FFPROBE, _NODE_DIR

    if _FFMPEG is None:
        _FFMPEG = _find_ffmpeg()
    if _FFMPEG:
        _prepend_path(Path(_FFMPEG).parent)
        ffprobe = Path(_FFMPEG).with_name("ffprobe.exe")
        if ffprobe.is_file():
            _FFPROBE = str(ffprobe)
        else:
            _FFPROBE = shutil.which("ffprobe")

    node = _find_node()
    if node:
        _NODE_DIR = str(Path(node).parent)
        _prepend_path(_NODE_DIR)

    paths = {
        "ffmpeg": _FFMPEG or "",
        "ffprobe": _FFPROBE or "",
        "node": node or "",
        "node_dir": _NODE_DIR or "",
    }
    missing = [name for name in ("ffmpeg", "ffprobe", "node") if not paths[name]]
    if missing:
        raise RuntimeError(
            "Не найдены программы: "
            + ", ".join(missing)
            + ". Установите их командой: winget install Gyan.FFmpeg OpenJS.NodeJS.LTS"
        )
    return paths


def ffmpeg() -> str:
    return ensure_tool_paths()["ffmpeg"]


def ffprobe() -> str:
    return ensure_tool_paths()["ffprobe"]


def npx() -> str:
    paths = ensure_tool_paths()
    candidate = Path(paths["node_dir"]) / "npx.cmd"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("npx")
    if found:
        return found
    raise RuntimeError("Не найден npx. Переустановите Node.js LTS.")


def run_capture(cmd: list[str], cwd: Path | None = None, timeout: int | None = None) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Команда завершилась с ошибкой:\n"
            + " ".join(cmd)
            + "\n\n"
            + (result.stderr or result.stdout or "")[-4000:]
        )
    return result.stdout


def run_streaming(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> int:
    """Запускает процесс и печатает его вывод в реальном времени."""
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged_env,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(line.rstrip(), flush=True)
    return process.wait()


def probe_video(path: Path) -> dict:
    raw = run_capture(
        [
            ffprobe(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        timeout=120,
    )
    data = json.loads(raw)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise RuntimeError(f"В файле нет видеодорожки: {path}")

    duration = 0.0
    for source in (video.get("duration"), data.get("format", {}).get("duration")):
        try:
            duration = float(source)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue

    fps = 30.0
    try:
        num, _, den = (video.get("avg_frame_rate") or "30/1").partition("/")
        if float(den or 1) > 0:
            fps = float(num) / float(den or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 30.0
    if not 10 <= fps <= 120:
        fps = 30.0

    return {
        "path": str(path),
        "duration": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": round(fps, 3),
        "video_codec": video.get("codec_name"),
        "has_audio": audio is not None,
        "audio_channels": int(audio.get("channels") or 0) if audio else 0,
        "size_bytes": int(data.get("format", {}).get("size") or path.stat().st_size),
        "format": data.get("format", {}).get("format_long_name", ""),
    }


def extract_audio_wav(source: Path, out_path: Path) -> None:
    run_capture(
        [
            ffmpeg(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(out_path),
        ],
        timeout=1800,
    )


def build_audio_bed(source: Path, segments: list[dict], out_path: Path) -> bool:
    """Склеивает звук только из оставленных фрагментов (без пауз).

    Возвращает False, если во входном файле нет звука.
    """
    probe = probe_video(source)
    if not probe["has_audio"]:
        return False

    usable = [
        (float(s["source_in"]), float(s["source_out"]))
        for s in segments
        if float(s["source_out"]) - float(s["source_in"]) > 0.02
    ]
    if not usable:
        return False

    total = sum(end - start for start, end in usable)
    parts = []
    labels = []
    for index, (start, end) in enumerate(usable):
        parts.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]"
        )
        labels.append(f"[a{index}]")
    fade_out_start = max(0.0, total - min(0.7, total / 4))
    filter_complex = (
        ";".join(parts)
        + ";"
        + "".join(labels)
        + f"concat=n={len(labels)}:v=0:a=1[cat];"
        + f"[cat]afade=t=in:st=0:d=0.12,"
        + f"afade=t=out:st={fade_out_start:.3f}:d={min(0.7, total / 4):.3f}[out]"
    )
    run_capture(
        [
            ffmpeg(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(out_path),
        ],
        timeout=1800,
    )
    return out_path.is_file() and out_path.stat().st_size > 1000


def make_thumbnails(video: Path, out_dir: Path, count: int = 3) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = probe_video(video)["duration"] or 1.0
    spots = [0.08, 0.45, 0.85][:count]
    paths: list[Path] = []
    for index, ratio in enumerate(spots, start=1):
        target = out_dir / f"preview_{index}.jpg"
        timestamp = max(0.0, min(duration - 0.1, duration * ratio))
        try:
            run_capture(
                [
                    ffmpeg(),
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{timestamp:.2f}",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(target),
                ],
                timeout=120,
            )
            if target.is_file():
                paths.append(target)
        except RuntimeError:
            continue
    return paths


def copy_or_link(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            if target.stat().st_size == source.stat().st_size:
                return
        except OSError:
            pass
        target.unlink(missing_ok=True)
    try:
        os.link(source, target)
        return
    except OSError:
        pass
    shutil.copy2(source, target)


def slugify(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "_", path.stem).strip("_")[:40] or "video"
    digest = hashlib.md5(str(path.resolve()).encode("utf-8", "replace")).hexdigest()[:6]
    return f"{stem}_{digest}"


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def human_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes, sec = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def iso_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
