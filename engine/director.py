"""«Режиссёр»: превращает транскрипт и сцены в план монтажа.

План — это список фрагментов исходника, которые нужно оставить, плюс титры,
главы и пословные субтитры, уже пересчитанные на таймлайн результата.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import util

SENTENCE_END = re.compile(r"[.!?…]+[\"'»)]*$")
WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()


def _short_title(text: str, max_words: int = 8, max_chars: int = 64) -> str:
    text = _clean_text(text).strip(" .,!?:;-—–")
    if not text:
        return "Без названия"
    words = text.split()
    title = " ".join(words[:max_words])
    if len(title) > max_chars:
        title = title[: max_chars - 1].rstrip() + "…"
    return title[:1].upper() + title[1:]


def _speech_regions(
    transcript: dict | None,
    duration: float,
    max_gap: float = 0.55,
    pad: float = 0.10,
) -> list[dict]:
    """Склеивает соседние фразы и убирает длинные паузы."""
    if not transcript:
        return []

    raw = []
    for segment in transcript.get("segments", []):
        try:
            start = float(segment.get("start"))
            end = float(segment.get("end"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        start = max(0.0, start - pad)
        end = min(duration, end + pad)
        raw.append(
            {
                "start": start,
                "end": end,
                "speech": max(0.0, min(duration, float(segment["end"])) - float(segment["start"])),
                "text": _clean_text(segment.get("text", "")),
            }
        )

    raw.sort(key=lambda item: item["start"])
    regions: list[dict] = []
    for item in raw:
        if regions and item["start"] - regions[-1]["end"] <= max_gap:
            previous = regions[-1]
            previous["end"] = max(previous["end"], item["end"])
            previous["speech"] += item["speech"]
            previous["text"] = _clean_text(previous["text"] + " " + item["text"])
        else:
            regions.append(dict(item))

    if regions:
        regions[0]["start"] = max(0.0, regions[0]["start"] - 0.05)
        regions[-1]["end"] = min(duration, regions[-1]["end"] + 0.25)
    return regions


def _chunks(regions: list[dict], target_chunk: float = 12.0) -> list[dict]:
    """Группирует фразы в смысловые куски примерно по target_chunk секунд."""
    chunks: list[dict] = []
    current: list[dict] = []
    for region in regions:
        current.append(region)
        span = current[-1]["end"] - current[0]["start"]
        if span >= target_chunk:
            chunks.append(_make_chunk(current, len(chunks)))
            current = []
    if current:
        chunks.append(_make_chunk(current, len(chunks)))
    return chunks


def _make_chunk(regions: list[dict], index: int) -> dict:
    span = max(0.001, regions[-1]["end"] - regions[0]["start"])
    speech = sum(item["speech"] for item in regions)
    words = sum(len(WORD_RE.findall(item["text"])) for item in regions)
    score = speech / span + 0.10 * min(1.0, words / 30.0)
    if index == 0:
        score += 0.35  # первая фраза почти всегда работает как хук
    return {
        "index": index,
        "regions": regions,
        "start": regions[0]["start"],
        "end": regions[-1]["end"],
        "span": span,
        "speech": speech,
        "words": words,
        "score": score,
    }


def _regions_duration(regions: list[dict]) -> float:
    """Суммарная длительность фрагментов (без вырезанных промежутков между ними)."""
    return sum(
        max(0.0, float(item["end"]) - float(item["start"])) for item in regions
    )


def _select_for_target(
    regions: list[dict], duration: float, target: float
) -> list[dict]:
    """Оставляет самые «плотные» куски, сохраняя их исходный порядок."""
    if target <= 0:
        return regions
    total = sum(item["end"] - item["start"] for item in regions)
    if total <= target * 1.02:
        return regions

    chunks = _chunks(regions)
    selected: set[int] = {0}
    # Считаем именно сумму длительностей выбранных фрагментов, а не span:
    # chunk["span"] — это расстояние от начала первого фрагмента до конца
    # последнего, включая вырезанные промежутки. Для разрозненных коротких
    # фрагментов (типичный план от LLM) span в разы больше реального вклада в
    # ролик, счётчик переполнялся досрочно и ролик выходил вдвое короче target.
    used = _regions_duration(chunks[0]["regions"]) if chunks else 0.0
    for chunk in sorted(chunks, key=lambda item: item["score"], reverse=True):
        if chunk["index"] in selected:
            continue
        if used >= target * 0.96:
            break
        selected.add(chunk["index"])
        used += _regions_duration(chunk["regions"])

    kept: list[dict] = []
    previous_index: int | None = None
    for chunk in sorted(chunks, key=lambda item: item["index"]):
        if chunk["index"] not in selected:
            continue
        for region in chunk["regions"]:
            item = dict(region)
            item["break_before"] = previous_index is not None and chunk["index"] != previous_index + 1
            kept.append(item)
        previous_index = chunk["index"]
    return _trim_to_target(kept, target)


def _trim_to_target(regions: list[dict], target: float) -> list[dict]:
    if target <= 0:
        return regions
    result: list[dict] = []
    used = 0.0
    for region in regions:
        length = region["end"] - region["start"]
        if used >= target:
            break
        if used + length > target:
            item = dict(region)
            item["end"] = item["start"] + (target - used)
            item["speech"] = min(item["speech"], item["end"] - item["start"])
            result.append(item)
            used = target
            break
        result.append(region)
        used += length
    return result


def _regions_from_ranges(ranges: list[list[float]], duration: float) -> list[dict]:
    regions = []
    for start, end in ranges:
        start = max(0.0, min(duration, float(start)))
        end = max(0.0, min(duration, float(end)))
        if end - start < 0.35:
            continue
        regions.append({"start": start, "end": end, "speech": end - start, "text": ""})
    regions.sort(key=lambda item: item["start"])
    merged: list[dict] = []
    for region in regions:
        if merged and region["start"] <= merged[-1]["end"] + 0.05:
            merged[-1]["end"] = max(merged[-1]["end"], region["end"])
            merged[-1]["speech"] += region["speech"]
        else:
            merged.append(region)
    return merged


def _build_segments(regions: list[dict], source_duration: float) -> list[dict]:
    segments = []
    cursor = 0.0
    for region in regions:
        length = max(0.05, region["end"] - region["start"])
        segment = {
            "source_in": round(region["start"], 3),
            "source_out": round(region["end"], 3),
            "out_start": round(cursor, 3),
            "out_end": round(cursor + length, 3),
            "text": _clean_text(region.get("text", "")),
            "break_before": bool(region.get("break_before", False)),
            "handle_after": round(max(0.0, source_duration - region["end"]), 3),
        }
        segments.append(segment)
        cursor += length
    return segments


def _map_captions(
    transcript: dict | None, segments: list[dict]
) -> list[dict]:
    if not transcript:
        return []
    words = transcript.get("word_timestamps") or []
    captions: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for segment in segments:
        source_in = segment["source_in"]
        source_out = segment["source_out"]
        out_start = segment["out_start"]
        out_end = segment["out_end"]
        segment_words: list[dict] = []

        for word in words:
            try:
                start = float(word.get("start"))
                end = float(word.get("end"))
            except (TypeError, ValueError):
                continue
            if end <= source_in or start >= source_out:
                continue
            text = _clean_text(str(word.get("word", "")))
            if not text:
                continue
            mapped_start = out_start + (max(start, source_in) - source_in)
            mapped_end = out_start + (min(end, source_out) - source_in)
            mapped_start = max(out_start, min(out_end - 0.02, mapped_start))
            mapped_end = max(mapped_start + 0.06, min(out_end, mapped_end))
            key = (text, int(round(mapped_start * 1000)))
            if key in seen:
                continue
            seen.add(key)
            item = {
                "word": text,
                "startMs": int(round(mapped_start * 1000)),
                "endMs": int(round(mapped_end * 1000)),
            }
            if SENTENCE_END.search(text):
                item["pageBreakAfter"] = True
            segment_words.append(item)
            captions.append(item)

        if segment_words:
            segment_words[-1]["pageBreakAfter"] = True

    captions.sort(key=lambda item: item["startMs"])
    return captions


def _auto_chapters(captions: list[dict], output_duration: float) -> list[dict]:
    if output_duration < 35:
        return []
    step = max(18.0, output_duration / 5.0)
    chapters = []
    at = step
    index = 1
    while at < output_duration - 6 and len(chapters) < 5:
        words = [item["word"] for item in captions if item["startMs"] / 1000 >= at][:6]
        text = _clean_text(" ".join(words)).strip(" .,!?:;-—–")
        chapters.append(
            {
                "at": round(at, 3),
                "text": _short_title(text, max_words=6, max_chars=42) if text else f"Часть {index}",
            }
        )
        at += step
        index += 1
    return chapters


def _ai_chapters(
    ai_chapters: list[dict], segments: list[dict], output_duration: float
) -> list[dict]:
    result = []
    for chapter in ai_chapters or []:
        try:
            source_at = float(chapter.get("at"))
        except (TypeError, ValueError):
            continue
        text = _clean_text(str(chapter.get("text", "")))
        if not text:
            continue
        for segment in segments:
            if segment["source_in"] <= source_at <= segment["source_out"]:
                at = segment["out_start"] + (source_at - segment["source_in"])
                if 4 < at < output_duration - 4:
                    result.append({"at": round(at, 3), "text": _short_title(text, 6, 42)})
                break
    return result[:5]


def build_plan(
    source: Path,
    probe: dict,
    transcript: dict | None,
    scenes: list[dict],
    *,
    target: float = 0.0,
    title: str | None = None,
    subtitle: str | None = None,
    end_text: str | None = None,
    ai_plan: dict | None = None,
) -> dict:
    duration = float(probe["duration"])
    regions: list[dict]

    if ai_plan and isinstance(ai_plan.get("keep"), list) and ai_plan["keep"]:
        regions = _regions_from_ranges(ai_plan["keep"], duration)
    else:
        regions = _speech_regions(transcript, duration)

    if not regions:
        # Нет речи: режем по сценам либо просто оставляем начало.
        scene_ranges = [
            [float(scene["start_seconds"]), float(scene["end_seconds"])]
            for scene in scenes
            if float(scene.get("end_seconds", 0)) > float(scene.get("start_seconds", 0))
        ]
        regions = _regions_from_ranges(scene_ranges, duration)
        if not regions:
            limit = target if target > 0 else duration
            regions = [{"start": 0.0, "end": min(duration, limit), "speech": min(duration, limit), "text": ""}]

    if target > 0:
        regions = _select_for_target(regions, duration, target)

    segments = _build_segments(regions, duration)
    output_duration = segments[-1]["out_end"] if segments else 0.0
    captions = _map_captions(transcript, segments)

    first_text = ""
    if transcript:
        for segment in transcript.get("segments", []):
            first_text = _clean_text(segment.get("text", ""))
            if first_text:
                break

    resolved_title = (
        title
        or (ai_plan or {}).get("title")
        or _short_title(first_text)
        or _short_title(source.stem.replace("_", " "))
    )
    resolved_subtitle = (
        subtitle
        or (ai_plan or {}).get("subtitle")
        or f"OpenMontage + Remotion · {util.human_time(output_duration)}"
    )

    chapters = []
    if ai_plan and isinstance(ai_plan.get("chapters"), list):
        chapters = _ai_chapters(ai_plan["chapters"], segments, output_duration)
    if not chapters:
        chapters = _auto_chapters(captions, output_duration)

    removed = max(0.0, duration - output_duration)
    return {
        "version": 1,
        "source": str(source),
        "language": (transcript or {}).get("language", "unknown"),
        "title": resolved_title,
        "subtitle": resolved_subtitle,
        "end_card": {
            "text": end_text or "Спасибо за просмотр",
            "subtitle": resolved_title,
            "duration": 2.8,
        },
        "segments": segments,
        "captions": captions,
        "chapters": chapters,
        "stats": {
            "source_duration": round(duration, 3),
            "output_duration": round(output_duration, 3),
            "removed_seconds": round(removed, 3),
            "removed_percent": round(removed / duration * 100, 1) if duration else 0.0,
            "segment_count": len(segments),
            "word_count": len(captions),
            "scene_count": len(scenes),
        },
    }
