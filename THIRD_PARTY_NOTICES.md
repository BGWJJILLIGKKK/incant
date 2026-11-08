# Third-party notices

incant's own code is under the BSD 3-Clause license ([LICENSE](LICENSE)). It depends on, and its Docker images
bundle, software and models under their own licenses. Some are copyleft: an `incant` or `incant_local` image as
a whole is distributed under their terms too, while incant's own files stay BSD-licensed.

## Copyleft components

| Component | License | Where | Used for |
|---|---|---|---|
| EbookLib | AGPL-3.0-or-later | core dependency | reading EPUB files |
| espeak-ng (via espeakng-loader) | GPL-3.0-or-later | `kokoro` extra, both images | phonemes for Kokoro |
| phonemizer | GPL-3.0-or-later | `kokoro` extra, both images | phonemes for Kokoro |
| mobi (KindleUnpack) | GPL-3.0-only | `mobi` extra, both images | Kindle files |
| num2words | LGPL-2.1-or-later | core dependency | numbers to words |
| ffmpeg (Debian `ffmpeg` package, built with `--enable-gpl`) | GPL-2.0-or-later | both images (apt) | audio |
| Playwright's ffmpeg build | LGPL-2.1 | `browser` extra, both images | (Playwright internals) |
| Pandoc / Calibre | GPL-2.0-or-later / GPL-3.0 | only with the `CONVERTERS` build arg | `ingest=ebook` |
| certifi, tqdm, tld | MPL-2.0 / MPL-2.0 / MPL-1.1 or GPL-2.0 or LGPL-2.1 | dependencies | |

