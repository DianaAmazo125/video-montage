# Автоматический монтаж видео — OpenMontage + Remotion

Движок берёт **один видеофайл** и сам делает из него готовый ролик: распознаёт речь,
вырезает паузы и «воду», оставляет самые плотные фрагменты, добавляет заголовок,
пословные субтитры, главы и финальную карточку. На выходе — обычный `mp4` (H.264 + AAC).

```
видео.mp4 → ffprobe → faster-whisper (речь) → scene detect (сцены)
          → «режиссёр» (план) → ffmpeg (звук без пауз) → Remotion (рендер) → output.mp4
```

Проект рассчитан на Windows: обёртка `montage.cmd`, поиск `ffmpeg`/`node` в стандартных
местах установки, UTF-8 в консоли.

## Что реально используется

**Python (папка `engine/`), только стандартная библиотека** — `argparse`, `subprocess`,
`json`, `urllib`, `pathlib`. Своего `requirements.txt` у движка нет:

- **ffmpeg / ffprobe** через `subprocess`: метаданные файла, сборка звука фильтрами
  `atrim` + `concat` с фейдами, превью-кадры.
- **faster-whisper** — речь и пословные таймкоды, через
  `tools.analysis.transcriber.Transcriber` из `vendor/OpenMontage`
  (модели `base`…`large-v3`, по умолчанию `small`).
- **Определение сцен** — `tools.analysis.scene_detect.SceneDetect`: метод `content`,
  порог 27.0, минимальная сцена 1 с.
- **LLM для `--ai`** — HTTP-запрос через `urllib.request` к OpenAI-совместимому
  `/chat/completions` (в `.env` прописан `https://api.proxyapi.ru/v1`). В промпт
  подмешиваются инструкции из `vendor/OpenMontage/skills/pipelines/talking-head/*.md`.

**Node.js:** **Remotion 4.x** — композиция `Montage` в `vendor/OpenMontage/remotion-composer`,
рендер `npx remotion render src/index.tsx Montage --codec=h264 --crf=18`. В её
`package.json`: `@remotion/cli`, `@remotion/captions`, `@remotion/media`,
`@remotion/transitions`, `@remotion/google-fonts`, `react` 18. Компоненты `HeroTitle`,
`SectionTitle` и `CaptionOverlay` (пословные субтитры с подсветкой текущего слова)
взяты из OpenMontage.

**Что в `vendor/`:** `OpenMontage/` — вендорная копия репозитория OpenMontage (анализ
в `tools/`, промпты в `skills/`, композиция в `remotion-composer/`, Python-окружение
`.venv` на Python 3.12). `remotion/` — копия монорепозитория Remotion; на работу движка
напрямую не влияет, рендер использует `node_modules` из `remotion-composer`.

## Про папку `vendor/` — её нет в репозитории

Сам движок — это только папка `engine/` (12 файлов, ~84 КБ). Всё остальное он берёт из `vendor/`,
а там лежат сторонние копии двух чужих проектов: **~2,2 ГБ, более 40 000 файлов**, и внутри каждой
копии — свой `.git`. Публиковать это в GitHub нельзя: лимит 100 МиБ на файл, а вложенные `.git`
превратились бы в битые ссылки вместо кода. Поэтому `vendor/` целиком исключён через `.gitignore`.

После клонирования репозитория папку нужно восстановить:

```powershell
git clone https://github.com/calesthio/OpenMontage.git vendor\OpenMontage
git clone --filter=blob:none https://github.com/remotion-dev/remotion.git vendor\remotion
```

Версии, на которых всё собиралось: OpenMontage `main` (d21f5d4, 03.11.2026) и
Remotion `main` (af6c460, 04.11.2026). Если в вашей копии есть локальные правки
(свои промпты в `skills/`, изменённые компоненты в `remotion-composer/`) — их нужно
перенести отдельно, клон их не вернёт.

## Установка

Всё уже установлено на этой машине; раздел — на случай переезда.

**1. Node.js и ffmpeg**

