# Совместимость движков транскрибации

[English](WHISPER_ENGINES_COMPATIBILITY.md) · **Русский**

Какие движки транскрибации поставляются, от чего они зависят и почему версии зафиксированы.
Всё описанное применяется поставляемым окружением автоматически — вручную править
установленные пакеты **не нужно**.

## Проверенный стек

Проверено вживую на Windows 11 с NVIDIA RTX 4060 Ti (сборка PyTorch под CUDA 12.4):

| Пакет | Версия | Почему именно эта |
|---|---|---|
| openai-whisper | 20250625 | Эталонная реализация |
| faster-whisper | 1.0.3 | Последняя версия, чьим `TranscriptionOptions` умеет управлять WhisperX 3.1.1 |
| whisperx | 3.1.1 | Требует рантайм-патча (см. ниже) |
| vosk | 0.3.45 | Лёгкий офлайн-движок |
| sherpa-onnx | 1.13.2 | Рантайм для sherpa-onnx, FunASR и офлайн-диаризации |
| torch / torchaudio | 2.6.0+cu124 | Transformers требует torch ≥ 2.6 ради безопасного `torch.load` по умолчанию |
| numpy | 1.26.4 | Обязательно `<2.0` для pyannote.audio 3.1.1 |
| pyannote.audio | 3.1.1 | Необязательный (gated) бэкенд диаризации |
| speechbrain | 0.5.16 | В 1.0+ тянется `k2` с тяжёлыми зависимостями CUDA |
| ctranslate2 | 4.7.2 | Рантайм инференса faster-whisper |
| huggingface-hub | 1.15.0 | В 1.x удалён `use_auth_token` — см. описание патча |
| transformers | 5.8.1 | Требует `huggingface-hub >= 1.5` |

Сборка **full** содержит весь этот стек; сборка **min** ставит его из
`backend/requirements.txt` во время `INSTALL.bat`.

## Движки и их рантаймы

Семь выбираемых движков транскрибации плюс пакет дополнительных sherpa-моделей,
доступный только на скачивание:

| Движок | Рантайм | Примечания |
|---|---|---|
| `whisper` | openai-whisper + torch | Эталонное качество, самый медленный |
| `faster-whisper` | ctranslate2 | В 2–4 раза быстрее эталона |
| `whisperx` | faster-whisper + torch | Самый быстрый и единственный, кто сопоставляет сегменты спикеров |
| `vosk` | vosk | Небольшие модели, щадит CPU, полностью офлайн |
| `sherpa-onnx` | sherpa-onnx | Офлайн zipformer-transducer через onnxruntime |
| `whisper-cpp` | pywhispercpp | Модели ggml, экономичен по CPU |
| `funasr` | sherpa-onnx | SenseVoice / Paraformer. Только EN/ZH/JA/KO — **русского нет**; реестр разрешает его лишь при `language=en`. Сознательно работает на рантайме sherpa-onnx, поэтому пакет `funasr`/modelscope не ставится и стек torch не затрагивается |

## Рантайм-патч WhisperX

WhisperX 3.1.1 не работает без правок с faster-whisper 1.0.3 и huggingface-hub 1.x.
Исправления живут в [`backend/whisperx_patch.py`](backend/whisperx_patch.py)
(`patch_whisperx()`, применяется из `whisperx_engine.py` до загрузки модели) — под контролем
версий и переносимо, вместо ручной правки `site-packages`. Патч делает всё перечисленное:

1. **Патчит и реэкспорт верхнего уровня.** В `whisperx/__init__.py` есть
   `from .asr import load_model`, поэтому `whisperx.load_model` — отдельная привязка от
   `whisperx.asr.load_model`. Патч только второй не даёт ничего: движок вызывает первую.
2. **Создаёт собственный подкласс `WhisperModel` из WhisperX**, а не
   `faster_whisper.WhisperModel`. Только в подклассе есть `generate_segment_batched`,
   который вызывает батч-конвейер.
3. **Фильтрует `TranscriptionOptions` по реальным `_fields` установленной версии**, убирая
   зашитый `multilingual`, который faster-whisper 1.0.3 не принимает.