**Source code.**
- incant: this repository.
- Python packages: PyPI, at the versions listed below.
- Debian packages (ffmpeg and the base system): [sources.debian.org](https://sources.debian.org) and
  [snapshot.debian.org](https://snapshot.debian.org), for the Debian 12 (bookworm) versions in the image.
- espeak-ng: [github.com/espeak-ng/espeak-ng](https://github.com/espeak-ng/espeak-ng) (1.52.0).

If any of these become unavailable, open an issue and the corresponding source for a published image will be
provided.

**License texts in the images.**
- Python packages: `/opt/venv/lib/python3.12/site-packages/*.dist-info/`.
- Debian packages: `/usr/share/doc/*/copyright`.
- Chromium: `/opt/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/LICENSE.headless_shell`.
- Playwright's ffmpeg: `/opt/ms-playwright/ffmpeg-*/COPYING.LGPLv2.1`.
- Texts not shipped by their packages: `/app/LICENSES/`, from [LICENSES/](LICENSES).

## Models and voices

| Model | License | Where | Text |
|---|---|---|---|
| Kokoro-82M (ONNX files from kokoro-onnx's releases) and its voices | Apache-2.0 | `kokoro` extra; baked into both images | [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt) |
| faster-whisper `base.en` (OpenAI Whisper weights) | MIT | `qa` extra; baked into both images | [LICENSES/MIT-whisper.txt](LICENSES/MIT-whisper.txt) |
| spaCy `en_core_web_sm` 3.8.0 | MIT | `nlp` extra | in its dist-info |
| Docling layout models (`docling-layout-heron`) | Apache-2.0 | `incant_local` | [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt) |
| RapidOCR models | Apache-2.0 | `incant_local` | [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt) |
| Voice-Zero voice list (`voices/voices.json`) | public domain | this repository | — |

LLMs and speech servers you connect to (llama.cpp, IndexTTS, ElevenLabs, Claude, Gemini…) are not part of
incant or its images; their terms apply to what you make with them. `credits.txt` beside each audiobook lists
what was used, and `--licenses` shows the known terms per method.

## Python packages in the `incant` image

Generated with `tools/licenses.py`, as declared by each package.

| Package | Version | License |
|---|---|---|
| addict | 2.4.0 | MIT License |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anthropic | 2.6.0 | Apache Software License |
| anyio | 4.16.0 | MIT |
| attrs | 26.1.0 | MIT |
| av | 19.0.2 | BSD-3-Clause |
| babel | 2.18.0 | BSD License |
| beautifulsoup4 | 4.15.0 | MIT License |
| blis | 1.3.4 | BSD License |
| catalogue | 2.0.10 | MIT License |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.1 | BSD-3-Clause |
| cloudpathlib | 0.26.0 | MIT License |
| cloudpickle | 3.1.2 | BSD License |
| confection | 1.3.4 | MIT License |
| courlan | 1.4.0 | Apache-2.0 |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| ctranslate2 | 4.8.5 | MIT |
| cymem | 2.0.16 | MIT License |
| dateparser | 1.5.0 | BSD-3-Clause |
| dlinfo | 2.0.0 | MIT |
| docopt | 0.6.2 | MIT License |
| docstring_parser | 0.18.0 | MIT License |
| EbookLib | 0.21 | GNU Affero General Public License v3 or later (AGPLv3+) |
| en_core_web_sm | 3.8.0 | MIT |
| espeakng-loader | 0.2.4 | GPL-3.0-or-later (bundles libespeak-ng; text: LICENSES/GPL-3.0.txt) |
| faster-whisper | 1.2.1 | MIT License |
| filelock | 4.2.9 | MIT |
| flatbuffers | 25.12.19 | Apache Software License |
| fsspec | 2026.9.0 | BSD-3-Clause |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | MIT License |
| hf-xet | 1.8.0 | Apache-2.0 |
| htmldate | 1.11.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.16.0 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| httpx2 | 2.16.0 | BSD-3-Clause |
| huggingface_hub | 1.33.0 | Apache Software License |
| idna | 3.20 | BSD-3-Clause |
| Jinja2 | 3.1.6 | BSD License |
| jiter | 0.19.0 | MIT |
| joblib | 1.6.0 | BSD-3-Clause |
| jusText | 3.0.2 | BSD License |
| kokoro-onnx | 0.6.1 | MIT |
| loguru | 0.7.3 | MIT License |
| lxml | 6.1.3 | BSD-3-Clause |
| lxml_html_clean | 0.4.6 | BSD-3-Clause |
| markdown-it-py | 4.2.0 | MIT License |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| mdurl | 0.1.2 | MIT License |
| misaki | 0.9.4 | Apache Software License |
| mobi | 0.4.1 | GPL-3.0-only |
| murmurhash | 1.0.16 | MIT License |
| num2words | 0.5.14 | GNU Library or Lesser General Public License (LGPL) |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| onnxruntime | 1.31.1 | MIT License |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pdfminer.six | 20260107 | MIT |
| phonemizer | 3.6.0 | GNU General Public License v3 or later (GPLv3+) |
| playwright | 1.64.1 | Apache-2.0 |
| preshed | 3.0.15 | MIT License |
| protobuf | 7.36.2 | 3-Clause BSD License |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic_core | 2.50.4 | MIT |
| pyee | 14.0.0 | MIT License |
| Pygments | 2.21.0 | BSD-2-Clause |
| python-dateutil | 4.0.0 | BSD-3-Clause |
| pytz | 2026.6 | MIT License |
| PyYAML | 6.0.3 | MIT License |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache Software License |
| rich | 15.0.0 | MIT License |
| setuptools | 80.10.2 | MIT |
| shellingham | 1.5.4 | ISC License (ISCL) |
| smart_open | 9.0.2 | MIT License |
| sniffio | 1.3.1 | MIT License / Apache Software License |
| soupsieve | 4.0.1 | MIT |
| spacy | 3.8.18 | MIT License |
| spacy-legacy | 3.0.12 | MIT License |
| spacy-loggers | 1.0.5 | MIT |
| srsly | 2.5.6 | MIT License |
| standard-imghdr | 3.13.0 | Python Software Foundation License |
| thinc | 8.3.14 | MIT License |
| tld | 0.13.2 | MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later |
| tokenizers | 0.23.3 | Apache Software License |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| trafilatura | 2.3.1 | Apache-2.0 |
| truststore | 0.10.5 | MIT |
| typer | 0.27.3 | MIT |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.6 | Apache-2.0 |
| tzlocal | 5.4.5 | MIT |
| urllib3 | 2.8.0 | MIT |
| wasabi | 1.1.3 | MIT |
| weasel | 1.0.1 | MIT License |
| wrapt | 2.5.2 | BSD-2-Clause |

## Added in the `incant_local` image (PyTorch, CPU-only, and Docling)

| Package | Version | License |
|---|---|---|
| accelerate | 1.15.0 | Apache Software License |
| colorlog | 6.12.0 | MIT License |
| defusedxml | 0.7.1 | Python Software Foundation License |
| dill | 0.4.1 | BSD License |
| doclang | 0.9.0 | Apache-2.0 |
| docling | 2.148.0 | MIT |
| docling-core | 2.106.0 | MIT |
| docling-ibm-models | 4.0.4 | MIT |
| docling-parse | 7.24.2 | MIT |
| docling-slim | 2.148.0 | MIT |
| et_xmlfile | 2.0.0 | MIT License |
| Faker | 40.44.0 | MIT License |
| filetype | 1.2.0 | MIT License |
| jsonref | 1.1.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| langcodes | 3.5.1 | MIT License |
| latex2mathml | 3.81.1 | MIT |
| mail-parser | 4.10.0 | Apache-2.0 |
| marko | 2.2.5 | MIT |
| mpire | 2.10.2 | MIT License |
| mpmath | 1.3.0 | BSD License |
| multiprocess | 0.70.19 | BSD License |
| networkx | 3.7 | BSD-3-Clause |
| olefile | 0.47 | BSD License |
| omegaconf | 2.4.0 | BSD License |
| opencv-python-headless | 5.0.0.93 | Apache Software License |
| openpyxl | 3.1.5 | MIT License |
| pandas | 3.1.1 | BSD-3-Clause |
| pillow | 13.0.0 | MIT-CMU |
| pluggy | 1.7.0 | MIT License |
| polyfactory | 3.5.0 | MIT License |
| psutil | 7.2.2 | BSD-3-Clause |
| pyclipper | 1.4.0 | MIT License |
| pydantic-settings | 2.16.2 | MIT |
| pylatexenc | 2.11 | MIT License |
| pypdfium2 | 5.15.1 | BSD-3-Clause, Apache-2.0, dependency licenses |
| python-docx | 1.2.0 | MIT License |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-oxmsg | 0.0.2 | MIT License |
| python-pptx | 1.0.2 | MIT License |
| rapidocr | 3.10.0 | Apache-2.0 |
| referencing | 0.37.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| rtree | 1.4.1 | MIT |
| safetensors | 0.8.0 | Apache Software License |
| scipy | 1.18.1 | BSD License |
| semchunk | 3.2.5 | MIT License |
| shapely | 2.2.0 | BSD-3-Clause |
| six | 1.17.0 | MIT License |
| sympy | 1.14.0 | BSD License |
| tabulate | 0.10.0 | MIT |
| torch | 2.15.0+cpu | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND ISC AND MIT AND Zlib |
| torchvision | 0.29.2+cpu | BSD |
| transformers | 5.22.2 | Apache 2.0 License |
| tree-sitter | 0.26.0 | MIT License |
| tree-sitter-c | 0.24.2 | MIT License |
| tree-sitter-javascript | 0.25.0 | MIT |
| tree-sitter-python | 0.25.0 | MIT |
| tree-sitter-typescript | 0.23.2 | MIT License |
| websockets | 17.4 | BSD-3-Clause |
| xlsxwriter | 3.3.1 | BSD-2-Clause |