```powershell
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg
```

Проверка в новом окне PowerShell: `node --version`, `npm --version`, `ffmpeg -version`.
Проверено на Node.js 26.x / npm 11.x и ffmpeg 9.0.2. Движок сам находит ffmpeg
в `WinGet\Packages` и Node в `C:\Program Files\nodejs`, если PATH ещё не обновился.

**2. Python-окружение OpenMontage (нужен Python 3.12)**

```powershell
python -m uv venv --python 3.12 vendor\OpenMontage\.venv
```

Именно 3.12: на более новых версиях часть зависимостей не собирается. `montage.cmd`
отказывается работать, если не находит `vendor\OpenMontage\.venv\Scripts\python.exe`.

**3. Зависимости Python**

```powershell
vendor\OpenMontage\.venv\Scripts\python.exe -m uv pip install --python vendor\OpenMontage\.venv\Scripts\python.exe -r vendor\OpenMontage\requirements.txt
vendor\OpenMontage\.venv\Scripts\python.exe -m uv pip install --python vendor\OpenMontage\.venv\Scripts\python.exe "av==16.0.1" faster-whisper
```

`faster-whisper` требует `av==16.0.1`: с более новым `av` импорт падает с `metadata_errors`.

**4. Зависимости Remotion**

```powershell
cd vendor\OpenMontage\remotion-composer
npm install
npm install-scripts approve esbuild
```

Вторая команда нужна для npm 11 — без неё установка esbuild блокируется. Браузер
для рендера Remotion скачивает сам при первом запуске.

**5. Проверка** — подставьте любой свой ролик (папка `demo/` в репозиторий не попадает):

```powershell
.\montage.cmd "C:\путь\к\видео.mp4" --target 20 --model base --dry-run
```

Команда напечатает план: сколько фрагментов оставлено, сколько времени вырезано, какая
получится длительность. Флаг `--dry-run` останавливает работу до рендера.

## Переменные окружения

`python-dotenv` движок не использует: `engine/cli.py` и `engine/ai.py` читают только
`os.environ`, поэтому `.env` надо не только создать, но и загрузить в текущую сессию
перед запуском:

```powershell
Copy-Item .env.example .env
```

| Переменная | Где читается | Зачем |
|---|---|---|
| `OPENAI_API_KEY` | `engine/ai.py` | ключ для `--ai`; без него движок уходит в локальный режим |
| `OPENAI_BASE_URL` | `engine/ai.py` | база OpenAI-совместимого API; код сам добавляет `/chat/completions` |
| `OPENMONTAGE_AI_MODEL` | `engine/cli.py` | модель по умолчанию для `--ai-model` (иначе `gpt-4.1-mini`) |

Для ProxyAPI `OPENAI_BASE_URL` обязан заканчиваться на `/v1` — без `/v1` эндпоинт
отдаёт HTML с кодом 404:

```ini
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
OPENAI_BASE_URL=https://api.proxyapi.ru/v1
OPENMONTAGE_AI_MODEL=deepseek/deepseek-chat
```

```powershell
# загрузить .env в текущую сессию PowerShell
Get-Content .env | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
  $name, $value = $_ -split '=', 2
  Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim()
}
```

## Запуск

```powershell
.\montage.cmd "C:\путь\к\видео.mp4"
```

Результат — `projects\<проект>\output.mp4` (путь печатается в конце работы) и превью-кадры
`projects\<проект>\preview\preview_1..3.jpg`. Поддерживаются `.mp4`, `.mov`, `.mkv`,
`.webm`, `.avi`, `.m4v`.

```powershell
# Вертикальный ролик для Reels/Shorts
.\montage.cmd "C:\video\interview.mp4" --target 45 --format vertical --style dynamic

# Музыка: с --music-replace она заменяет исходный звук, а не подмешивается
.\montage.cmd "C:\video\webinar.mp4" --target 60 --music "C:\music\track.mp3" --music-replace

# Свои тексты и отбор фрагментов через LLM
.\montage.cmd "C:\video\clip.mp4" --title "Как это работает" --ai --target 40

# Только план, без рендера
.\montage.cmd "C:\video\clip.mp4" --target 40 --dry-run
```