4. **Грузит VAD на CPU**, даже когда ASR работает на CUDA.
5. **Чинит кодирование на CUDA.** Цепочка `WhisperModel.encode` →
   `get_ctranslate2_storage` → `np.ascontiguousarray` требует тензоров на CPU, а мел-признаки
   приходят на `cuda:0`. Патч переносит их на CPU внутри `encode`.
6. **Подменяет `use_auth_token` на `token`.** В huggingface-hub 1.x `use_auth_token` удалён,
   но WhisperX и pyannote 3.1.1 всё ещё его передают. Шим оборачивает `hf_hub_download` /
   `snapshot_download` и переустанавливает ссылку в модулях, которые импортировали её раньше.

## Бэкенды диаризации

Модели pyannote **закрыты гейтом на Hugging Face** — каждому пользователю понадобился бы
аккаунт, токен и принятие условий, — поэтому они не могут быть значением по умолчанию в
распространяемом приложении. Настройка `diarizationBackend` выбирает один из вариантов:

- **`sherpa`** (по умолчанию) — офлайновая `sherpa_onnx.OfflineSpeakerDiarization` на
  бесплатных ONNX-моделях без гейта, которые скачиваются в `resources/diarization_models/`:
  сегментация `sherpa-onnx-pyannote-segmentation-3-0`, эмбеддинги
  `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` (обе из релизов моделей
  k2-fsa). Токен не нужен. Реализация — `backend/processing/diarization.py`.
- **`pyannote`** — путь с гейтом, качество выше. Пользователь вставляет свой токен в
  настройку `hfToken` и должен принять условия моделей (см. ниже).
- **`off`** — без диаризации.

WhisperX сопоставляет сегменты спикеров от выбранного бэкенда со своими сегментами
транскрипта по перекрытию во времени, поэтому результат одинаков в обоих случаях:

```
[00:00:00] [SPEAKER_00]: текст первого спикера
[00:00:15] [SPEAKER_01]: текст второго спикера
```

Именно этот формат разбирает интерфейс управления спикерами.

### Как включить pyannote (путь с гейтом)

1. Создайте токен Hugging Face с правом чтения на <https://huggingface.co/settings/tokens>.
2. Примите условия на обеих страницах моделей — «Agree and access repository»:
   - <https://huggingface.co/pyannote/segmentation-3.0>
   - <https://huggingface.co/pyannote/speaker-diarization-3.1>
3. Вставьте токен в Настройки → `hfToken` (в бэкенд он уходит как `--hf-token`) либо
   закэшируйте его один раз через `huggingface-cli login`.

Предупреждения `k2_fsa` при диаризации безвредны — это необязательная зависимость.

## FFmpeg

FFmpeg **обязателен**: извлечение аудио и `whisperx.load_audio()` вызывают его как
подпроцесс. Без него в `PATH` вы получите `[WinError 2] File not found`. Сборка full
содержит ffmpeg и добавляет его в `PATH` для каждого порождаемого подпроцесса; для сборки
min установите ffmpeg в систему и добавьте в `PATH` Windows.

## Известные проблемы

**Битые «указатели-симлинки» моделей после переноса между Windows-машинами.** Снапшот
Hugging Face, скопированный с одной Windows-машины на другую, может превратиться в
текстовые файлы по 76 байт с содержимым `../../blobs/…` вместо настоящих файлов или
симлинков. CTranslate2 тогда читает мусорную версию бинарника. Лечение: удалить эти
файлы-указатели и повторно выполнить `snapshot_download` — блобы обычно целы, поэтому
ничего не скачивается заново, а нормальные симлинки создаются. Шаг упаковки проверяет, что
в архив попадают настоящие файлы, а не указатели.

**У FunASR нет русского.** Реестр предлагает его только при `language=en`. Для русского
аудио используйте движки семейства Whisper, Vosk или sherpa-onnx.

## Как проверить свою установку

```python
import torch, whisperx
from faster_whisper import WhisperModel

print("CUDA available:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU only")

model = whisperx.load_model("tiny", "cuda", compute_type="float16", language="en")
```

Либо воспользуйтесь приложением: окно **Диагностика** показывает определённое устройство,
доступность CUDA и готовность каждого движка, не выходя из интерфейса.
