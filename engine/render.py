"""Подготовка ассетов и запуск рендера Remotion."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from . import util


def stage_assets(
    slug: str,
    source: Path,
    audio_bed: Path | None,
    music: Path | None,
) -> dict[str, str | None]:
    """Кладёт файлы в public/ Remotion и возвращает относительные пути."""
    public_dir = util.COMPOSER / "public" / "montage" / slug
    public_dir.mkdir(parents=True, exist_ok=True)

    source_target = public_dir / f"source{source.suffix.lower()}"
    util.copy_or_link(source, source_target)

    audio_target = None
    if audio_bed and audio_bed.is_file():
        audio_target = public_dir / "audio.m4a"
        util.copy_or_link(audio_bed, audio_target)

    music_target = None
    if music and music.is_file():
        music_target = public_dir / f"music{music.suffix.lower()}"
        util.copy_or_link(music, music_target)

    base = f"montage/{slug}"
    return {
        "source": f"{base}/{source_target.name}",
        "audio": f"{base}/{audio_target.name}" if audio_target else None,
        "music": f"{base}/{music_target.name}" if music_target else None,
        "public_dir": str(public_dir),
    }


def write_props(project_dir: Path, props: dict) -> Path:
    path = project_dir / "props.json"
    path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def render(project_dir: Path, props_path: Path, slug: str) -> Path:
    """Рендерит композицию Montage и возвращает путь к готовому mp4."""
    temp_dir = Path(tempfile.gettempdir()) / "openmontage-render"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_output = temp_dir / f"{slug}.mp4"
    temp_output.unlink(missing_ok=True)

    command = [
        util.npx(),
        "remotion",
        "render",
        "src/index.tsx",
        "Montage",
        str(temp_output),
        f"--props={props_path}",
        "--codec=h264",
        "--crf=18",
        "--overwrite",
        "--log=info",
    ]
    util.log("      команда: npx remotion render src/index.tsx Montage ...")
    code = util.run_streaming(command, cwd=util.COMPOSER)
    if code != 0:
        raise RuntimeError(f"Remotion завершился с кодом {code}. Смотрите лог выше.")
    if not temp_output.is_file() or temp_output.stat().st_size < 10_000:
        raise RuntimeError("Remotion не создал выходной файл.")

    final_output = project_dir / "output.mp4"
    final_output.unlink(missing_ok=True)
    shutil.move(str(temp_output), str(final_output))
    return final_output
