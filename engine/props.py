"""Сборка props для Remotion-композиции Montage."""

from __future__ import annotations

STYLES: dict[str, dict] = {
    "clean": {
        "accent": "#22D3EE",
        "background": "#0B1220",
        "text": "#F8FAFC",
        "captionBackground": "rgba(15, 23, 42, 0.78)",
        "captionSize": 42,
        "wordsPerPage": 5,
        "transition": "fade",
        "transitionDuration": 0.35,
    },
    "dynamic": {
        "accent": "#FACC15",
        "background": "#09090B",
        "text": "#FAFAFA",
        "captionBackground": "rgba(9, 9, 11, 0.72)",
        "captionSize": 56,
        "wordsPerPage": 4,
        "transition": "slide",
        "transitionDuration": 0.28,
    },
    "cinematic": {
        "accent": "#F5C470",
        "background": "#0A0A0A",
        "text": "#F5F5F4",
        "captionBackground": "rgba(0, 0, 0, 0.55)",
        "captionSize": 44,
        "wordsPerPage": 6,
        "transition": "fade",
        "transitionDuration": 0.55,
    },
}


def _resolution(probe: dict, mode: str) -> tuple[int, int]:
    width = int(probe.get("width") or 1920)
    height = int(probe.get("height") or 1080)
    if mode == "vertical":
        return 1080, 1920
    if mode == "square":
        return 1080, 1080
    if mode == "landscape":
        return 1920, 1080
    if height > width * 1.08:
        return 1080, 1920
    if abs(width - height) <= max(width, height) * 0.12:
        return 1080, 1080
    return 1920, 1080


def build_props(
    plan: dict,
    probe: dict,
    *,
    style: str = "clean",
    fmt: str = "auto",
    source_asset: str,
    audio_asset: str | None,
    music_asset: str | None,
    music_volume: float = 0.08,
    captions: bool = True,
    titles: bool = True,
    end_card: bool = True,
) -> dict:
    preset = STYLES.get(style, STYLES["clean"])
    width, height = _resolution(probe, fmt)
    fps = int(round(float(probe.get("fps") or 30)))
    fps = min(30, max(12, fps))

    segments = plan["segments"]
    clips: list[dict] = []
    for index, segment in enumerate(segments):
        duration = round(segment["out_end"] - segment["out_start"], 3)
        if duration <= 0:
            continue
        is_first = index == 0
        is_last = index == len(segments) - 1
        incoming = "fade" if is_first else (
            preset["transition"] if segment.get("break_before") else "cut"
        )
        fade_out = 0.0
        if is_first:
            tail = min(0.45, duration * 0.35)
        elif is_last:
            if end_card:
                tail = min(0.45, duration * 0.25)
            else:
                tail = 0.0
                fade_out = min(0.45, duration * 0.3)
        else:
            handle = float(segment.get("handle_after") or 0.0)
            tail = min(preset["transitionDuration"], handle, duration * 0.5)
        clips.append(
            {
                "kind": "video",
                "src": source_asset,
                "sourceStart": segment["source_in"],
                "duration": duration,
                "tail": round(tail, 3),
                "fadeOut": round(fade_out, 3),
                "transitionIn": incoming,
                "transitionDuration": preset["transitionDuration"],
            }
        )

    if end_card:
        clips.append(
            {
                "kind": "card",
                "duration": float(plan["end_card"].get("duration", 2.8)),
                "title": plan["end_card"]["text"],
                "subtitle": plan["end_card"].get("subtitle", ""),
                "transitionIn": "fade",
                "transitionDuration": 0.5,
                "tail": 0.0,
                "fadeOut": 0.4,
            }
        )

    first_duration = clips[0]["duration"] if clips else 0.0
    title_overlay = None
    if titles and clips and first_duration > 0.9:
        title_overlay = {
            "text": plan["title"],
            "subtitle": plan["subtitle"],
            "at": 0.35,
            "duration": round(min(3.3, max(1.2, first_duration - 0.2)), 3),
        }

    chapters = []
    if titles:
        for chapter in plan.get("chapters", []):
            if title_overlay and chapter["at"] < title_overlay["at"] + title_overlay["duration"] + 1.0:
                continue
            chapters.append({"at": chapter["at"], "text": chapter["text"]})

    return {
        "fps": fps,
        "width": width,
        "height": height,
        "clips": clips,
        "captions": plan.get("captions", []) if captions else [],
        "captionsEnabled": bool(captions and plan.get("captions")),
        "wordsPerPage": preset["wordsPerPage"],
        "captionFontSize": preset["captionSize"],
        "accentColor": preset["accent"],
        "backgroundColor": preset["background"],
        "textColor": preset["text"],
        "captionBackgroundColor": preset["captionBackground"],
        "title": title_overlay,
        "chapters": chapters,
        "audio": {"src": audio_asset, "volume": 1.0} if audio_asset else None,
        "music": (
            {"src": music_asset, "volume": music_volume, "fadeIn": 1.5, "fadeOut": 2.5}
            if music_asset
            else None
        ),
        "style": style,
        "source": source_asset,
        "meta": {
            "title": plan["title"],
            "sourceDuration": plan["stats"]["source_duration"],
            "outputDuration": plan["stats"]["output_duration"],
            "segments": len(segments),
        },
    }