| Параметр | Что делает | По умолчанию |
|---|---|---|
| `input` | исходный видеофайл | обязателен |
| `--out` | файл `.mp4` или папка для результата | `projects\<проект>\output.mp4` |
| `--name` | имя проекта (папка в `projects/`) | имя исходного файла |
| `--target` | желаемая длительность, `0` — оставить всё | `0` |
| `--style` | `clean`, `dynamic`, `cinematic` | `clean` |
| `--format` | `auto`, `landscape`, `vertical`, `square` | `auto` |
| `--lang` / `--model` | язык речи и модель распознавания | `auto` / `small` |
| `--music`, `--music-volume`, `--music-replace` | музыка, её громкость, замена звука | нет / `0.08` / выкл. |
| `--title`, `--subtitle`, `--end-text` | титры и финальная карточка | из первой фразы / авто / «Спасибо за просмотр» |
| `--no-captions`, `--no-titles`, `--no-end-card` | отключить субтитры, титры, карточку | выкл. |
| `--ai`, `--ai-model` | план от LLM | выкл. |
| `--force` / `--dry-run` | игнорировать кэш / остановиться после плана | выкл. |

Полная справка — `.\montage.cmd --help`. Стили заданы в `engine/props.py` (акцентный цвет,
размер субтитров, слов на страницу, тип перехода). Форматы: `landscape` 1920×1080,
`vertical` 1080×1920, `square` 1080×1080, `auto` — по ориентации исходника. При смене
ориентации видео вписывается с обрезкой краёв — для вертикальных роликов из
горизонтального исходника это нормально.

## Что происходит внутри

| Шаг | Файл | Что делает |
|---|---|---|
| 1. Анализ | `engine/util.py` | `ffprobe`: длительность, разрешение, fps, наличие звука |
| 2. Речь | `engine/analyze.py` | `Transcriber` (faster-whisper), кэш в `transcript.json` |
| 3. Сцены | `engine/analyze.py` | `SceneDetect`, кэш в `scenes.json` |
| 4. План | `engine/director.py` | склеивает фразы, режет паузы, отбирает куски под `--target`, строит субтитры и главы |
| 5. LLM (опция) | `engine/ai.py` | план от модели с пост-проверкой длительности; без ключа работает локальный «режиссёр» |
| 6. Звук | `engine/util.py` | ffmpeg собирает аудио только из оставленных фрагментов (`audio_bed.m4a`) |
| 7. Props | `engine/props.py` | стиль, разрешение, клипы, субтитры, музыка → `props.json` |
| 8. Рендер | `engine/render.py` | `npx remotion render src/index.tsx Montage` |
| 9. Проверка | `engine/util.py` | `ffprobe` результата + три превью-кадра |

Звук собирается отдельно: видеоклипы в Remotion идут без звука, а речь склеивается
ffmpeg-ом из тех же отрезков — не нужно синхронизировать аудио после каждого cut,
и вырезанные паузы не «тянутся» в дорожку. Повторный запуск берёт транскрипт и сцены
из кэша (кэш привязан к размеру и дате файла, модели и языку); пересчитать — `--force`.

## Структура проекта

```
my_new_project/
├── montage.cmd                  ← запуск: UTF-8 + проверка окружения + engine.cli
├── .env                         ← ваши ключи для --ai (в репозиторий не попадает)
├── .env.example                 ← шаблон переменных окружения (публикуется)
├── .gitignore                   ← исключает .env, тяжёлые медиа и vendor/
├── engine/
│   ├── cli.py                   ← аргументы, порядок шагов, отчёт
│   ├── util.py                  ← пути, поиск ffmpeg/node, ffprobe, аудио, превью
│   ├── analyze.py               ← распознавание речи и определение сцен
│   ├── director.py              ← план монтажа: фрагменты, титры, главы, субтитры
│   ├── ai.py                    ← необязательный LLM-режим (OpenAI-совместимый API)
│   ├── props.py                 ← стили и props для композиции Remotion
│   ├── render.py                ← раскладка ассетов в public/ и запуск рендера
│   └── __init__.py              ← версия пакета
├── projects/<проект>/           ← результат: plan.json, props.json, output.mp4, preview/ (локально)
├── demo/                        ← тестовые исходники (локально, в репозиторий не попадают)
└── vendor/                      ← сторонние копии; в репозиторий НЕ попадают (см. раздел выше)
    ├── OpenMontage/             ← анализ, промпты, remotion-composer, .venv
    └── remotion/                ← копия монорепозитория Remotion
```

