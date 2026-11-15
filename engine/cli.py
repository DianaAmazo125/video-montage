# -*- coding: utf-8 -*-
"""CLI автоматического монтажа: видеофайл на входе — готовый ролик на выходе."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import ai, analyze, director, props as props_builder, render, util

SUPPORTED = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="montage",
        description="Автоматический монтаж: OpenMontage анализирует, Remotion рендерит.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            '  montage.cmd "C:\\video\\interview.mp4"\n'
            '  montage.cmd "C:\\video\\webinar.mp4" --target 60 --style dynamic\n'
            '  montage.cmd "C:\\video\\clip.mp4" --format vertical --music music.mp3\n'
        ),
    )
    parser.add_argument("input", nargs="?", help="исходный видеофайл")
    parser.add_argument("--out", help="куда сохранить результат (файл .mp4 или папка)")
    parser.add_argument("--name", help="имя проекта (папка в projects/)")
    parser.add_argument(
        "--target",
        type=float,
        default=0,
        help="желаемая длительность ролика в секундах (0 = оставить всё)",
    )
    parser.add_argument("--title", help="заголовок ролика")
    parser.add_argument("--subtitle", help="подзаголовок")
    parser.add_argument("--end-text", help="текст финальной карточки")
    parser.add_argument(
        "--style",
        choices=sorted(props_builder.STYLES),
        default="clean",
        help="визуальный стиль (по умолчанию clean)",
    )
    parser.add_argument(
        "--format",
        choices=["auto", "landscape", "vertical", "square"],
        default="auto",
        help="формат кадра (по умолчанию по ориентации исходника)",
    )
    parser.add_argument(
        "--lang",
        default="auto",
        help="язык речи: auto, ru, en, ... (по умолчанию auto)",
    )
    parser.add_argument(
        "--model",
        default="small",
        help="модель распознавания: base, small, medium, large-v3 (по умолчанию small)",
    )
    parser.add_argument("--music", help="фоновая музыка (mp3/wav/m4a)")
    parser.add_argument(
        "--music-replace",
        action="store_true",
        help="заменить исходный звук музыкой из --music (а не подмешать её тише)",
    )
    parser.add_argument(
        "--music-volume",
        type=float,
        default=0.08,
        help="громкость музыки 0..1 (по умолчанию 0.08)",
    )
    parser.add_argument("--no-captions", action="store_true", help="без субтитров")
    parser.add_argument("--no-titles", action="store_true", help="без титров и глав")
    parser.add_argument("--no-end-card", action="store_true", help="без финальной карточки")
    parser.add_argument(
        "--ai",
        action="store_true",
        help="попросить LLM выбрать фрагменты (нужен OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--ai-model",
        default=os.environ.get("OPENMONTAGE_AI_MODEL", "gpt-4.1-mini"),
        help="модель для --ai",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="заново распознать речь и сцены (игнорировать кэш)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только проанализировать и собрать план, без рендера",
    )
    return parser.parse_args(argv)


def _check_environment() -> None:
    if not util.VENV_PY.is_file():
        raise RuntimeError(
            "Не найдено Python-окружение OpenMontage.\n"
            f"Ожидался файл: {util.VENV_PY}\n"
            "Создайте его: python -m uv venv --python 3.12 vendor/OpenMontage/.venv"
        )
    if not (util.COMPOSER / "node_modules" / "remotion").is_dir():
        raise RuntimeError(
            "Не установлены npm-пакеты Remotion.\n"
            f"Выполните: cd {util.COMPOSER} && npm install"
        )
    util.ensure_tool_paths()


def _resolve_output(project_dir: Path, value: str | None) -> Path:
    if not value:
        return project_dir / "output.mp4"
    target = Path(value).expanduser().resolve()
    if target.suffix.lower() == ".mp4":
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    target.mkdir(parents=True, exist_ok=True)
    return target / f"{project_dir.name}_montage.mp4"


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    args = _parse_args(argv)
    if not args.input:
        util.log('Укажите видеофайл. Пример: montage.cmd "C:\\video\\input.mp4"')
        util.log("Полная справка: montage.cmd --help")
        return 1
    started = time.time()
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        util.log(f"[ОШИБКА] Файл не найден: {source}")
        return 1
    if source.suffix.lower() not in SUPPORTED:
        util.log(
            f"[ОШИБКА] Формат {source.suffix} не поддержан. "
            f"Можно: {', '.join(sorted(SUPPORTED))}"
        )
        return 1

    music = Path(args.music).expanduser().resolve() if args.music else None
    if music and not music.is_file():
        util.log(f"[ОШИБКА] Музыка не найдена: {music}")
        return 1
    if args.music_replace and not music:
        util.log("[ОШИБКА] --music-replace требует --music: нечем заменять звук")
        return 1
    keep_source_audio = not (args.music_replace and music)

    try:
        _check_environment()
    except RuntimeError as exc:
        util.log(f"[ОШИБКА] {exc}")
        return 1

    project_name = args.name or source.stem
    slug = util.slugify(Path(project_name))
    project_dir = util.PROJECTS / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    util.log("=" * 64)
    util.log("  АВТОМАТИЧЕСКИЙ МОНТАЖ  ·  OpenMontage + Remotion")
    util.log("=" * 64)
    util.log(f"  Исходник : {source}")
    util.log(f"  Проект   : {project_dir}")
    util.log("")

    try:
        util.log("[1/7] Анализирую видео...")
        probe = util.probe_video(source)
        util.log(
            f"      {probe['width']}x{probe['height']}, {probe['fps']:.2f} fps, "
            f"{util.human_time(probe['duration'])}, звук: "
            f"{'да' if probe['has_audio'] else 'нет'}"
        )

        util.log("[2/7] Распознаю речь...")
        transcript = None
        if probe["has_audio"]:
            language = None if args.lang == "auto" else args.lang
            transcript = analyze.transcribe(
                source,
                project_dir,
                model_size=args.model,
                language=language,
                force=args.force,
            )
            if transcript is None:
                util.log("      продолжаю без транскрипта (нарезка по сценам)")
        else:
            util.log("      звука нет — пропускаю")

        util.log("[3/7] Определяю границы сцен...")
        scenes = analyze.detect_scenes(source, project_dir, force=args.force)
        util.log(f"      найдено сцен: {len(scenes)}")

        util.log("[4/7] Строю план монтажа...")
        ai_plan = None
        if args.ai:
            ai_plan = ai.ask_for_plan(
                probe,
                transcript,
                scenes,
                model=args.ai_model,
                target=args.target,
            )
        plan = director.build_plan(
            source,
            probe,
            transcript,
            scenes,
            target=args.target,
            title=args.title,
            subtitle=args.subtitle,
            end_text=args.end_text,
            ai_plan=ai_plan,
        )
        (project_dir / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        stats = plan["stats"]
        util.log(
            f"      оставляю {stats['segment_count']} фрагментов, "
            f"вырезано {stats['removed_percent']:.0f}% времени "
            f"({util.human_time(stats['removed_seconds'])})"
        )
        util.log(
            f"      результат: {util.human_time(stats['output_duration'])}, "
            f"субтитров: {stats['word_count']} слов"
        )
        util.log(f"      заголовок: «{plan['title']}»")

        util.log("[5/7] Готовлю звук и ассеты...")
        if keep_source_audio:
            audio_bed_path = project_dir / "audio_bed.m4a"
            has_bed = util.build_audio_bed(source, plan["segments"], audio_bed_path)
        else:
            # Звук заменяется музыкой: дорожку не собираем и не отдаём в props,
            # иначе исходный звук лёг бы поверх музыки на полной громкости.
            audio_bed_path = None
            has_bed = False
            util.log("      исходный звук отключён — в ролике только музыка")
        assets = render.stage_assets(
            slug,
            source,
            audio_bed_path if has_bed else None,
            music,
        )
        util.log("      ассеты скопированы в public/ Remotion")

        remotion_props = props_builder.build_props(
            plan,
            probe,
            style=args.style,
            fmt=args.format,
            source_asset=assets["source"],
            audio_asset=assets["audio"],
            music_asset=assets["music"],
            music_volume=(
                args.music_volume
                if keep_source_audio
                else max(args.music_volume, 0.6)
            ),
            captions=not args.no_captions,
            titles=not args.no_titles,
            end_card=not args.no_end_card,
        )
        props_path = render.write_props(project_dir, remotion_props)
        util.log(f"      props: {props_path.name}")

        if args.dry_run:
            util.log("")
            util.log("[dry-run] План готов, рендер пропущен.")
            util.log(f"  План   : {project_dir / 'plan.json'}")
            util.log(f"  Props  : {props_path}")
            return 0

        util.log("[6/7] Рендерю в Remotion (самый долгий шаг)...")
        output = render.render(project_dir, props_path, slug)

        util.log("[7/7] Проверяю результат...")
        final_probe = util.probe_video(output)
        thumbs = util.make_thumbnails(output, project_dir / "preview")
        destination = _resolve_output(project_dir, args.out)
        if destination.resolve() != output.resolve():
            shutil.copy2(output, destination)

        elapsed = time.time() - started
        util.log("")
        util.log("=" * 64)
        util.log("  ГОТОВО")
        util.log("=" * 64)
        util.log(f"  Файл      : {destination}")
        util.log(
            f"  Длительность: {util.human_time(final_probe['duration'])} "
            f"(было {util.human_time(probe['duration'])})"
        )
        util.log(
            f"  Кадр      : {final_probe['width']}x{final_probe['height']}, "
            f"{final_probe['fps']:.0f} fps"
        )
        util.log(f"  Размер    : {util.human_size(final_probe['size_bytes'])}")
        util.log(f"  Время     : {util.human_time(elapsed)}")
        if thumbs:
            util.log(f"  Превью    : {thumbs[0].parent}")
        util.log("")
        return 0
    except KeyboardInterrupt:
        util.log("\n[ОСТАНОВЛЕНО] Пользователь прервал выполнение.")
        return 130
    except RuntimeError as exc:
        util.log("")
        util.log(f"[ОШИБКА] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
