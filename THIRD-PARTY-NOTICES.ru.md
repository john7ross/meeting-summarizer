# Уведомления о стороннем ПО

[English](THIRD-PARTY-NOTICES.md) · **Русский**

Сам Meeting Summarizer распространяется под MIT (см. [LICENSE](LICENSE)). Но в
дистрибутивных архивах едут стороннее ПО и веса моделей, у которых **свои**
лицензии. Здесь перечислено, что именно и где лежит текст каждой лицензии.

Тексты лицензий — в каталоге [`licenses/`](licenses/); он входит и в `min`, и в
`full`.

## Что в каком архиве

| Компонент | Есть в `min` | Есть в `full` |
|---|---|---|
| Исходники программы (этот проект) | да | да |
| Бинарники FFmpeg | да | да |
| Рантайм Node.js | нет | да |
| CPython и все Python-пакеты | нет | да |
| Веса моделей ASR / диаризации | только малые | полный набор |

## FFmpeg — **GPL-3.0-or-later**

`backend/FFmpeg/ffmpeg.exe`, `backend/FFmpeg/ffprobe.exe`.

Вложенная сборка собрана с `--enable-gpl --enable-version3` (и с GPL-only
кодировщиками libx264 / libx265 / libxvid), поэтому **этот бинарник — GPLv3**, а
не LGPL-вариант, который FFmpeg тоже выпускает. Полная строка конфигурации:
[`licenses/ffmpeg-build-configuration.txt`](licenses/ffmpeg-build-configuration.txt).

- Текст лицензии: [`licenses/GPL-3.0.txt`](licenses/GPL-3.0.txt)
- Апстрим и полные соответствующие исходники: <https://ffmpeg.org/download.html>
  и <https://git.ffmpeg.org/ffmpeg.git>. Вложенная сборка — неизменённый бинарник
  из Windows-сборок BtbN (<https://github.com/BtbN/FFmpeg-Builds>); точная ревизия
  зафиксирована в файле конфигурации выше.

FFmpeg запускается **отдельным процессом** через командную строку. Он не
линкуется в программу, поэтому это агрегация, а не объединение: проект остаётся
MIT, а бинарник FFmpeg — GPLv3.

## Qt / PySide6 — **LGPL-3.0** (open-source редакция)

`backend/python/Lib/site-packages/PySide6*` (только `full`).

- Текст лицензии: [`licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt)
- Апстрим: <https://doc.qt.io/qtforpython/> · <https://www.qt.io/licensing/>
- В архиве PySide6 едет неизменёнными wheel-пакетами. По LGPL-3.0 вы вправе их
  заменить: удалите каталоги `PySide6*` из
  `backend/python/Lib/site-packages` и поставьте свою сборку той же версии.

> В метаданных самих wheel лежит `LicenseRef-Qt-Commercial.txt` — он описывает
> *коммерческий* вариант Qt и к этой поставке не относится: здесь действуют
> open-source условия LGPL, указанные выше.

## Node.js — MIT

`backend/JavaScript/` (только `full`). Текст лицензии едет рядом с бинарником как
`NODE-LICENSE.txt`. Апстрим: <https://nodejs.org/>

## Python и Python-пакеты

В `full` едут CPython и ~187 пакетов. У каждого своя лицензия внутри его каталога
`*.dist-info/` в архиве; 178 из них везут текст именно так. Девять перечисленных
ниже не кладут файл лицензии в метаданные wheel — их лицензии зафиксированы здесь:

| Пакет | Лицензия | Апстрим |
|---|---|---|
| ctranslate2 | MIT | <https://github.com/OpenNMT/CTranslate2> |
| onnxruntime | MIT | <https://github.com/microsoft/onnxruntime> |
| tokenizers | Apache-2.0 | <https://github.com/huggingface/tokenizers> |
| sentencepiece | Apache-2.0 | <https://github.com/google/sentencepiece> |
| vosk | Apache-2.0 | <https://github.com/alphacep/vosk-api> |
| sherpa-onnx-core | Apache-2.0 | <https://github.com/k2-fsa/sherpa-onnx> |
| flatbuffers | Apache-2.0 | <https://github.com/google/flatbuffers> |
| antlr4-python3-runtime | BSD-3-Clause | <https://github.com/antlr/antlr4> |
| primePy | MIT | <https://github.com/janaindrajit/primePy> |

Текст Apache-2.0: [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt).

PyTorch (BSD-3-Clause) везёт свою лицензию в
`backend/python/Lib/site-packages/torch-*.dist-info/LICENSE`.

## Веса моделей

Файлы моделей — это **данные**, распространяемые на условиях их издателей.

| Модель | Лицензия | Апстрим |
|---|---|---|
| OpenAI Whisper (`medium.pt`) | MIT | <https://github.com/openai/whisper> |
| faster-whisper medium (конверсия CTranslate2) | MIT | <https://huggingface.co/Systran/faster-whisper-medium> |
| whisper.cpp GGML medium | MIT | <https://huggingface.co/ggerganov/whisper.cpp> |
| Модели Vosk ru / en | Apache-2.0 | <https://alphacephei.com/vosk/models> |
| sherpa-onnx zipformer ru / en | Apache-2.0 | <https://github.com/k2-fsa/sherpa-onnx> |
| sherpa-onnx SenseVoice (FunASR) | Apache-2.0 | <https://github.com/k2-fsa/sherpa-onnx> |
| sherpa-onnx pyannote segmentation | MIT (веса: CC-BY-4.0 в апстриме) | <https://huggingface.co/pyannote/segmentation-3.0> |
| 3D-Speaker ERes2Net (эмбеддинги дикторов) | Apache-2.0 | <https://github.com/modelscope/3D-Speaker> |

Модели, которые приложение скачивает **по требованию** (встроенный локальный ИИ,
дополнительные ASR-модели), в эти архивы не входят и распространяются на
условиях, заявленных их издателями на момент скачивания.

## Если здесь ошибка

Если что-то указано неверно или пропущено — заведите issue. Всё, что связано с
безопасностью, — через [SECURITY.ru.md](SECURITY.ru.md).
