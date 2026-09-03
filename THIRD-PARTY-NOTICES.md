# Third-party notices

**English** · [Русский](THIRD-PARTY-NOTICES.ru.md)

Meeting Summarizer itself is MIT-licensed (see [LICENSE](LICENSE)). The
distribution archives additionally carry third-party software and model weights
that keep their **own** licences. This file lists them and says where each
licence text ships.

Licence texts live in [`licenses/`](licenses/), which is included in both the
`min` and `full` archives.

## Which archive contains what

| Component | Bundled in `min` | Bundled in `full` |
|---|---|---|
| Program source (this project) | yes | yes |
| FFmpeg binaries | yes | yes |
| Node.js runtime | no | yes |
| CPython + all Python packages | no | yes |
| ASR / diarization model weights | small models only | full set |

## FFmpeg — **GPL-3.0-or-later**

`backend/FFmpeg/ffmpeg.exe`, `backend/FFmpeg/ffprobe.exe`.

The bundled build is configured with `--enable-gpl --enable-version3` (and
GPL-only encoders such as libx264 / libx265 / libxvid), which makes **this binary
GPLv3**, not the LGPL build FFmpeg also offers. Full configuration string:
[`licenses/ffmpeg-build-configuration.txt`](licenses/ffmpeg-build-configuration.txt).

- Licence text: [`licenses/GPL-3.0.txt`](licenses/GPL-3.0.txt)
- Upstream and complete corresponding source: <https://ffmpeg.org/download.html>
  and <https://git.ffmpeg.org/ffmpeg.git>. The bundled build is an unmodified
  binary from the BtbN Windows builds (<https://github.com/BtbN/FFmpeg-Builds>);
  its exact revision is recorded in the configuration file above.

FFmpeg runs as a **separate process** invoked over a command line. It is not
linked into this program, so the two are aggregated, not combined — this project
stays MIT while the FFmpeg binary stays GPLv3.

## Qt / PySide6 — **LGPL-3.0** (open-source edition)

`backend/python/Lib/site-packages/PySide6*` (`full` only).

- Licence text: [`licenses/LGPL-3.0.txt`](licenses/LGPL-3.0.txt)
- Upstream: <https://doc.qt.io/qtforpython/> · <https://www.qt.io/licensing/>
- The archive ships PySide6 as unmodified wheels. Under LGPL-3.0 you may replace
  them: delete the `PySide6*` directories from `backend/python/Lib/site-packages`
  and install your own build of the same version.

> The wheels' own metadata directories contain `LicenseRef-Qt-Commercial.txt`,
> which describes Qt's *commercial* option. It does not apply here — this
> distribution uses the open-source LGPL terms above.

## Node.js — MIT

`backend/JavaScript/` (`full` only). Licence text ships beside the binary as
`NODE-LICENSE.txt`. Upstream: <https://nodejs.org/>

## Python and Python packages

`full` carries CPython and ~187 packages. Each keeps its own licence inside its
`*.dist-info/` directory in the archive; 178 of them ship the text that way. The
following nine do not include a licence file in their wheel metadata — their
licences are recorded here instead:

| Package | Licence | Upstream |
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

Apache-2.0 text: [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt).

PyTorch (BSD-3-Clause) ships its licence at
`backend/python/Lib/site-packages/torch-*.dist-info/LICENSE`.

## Model weights

Model files are **data**, distributed under the terms of their publishers.

| Model | Licence | Upstream |
|---|---|---|
| OpenAI Whisper (`medium.pt`) | MIT | <https://github.com/openai/whisper> |
| faster-whisper medium (CTranslate2 conversion) | MIT | <https://huggingface.co/Systran/faster-whisper-medium> |
| whisper.cpp GGML medium | MIT | <https://huggingface.co/ggerganov/whisper.cpp> |
| Vosk ru / en models | Apache-2.0 | <https://alphacephei.com/vosk/models> |
| sherpa-onnx zipformer ru / en | Apache-2.0 | <https://github.com/k2-fsa/sherpa-onnx> |
| sherpa-onnx SenseVoice (FunASR) | Apache-2.0 | <https://github.com/k2-fsa/sherpa-onnx> |
| sherpa-onnx pyannote segmentation | MIT (weights: CC-BY-4.0 upstream) | <https://huggingface.co/pyannote/segmentation-3.0> |
| 3D-Speaker ERes2Net speaker embedding | Apache-2.0 | <https://github.com/modelscope/3D-Speaker> |

Models downloaded **on demand** by the app (the built-in local AI, extra ASR
models) are not part of these archives and carry the licences stated by their
publishers at download time.

## Reporting a problem with this list

If something here is wrong or missing, please open an issue — see
[SECURITY.md](SECURITY.md) for anything that is security-sensitive.
