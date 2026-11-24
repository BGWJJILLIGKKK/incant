# Third-party notices

incant's own code is under the BSD 3-Clause license ([LICENSE](LICENSE)). It depends on, and its binary releases
bundle, software and models under their own licenses. Binary releases are:

- the Docker images `incant` and `incant_local` (Debian 12 base; Python packages in `/opt/venv`);
- the Debian package `incant_*.deb` (built by `packaging/debian/build` against the target release's system
  Python; Python packages in `/usr/lib/incant/venv`). Its default extras are `kokoro mobi`; builds with
  `INCANT_DEB_EXTRAS` add more (e.g. `qa`, which brings in PyAV's FFmpeg, below).

Some bundled components are copyleft and are loaded **in-process** (imported or `dlopen`ed by incant's Python
process, not run as separate tools): with the `kokoro` and `mobi` extras that is mobi 0.4.x (GPL-3.0-only),
phonemizer (GPL-3.0-or-later) and espeakng-loader, which bundles libespeak-ng and espeak-ng-data
(GPL-3.0-or-later). Both images and the default `.deb` include them, so **an image or `.deb` as a whole is
distributed under the terms of the GNU GPL version 3**, while incant's own files stay BSD-licensed and can be
reused under BSD-3-Clause on their own. A source checkout installed without those extras is not affected.

## Copyleft components

| Component | License | Where | Used for |
|---|---|---|---|
| espeak-ng 1.52.0 (libespeak-ng + espeak-ng-data, bundled in espeakng-loader 0.2.4) | GPL-3.0-or-later | `kokoro` extra: both images, default `.deb` | phonemes for Kokoro |
| phonemizer | GPL-3.0-or-later | `kokoro` extra: both images, default `.deb` | phonemes for Kokoro |
| mobi (KindleUnpack) 0.4.x | GPL-3.0-only | `mobi` extra: both images, default `.deb` | Kindle files |
| PyAV (`av`) wheel's bundled FFmpeg (libavcodec 63.1, built `--enable-version3` with `--enable-libx264 --enable-libx265`) and its libraries in `av.libs/` | FFmpeg: LGPL-3.0-or-later as built; bundled libx264 and libx265: GPL-2.0-or-later, which makes this FFmpeg build GPL as a whole; gnutls, libunistring, libasound (ALSA): LGPL; gmp, nettle/hogweed: LGPL-3.0-or-later or GPL-2.0-or-later; libmp3lame: LGPL-2.0-or-later; dav1d, libvpx, libopus, libwebp, SVT-AV1, libvmaf: BSD-style; libvpl, libdrm, libxcb, libXau: MIT-style | `qa` extra (faster-whisper → PyAV): both images; `.deb` built with `qa` in `INCANT_DEB_EXTRAS` | decoding audio for QA transcription. PyAV's own code is BSD-3-Clause. |
| libgomp (bundled in ctranslate2's wheel, `ctranslate2.libs/`) | GPL-3.0-or-later with the GCC Runtime Library Exception 3.1 | `qa` extra | OpenMP runtime for CTranslate2 |
| num2words | LGPL-2.1-or-later | core dependency: images and `.deb` | numbers to words |
| certifi, tqdm | MPL-2.0 (tqdm: MPL-2.0 AND MIT) | dependencies: images and `.deb` | CA bundle, progress bars |
| tld | MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later (recipient's choice) | dependency: images and `.deb` | domain names |
| ffmpeg (Debian `ffmpeg` package, built with `--enable-gpl`) | GPL-2.0-or-later | both images (apt); for the `.deb` a declared dependency installed from Debian's own archive, not shipped in it | audio |
| Playwright's ffmpeg build | LGPL-2.1 | `browser` extra, both images | (Playwright internals) |
| Pandoc / Calibre | GPL-2.0-or-later / GPL-3.0 | only with the `CONVERTERS` build arg (images); `pandoc` is only a Recommends of the `.deb` | `ingest=ebook` |

The licenses of PyAV's bundled libraries are as their upstream projects declare them; the wheel itself declares
only PyAV's BSD-3-Clause (its `sboms/auditwheel.cdx.json` lists the bundled system libraries without licenses).

## Corresponding Source

For every GPL, LGPL or MPL component in a released binary (a `.deb` or a Docker image):

1. **Written offer (always).** The offer below covers every binary release. Releases may also attach the
   Corresponding Source as assets of the same GitHub release
   ([github.com/BGWJJILLIGKKK/incant/releases](https://github.com/BGWJJILLIGKKK/incant/releases)): the source
   archives (sdists, or upstream source tarballs where a wheel bundles native code built from another project,
   such as espeak-ng, FFmpeg, x264 and x265 inside PyAV and espeakng-loader) of the exact versions in that
   release, as pinned in `uv.lock`, together with incant's own source at the release tag.
2. **Debian system packages** in the images (ffmpeg and the base system) come unmodified from Debian; their source
   is in Debian's archive for the versions in the image: [sources.debian.org](https://sources.debian.org) and
   [snapshot.debian.org](https://snapshot.debian.org) (Debian 12, bookworm). The `.deb` does not ship ffmpeg;
   it depends on the system's `ffmpeg` package from Debian's archive.
3. **Written offer.** For at least three years after the last date a given `.deb` or image was distributed by
   this project, and for as long as spare parts or support are offered for it, we will provide the complete
   Corresponding Source of every GPL- and LGPL-licensed component in it (and the Source Code Form of every
   MPL-2.0 component), for no more than the cost of physically performing the distribution, to anyone who asks.
   Ask by opening an issue at [github.com/BGWJJILLIGKKK/incant/issues](https://github.com/BGWJJILLIGKKK/incant/issues),
   or privately through the repository's security advisories
   ([github.com/BGWJJILLIGKKK/incant/security](https://github.com/BGWJJILLIGKKK/incant/security)), naming the
   release (version and `.deb` or image tag/digest). The source will be provided as a download.

Upstream homes, for reference: Python packages on PyPI at the versions listed below; espeak-ng at
[github.com/espeak-ng/espeak-ng](https://github.com/espeak-ng/espeak-ng) (1.52.0); PyAV at
[github.com/PyAV-Org/PyAV](https://github.com/PyAV-Org/PyAV).

## Where the license texts are

| | Docker images | Debian package |
|---|---|---|
| Python packages (their own files) | `/opt/venv/lib/python3.12/site-packages/*.dist-info/` | `/usr/lib/incant/venv/lib/python3.13/site-packages/*.dist-info/` (Debian 13; the Python minor version follows the target release) |
| Texts not shipped by their packages, and the GPL/LGPL/MPL family | `/app/LICENSES/` | `/usr/share/doc/incant/LICENSES/` |
| This file | `/app/THIRD_PARTY_NOTICES.md` | `/usr/share/doc/incant/THIRD_PARTY_NOTICES.md` |
| Package copyright summary | — | `/usr/share/doc/incant/copyright` (machine-readable, DEP-5) |
| Debian system packages | `/usr/share/doc/*/copyright` | `/usr/share/doc/*/copyright` (installed with each dependency) |
| Chromium | `/opt/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/LICENSE.headless_shell` | (`browser` extra: Playwright's browsers are not in the `.deb`) |
| Playwright's ffmpeg | `/opt/ms-playwright/ffmpeg-*/COPYING.LGPLv2.1` | — |

In [LICENSES/](LICENSES) (copied to both locations above):

| File | For |
|---|---|
| [GPL-3.0.txt](LICENSES/GPL-3.0.txt) | espeak-ng / espeakng-loader, phonemizer, mobi; the image or `.deb` as a whole |
| [GPL-2.0.txt](LICENSES/GPL-2.0.txt) | libx264 and libx265 in PyAV's `av.libs/`; Debian's ffmpeg; tld's GPL option |
| [LGPL-2.1.txt](LICENSES/LGPL-2.1.txt), [LGPL-3.0.txt](LICENSES/LGPL-3.0.txt) | num2words; FFmpeg, gnutls, gmp, nettle, libunistring, libmp3lame in `av.libs/`; tld's LGPL option |
| [MPL-2.0.txt](LICENSES/MPL-2.0.txt) | certifi, tqdm |
| [GCC-exception-3.1.txt](LICENSES/GCC-exception-3.1.txt) | libgomp in `ctranslate2.libs/` (with GPL-3.0.txt) |
| [Apache-2.0.txt](LICENSES/Apache-2.0.txt) | flatbuffers and tokenizers (their wheels ship no license file; Apache-2.0 per their metadata); Kokoro-82M; Docling and RapidOCR models |
| [MIT-loguru.txt](LICENSES/MIT-loguru.txt), [MIT-ctranslate2.txt](LICENSES/MIT-ctranslate2.txt) | loguru and ctranslate2 (their wheels ship no license file) |
| [MIT-whisper.txt](LICENSES/MIT-whisper.txt) | faster-whisper `base.en` weights |

Wheels that ship no license file, and what is known about them offline:

- **espeakng-loader 0.2.4**: declares no license in its metadata (author: thewh1teagle). It bundles libespeak-ng
  1.52.0 and espeak-ng-data, which are GPL-3.0-or-later, so it is treated as GPL-3.0-or-later as a whole
  (text: GPL-3.0.txt).
- **ctranslate2 4.8.5**: MIT per its metadata (author: OpenNMT). The upstream copyright line could not be read
  offline; MIT-ctranslate2.txt names the author from the metadata and points to the upstream LICENSE.
- **loguru 0.7.3**: MIT per its classifiers (author: Delgan). As for ctranslate2: MIT-loguru.txt names the author
  and points to the upstream LICENSE.
- **flatbuffers 25.12.19** (Google; author in metadata: Derek Bailey) and **tokenizers 0.23.3** (Hugging Face):
  Apache-2.0 per their metadata. Any upstream NOTICE file is not in the wheels and was not checked.
- **onnxruntime**: no license file in its dist-info, but `onnxruntime/LICENSE` (MIT, Microsoft Corporation) and
  `onnxruntime/ThirdPartyNotices.txt` are inside the package directory.

## Models and voices

| Model | License | Where | Text |
|---|---|---|---|
| Kokoro-82M (ONNX files from kokoro-onnx's releases) and its voices | Apache-2.0 | `kokoro` extra; baked into both images (the `.deb` downloads them on first use) | [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt) |
| faster-whisper `base.en` (OpenAI Whisper weights) | MIT | `qa` extra; baked into both images (the `.deb` downloads it on first use) | [LICENSES/MIT-whisper.txt](LICENSES/MIT-whisper.txt) |
| spaCy `en_core_web_sm` 3.8.0 | MIT | `nlp` extra | in its dist-info |
| Docling layout models (`docling-layout-heron`) | Apache-2.0 | `incant_local` | [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt) |
| RapidOCR models | Apache-2.0 | `incant_local` | [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt) |
| Voice-Zero voice list (`voices/voices.json`) | public domain (as declared by the Voice-Zero set) | this repository, both images, the `.deb` | — |

LLMs and speech servers you connect to (llama.cpp, IndexTTS, ElevenLabs, Claude, Gemini…) are not part of
incant or its images; their terms apply to what you make with them. `credits.txt` beside each audiobook lists
what was used, and `--licenses` shows the known terms per method. The Voice-Zero entries record no speaker
consent (`consent` field), so `credits.txt` lists each one used as "consent: not recorded (voice provenance:
voice-zero)"; that is separate from the recordings' public-domain terms.

## Python packages in the `incant` image

Generated with `tools/licenses.py`, as declared by each package. The `.deb` holds the subset for its extras
(`kokoro mobi` by default), at the same versions (all pinned by `uv.lock`).

| Package | Version | License |
|---|---|---|
| addict | 2.4.0 | MIT License |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anthropic | 2.6.0 | Apache Software License |
| anyio | 4.16.0 | MIT |
| attrs | 26.1.0 | MIT |
| av | 19.0.2 | BSD-3-Clause (bundles FFmpeg with libx264/libx265, GPL-2.0-or-later: see Copyleft components) |
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
| ctranslate2 | 4.8.5 | MIT (text: LICENSES/MIT-ctranslate2.txt; bundles libgomp, GPL-3.0-or-later WITH GCC-exception-3.1) |
| cymem | 2.0.16 | MIT License |
| dateparser | 1.5.0 | BSD-3-Clause |
| dlinfo | 2.0.0 | MIT |
| docopt | 0.6.2 | MIT License |
| docstring_parser | 0.18.0 | MIT License |
| en_core_web_sm | 3.8.0 | MIT |
| espeakng-loader | 0.2.4 | GPL-3.0-or-later (bundles libespeak-ng; text: LICENSES/GPL-3.0.txt) |
| faster-whisper | 1.2.1 | MIT License |
| filelock | 4.2.9 | MIT |
| flatbuffers | 25.12.19 | Apache Software License (text: LICENSES/Apache-2.0.txt) |
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
| loguru | 0.7.3 | MIT License (text: LICENSES/MIT-loguru.txt) |
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
| tokenizers | 0.23.3 | Apache Software License (text: LICENSES/Apache-2.0.txt) |
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