Имя проекта — `<имя>_<хеш>` (`util.slugify`), поэтому разные файлы с одинаковым именем
не перетирают друг друга. Ассеты кладутся в
`vendor/OpenMontage/remotion-composer/public/montage/<slug>/` (жёсткая ссылка, при
невозможности — копия), рендер идёт во временную папку и переносится в `projects/<проект>/`.

## ⚠️ Тяжёлые медиафайлы — не коммитить

`demo/` и `projects/` содержат реальные видео и результаты рендера, и они большие:

| Файл | Размер |
|---|---|
| `demo\KVS.mp4` | ≈ 100,5 МБ |
| `projects\kittens_vertical.mp4` | ≈ 44 МБ |
| `projects\kittens_montage_8b0625\output.mp4` | ≈ 44 МБ |
| `projects\kvs_concert_0b66f7\output.mp4` | ≈ 43,4 МБ |
| `projects\kvs_concert_vertical.mp4` | ≈ 43,4 МБ |
| `projects\kittens_ac0f82\output.mp4` | ≈ 29,6 МБ |

Файлы около 100 МБ упираются в лимит GitHub в 100 МиБ на один файл (≈ 104,9 МБ): такой
файл не запушится, а чуть меньшие раздуют репозиторий и клон на гигабайты. Поэтому
`demo/`, `projects/` и все `*.mp4` / `*.mp3` исключены через `.gitignore`. Тестовые ролики
и примеры результатов держите локально; если нужен пример в репозитории — выложите
один короткий файл или ссылку на внешнее хранилище.

Отдельно и целиком исключён `vendor/` — он весит больше, чем всё остальное вместе
(подробности в разделе «Про папку `vendor/`» выше).

## Ограничения и типовые ошибки

- Без `--ai` монтаж детерминированный: движок опирается на плотность речи, паузы
  и сцены, а не на смысл сказанного. Музыка не подбирается автоматически — передайте
  файл через `--music` и следите за лицензией на трек.
- Движок рассчитан на видео с речью (интервью, лекции, скринкасты, влоги). Если звука нет,
  нарезка идёт по сценам, субтитров не будет. Рендер покадровый — самая долгая часть
  работы. Всё выполняется локально; видео уходит наружу только при `--ai`.
- **`Не найдены программы: ffmpeg, ffprobe, node`** — установите их через `winget`
  и откройте новое окно PowerShell.
- **`Не найдено Python-окружение OpenMontage`** — пересоздайте его командой из шага 2.
- **`No module named 'faster_whisper'` или `metadata_errors`** — переустановите
  `--force-reinstall "av==16.0.1" faster-whisper`.
- **`npm install` падает на esbuild** — `npm install-scripts approve esbuild`,
  затем снова `npm install`.
- **Кириллица в пути ломается** — запускайте только через `.\montage.cmd "путь"`:
  обёртка включает UTF-8 (`chcp 65001`) и не передаёт скрипты через stdin.
- **Речь распознана неточно** — `--model medium --lang ru --force`. Первый запуск
  дополнительно скачивает модель Whisper: для `small` это порядка 500 МБ.
- **LLM недоступна** — движок не падает, а печатает предупреждение и продолжает
  с локальным «режиссёром». Проверьте `OPENAI_API_KEY` и `/v1` в `OPENAI_BASE_URL`.
