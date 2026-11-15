"""Анализ исходника: распознавание речи и определение сцен.

Использует инструменты OpenMontage (faster-whisper и scene_detect), поэтому
результат совместим с остальной экосистемой репозитория.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import util


def _import_tools():
    if str(util.OPENMONTAGE) not in sys.path:
        sys.path.insert(0, str(util.OPENMONTAGE))


def transcribe(
    source: Path,
    project_dir: Path,
    model_size: str = "small",
    language: str | None = None,
    force: bool = False,
) -> dict | None:
    """Возвращает транскрипт faster-whisper или None, если речи нет."""
    util.ensure_tool_paths()
    cache_path = project_dir / "transcript.json"
    stat = source.stat()
    cache_key = {
        "source_size": stat.st_size,
        "source_mtime": int(stat.st_mtime),
        "model": model_size,
        "language": language or "auto",
    }

    if not force and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key:
                util.log("      использую сохранённый транскрипт")
                return cached.get("transcript")
        except (json.JSONDecodeError, OSError):
            pass

    _import_tools()
    try:
        from tools.analysis.transcriber import Transcriber
    except Exception as exc:  # noqa: BLE001 - показываем понятную причину
        util.log(f"      [!] Не удалось загрузить transcriber: {exc}")
        return None

    started = time.time()
    try:
        result = Transcriber().execute(
            {
                "input_path": str(source),
                "model_size": model_size,
                "language": language,
                "output_dir": str(project_dir),
                "diarize": False,
            }
        )
    except Exception as exc:  # noqa: BLE001
        util.log(f"      [!] Распознавание не удалось: {exc}")
        return None

    if not result.success:
        util.log(f"      [!] Распознавание не удалось: {result.error}")
        return None

    transcript = result.data
    payload = {
        "cache_key": cache_key,
        "created_at": util.iso_now(),
        "transcript": transcript,
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    util.log(
        f"      готово за {time.time() - started:.1f} с: "
        f"{len(transcript.get('segments', []))} фраз, "
        f"{len(transcript.get('word_timestamps', []))} слов, "
        f"язык {transcript.get('language', '?')}"
    )
    return transcript


def detect_scenes(source: Path, project_dir: Path, force: bool = False) -> list[dict]:
    """Возвращает список сцен. При ошибке — одна сцена на весь файл."""
    util.ensure_tool_paths()
    cache_path = project_dir / "scenes.json"
    if not force and cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(data.get("scenes"), list) and data["scenes"]:
                return data["scenes"]
        except (json.JSONDecodeError, OSError):
            pass

    _import_tools()
    try:
        from tools.analysis.scene_detect import SceneDetect

        result = SceneDetect().execute(
            {
                "input_path": str(source),
                "method": "content",
                "threshold": 27.0,
                "min_scene_length_seconds": 1.0,
                "output_path": str(cache_path),
            }
        )
        if result.success and result.data.get("scenes"):
            return result.data["scenes"]
    except Exception as exc:  # noqa: BLE001
        util.log(f"      [!] Определение сцен не удалось: {exc}")

    probe = util.probe_video(source)
    return [
        {
            "index": 0,
            "start_seconds": 0.0,
            "end_seconds": probe["duration"],
            "duration_seconds": probe["duration"],
        }
    ]
