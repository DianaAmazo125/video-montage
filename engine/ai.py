"""Необязательный «умный» режим: план монтажа через LLM (для видео с речью и без речи)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from . import util

SKILL_FILES = [
    "skills/pipelines/talking-head/script-director.md",
    "skills/pipelines/talking-head/edit-director.md",
]

# Сколько раз просить LLM добрать секунды, если сумма keep короче min_target.
# Модель сходится к цели постепенно (на KVS: 15 -> 20 -> 23 -> ... с), поэтому
# двух попыток может не хватить: значение вынесено, чтобы его можно было
# подстроить под конкретную модель.
MAX_ADD_ATTEMPTS = 4


def _skill_context(max_chars: int = 7000) -> str:
    chunks = []
    for relative in SKILL_FILES:
        path = util.OPENMONTAGE / relative
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunks.append(f"### {relative}\n{text[: max_chars // len(SKILL_FILES)]}")
    return "\n\n".join(chunks)


def _extract_json(content: str) -> dict | None:
    content = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    if fence:
        content = fence.group(1).strip()
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            value = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _keep_total(plan: dict | None) -> float:
    """Суммарная длительность всех отрезков в keep (в секундах)."""
    if not isinstance(plan, dict):
        return 0.0
    total = 0.0
    for item in plan.get("keep") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            start, end = float(item[0]), float(item[1])
        except (TypeError, ValueError):
            continue
        if end > start:
            total += end - start
    return total


def _request_plan(model: str, api_key: str, system: str, user: str) -> dict | None:
    """Один запрос к LLM; возвращает разобранный план или None."""
    body = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    base_url = os.environ.get("OPENAI_BASE_URL", "https://openai.com").rstrip("/")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"].pop(0)["message"]["content"]
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as exc:
        util.log(f"      [!] LLM недоступна ({exc}) — работаю без неё")
        return None

    plan = _extract_json(content)
    if not plan:
        util.log("      [!] LLM вернула не-JSON — работаю без неё")
        return None
    return plan


def _shortfall_note(
    shortfall: float,
    min_target: float,
    eff_target: float,
    has_speech: bool,
) -> str:
    """Явная просьба добрать недостающие секунды для повторного запроса.

    Формулировка намеренно «арифметическая»: модель, которой просто велят
    «добрать фрагменты», часто возвращает план КОРОЧЕ прежнего. Когда ей дают
    посчитать сумму, назвать число недостающих секунд и прикинуть количество
    фрагментов, она попадает в диапазон с первого-второго добора.
    """
    estimated = min_target - shortfall
    count = max(1, int(min_target / 3.5) + 1)
    boundary = (
        "Границы новых фрагментов бери по границам фраз из транскрипта."
        if has_speech
        else "Границы новых фрагментов бери по границам сцен, используй куски по 4-6 с."
    )
    return (
        f"Твой прошлый план НЕ ПРОШЁЛ ПРОВЕРКУ по длительности. Посчитай: сумма всех пар "
        f"[start, end] в keep должна быть от {min_target:.0f} до {eff_target:.0f} с, "
        f"а в прошлом плане она примерно {estimated:.1f} с. Не хватает {shortfall:.1f} с. "
        f"Это примерно {count} фрагментов по 3-4 секунды. "
        "Верни ПОЛНЫЙ JSON целиком: сохрани прежние сильные фрагменты и добавь столько новых "
        f"участков исходника, чтобы СУММА всех отрезков попала в {min_target:.0f}-{eff_target:.0f} с. "
        "Можно брать любые участки исходника, не только начало. "
        f"{boundary} "
        "Перед ответом сложи длительности всех отрезков и убедись, что сумма не меньше "
        f"{min_target:.0f} с. Не разрывай слова и фразы и сохраняй порядок по времени."
    )


def ask_for_plan(
    probe: dict,
    transcript: dict | None,
    scenes: list[dict],
    *,
    model: str,
    target: float = 0.0,
) -> dict | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        util.log("      [!] OPENAI_API_KEY не задан — работаю без LLM (локальный режиссёр)")
        return None

    has_speech = bool(transcript and transcript.get("segments"))
    duration = float(probe["duration"])
    eff_target = target if target > 0 else min(duration, 25.0)
    min_target = max(5.0, eff_target - 3.0)

    scene_lines = [
        f"{float(s.get('start_seconds', 0)):.2f}-{float(s.get('end_seconds', 0)):.2f}"
        for s in scenes[:120]
    ]

    if has_speech:
        lines = [
            f"{index}\t{float(segment['start']):.2f}\t{float(segment['end']):.2f}\t"
            f"{str(segment.get('text', '')).strip()}"
            for index, segment in enumerate(transcript["segments"])
        ]
        system = (
            "Ты — режиссёр монтажа OpenMontage. Твоя задача: из сырого видео собрать "
            "короткий, связный ролик. Верни ТОЛЬКО JSON без пояснений.\n"
            "Схема: {\"title\": str, \"subtitle\": str, "
            "\"keep\": [[start_sec, end_sec], ...], "
            "\"chapters\": [{\"at\": source_sec, \"text\": str}], "
            "\"style\": \"clean\"|\"dynamic\"|\"cinematic\"}.\n"
            f"Правила: не разрывай слова и фразы; сохраняй порядок фрагментов; "
            f"начинай с сильного хука; ОБЯЗАТЕЛЬНО набери сумму длительностей в keep "
            f"строго в пределах от {min_target:.0f} до {eff_target:.0f} секунд (не меньше!); "
            "границы — по границам фраз из транскрипта.\n\n"
            + _skill_context()
        )
        user = (
            f"Длительность исходника: {duration:.2f} с.\n"
            f"Требуемая длительность: строго {min_target:.0f}-{eff_target:.0f} с.\n"
            f"Сцены (сек): {', '.join(scene_lines) or 'нет данных'}\n\n"
            "Транскрипт (index, start, end, text):\n"
            + "\n".join(lines[:1200])
        )
    else:
        util.log("      [*] Речи в видео нет — включаю визуальный режим режиссёра по сценам")
        system = (
            "Ты — вирусный режиссёр монтажа для Reels, Shorts и TikTok. В видео нет речи, "
            "есть только визуальные сцены. Собери из самых динамичных и цепляющих отрезков "
            "короткий ролик с бодрым темпом (смена кадров каждые 2-4 секунды). "
            "Верни ТОЛЬКО JSON без пояснений.\n"
            "Схема: {\"title\": str, \"subtitle\": str, "
            "\"keep\": [[start_sec, end_sec], ...], "
            "\"chapters\": [{\"at\": source_sec, \"text\": str}], "
            "\"style\": \"dynamic\"}.\n"
            f"Правила: сохраняй хронологический порядок; ОБЯЗАТЕЛЬНО набери сумму длительностей "
            f"в keep строго от {min_target:.0f} до {eff_target:.0f} секунд (не короче!); "
            "начинай с самого активного фрагмента; придумай короткий живой title и subtitle.\n"
            "ВАЖНО про chapters: это КОРОТКИЕ ОСМЫСЛЕННЫЕ ПОДПИСИ ДЛЯ ЗРИТЕЛЯ, которые "
            "показываются на экране поверх видео. Пиши их на том же языке, что title и "
            "subtitle (если ролик русский — по-русски), 2-4 слова, по существу происходящего "
            "в кадре, с реальными таймкодами исходника по возрастанию. "
            "КАТЕГОРИЧЕСКИ НЕЛЬЗЯ писать в chapters названия этапов планирования, служебные "
            "или технические формулировки, англоязычные ярлыки внутренних шагов "
            "(вроде \"Segmenting into Sections\", \"Building Script Artifact\", "
            "\"Enhancing Section Metadata\", \"Self-Evaluation Criteria\") — зритель их видит "
            "на видео, и это выглядит как технический мусор. 3-6 глав на ролик."
        )
        user = (
            f"Длительность исходника: {duration:.2f} с.\n"
            f"Требуемая длительность: строго {min_target:.0f}-{eff_target:.0f} с.\n"
            f"Обнаруженные сцены (start-end в сек): {', '.join(scene_lines) or f'0.00-{duration:.2f}'}\n"
            "Выбери лучшие визуальные фрагменты под заданную длительность."
        )

    plan = _request_plan(model, api_key, system, user)
    if not plan:
        return None

    total = _keep_total(plan)
    util.log(
        f"      LLM предложила план: {len(plan.get('keep', []))} фрагментов, "
        f"сумма {total:.1f} с (нужно {min_target:.0f}-{eff_target:.0f} с)"
    )

    # Пост-проверка: если сумма короче min_target, просим добрать. Только когда
    # исходник реально длиннее min_target — иначе добрать физически нечего.
    for _ in range(MAX_ADD_ATTEMPTS):
        if total >= min_target or duration <= min_target:
            break
        shortfall = min_target - total
        util.log(
            f"      [!] короче цели на {shortfall:.1f} с — прошу LLM добрать фрагменты"
        )
        extra = _request_plan(
            model,
            api_key,
            system,
            user + "\n\n" + _shortfall_note(shortfall, min_target, eff_target, has_speech),
        )
        new_total = _keep_total(extra)
        if not extra or new_total <= total:
            util.log("      [!] добрать не удалось — оставляю прежний план")
            break
        plan, total = extra, new_total
        util.log(
            f"      [+] после добора: {len(plan.get('keep', []))} фрагментов, "
            f"сумма {total:.1f} с"
        )
    else:
        if total < min_target and duration > min_target:
            util.log(
                f"      [!] после {MAX_ADD_ATTEMPTS} доборов всё ещё {total:.1f} с "
                f"(короче {min_target:.0f} с) — оставляю как есть"
            )

    return plan
