# Credits

NookCut is a small layer on top of a lot of excellent open-source work. Thank you to everyone who
builds and maintains these projects.

NookCut's own code is MIT-licensed (see [LICENSE](LICENSE)). It **does not bundle** any of the
software or models below: it runs the tools you install, or downloads a model only when you run
`nookcut models install`. Each project keeps its own license.

## Runtime

| Project | What NookCut uses it for | License |
|---|---|---|
| [Python](https://www.python.org/) and its standard library | Everything in NookCut itself; it has no other required Python dependency | PSF License |
| [FFmpeg](https://ffmpeg.org/) (`ffmpeg`, `ffprobe`) | All decoding, analysis, rendering, mixing and encoding | LGPL 2.1+ (GPL 2+ for builds that include GPL parts such as x264) |
| [x264](https://www.videolan.org/developers/x264.html) (through FFmpeg) | H.264 encoding on the CPU | GPL 2+ |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) and [ggml](https://github.com/ggml-org/ggml) (FFmpeg's `whisper` filter) | Offline speech-to-text for speech captions | MIT |
| [OpenAI Whisper](https://github.com/openai/whisper) models, in whisper.cpp's ggml format | The speech models `nookcut models install` downloads (from [Hugging Face](https://huggingface.co/ggerganov/whisper.cpp)) | MIT |
| [zimg](https://github.com/sekrit-twc/zimg) (FFmpeg's `zscale` filter) | HDR to SDR tone mapping on the CPU | WTFPL |
| [x265](https://www.videolan.org/developers/x265.html) (through FFmpeg) | 10-bit HDR encoding on the CPU (`hdr: keep`) | GPL 2+ |
| [libass](https://github.com/libass/libass) (FFmpeg's `ass` filter) | Word-by-word captions with the highlighted word | ISC |
| [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) and [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (optional, installed by you) | A natural voice for voiceover, through `nookcut config kokoro` | Apache 2.0 / MIT |
| [espeak-ng](https://github.com/espeak-ng/espeak-ng) (optional, Linux) | The computer's own voice for voiceover, if installed | GPL 3 |
| [FreeType](https://freetype.org/) (FFmpeg's `drawtext` filter, libass) | Drawing caption text | FreeType License or GPL 2 |
| [HarfBuzz](https://github.com/harfbuzz/harfbuzz) and [FriBidi](https://github.com/fribidi/fribidi) (through libass) | Text shaping and right-to-left scripts in captions | Old MIT / LGPL 2.1+ |

GPU tone mapping on Apple silicon uses VideoToolbox through FFmpeg's `scale_vt` filter. Hardware encoding goes through FFmpeg's support for NVIDIA NVENC, AMD AMF, Intel Quick Sync, VA-API
and Apple VideoToolbox. Those are provided by your GPU vendor's drivers and the operating system.

## MCP server (optional `mcp` extra)

| Project | License |
|---|---|
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) and the [Model Context Protocol](https://modelcontextprotocol.io) | MIT |
| [Uvicorn](https://github.com/Kludex/uvicorn) | BSD-3-Clause |
| [Starlette](https://github.com/Kludex/starlette), [sse-starlette](https://github.com/sysid/sse-starlette) | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic), pydantic-core, [typing-inspection](https://github.com/pydantic/typing-inspection), [annotated-types](https://github.com/annotated-types/annotated-types) | MIT |
| [HTTPX 2 / HTTP Core 2](https://github.com/pydantic/httpx2), [h11](https://github.com/python-hyper/h11), [idna](https://github.com/kjd/idna), [Click](https://github.com/pallets/click) | BSD-3-Clause |
| [AnyIO](https://github.com/agronholm/anyio), [PyJWT](https://github.com/jpadilla/pyjwt), [truststore](https://github.com/sethmlarson/truststore), [attrs](https://github.com/python-attrs/attrs) | MIT |
| [jsonschema](https://github.com/python-jsonschema/jsonschema), [referencing](https://github.com/python-jsonschema/referencing), [jsonschema-specifications](https://github.com/python-jsonschema/jsonschema-specifications), [rpds-py](https://github.com/crate-py/rpds) | MIT |
| [python-multipart](https://github.com/Kludex/python-multipart), [OpenTelemetry API](https://github.com/open-telemetry/opentelemetry-python) | Apache-2.0 |
| [typing_extensions](https://github.com/python/typing_extensions) | PSF-2.0 |

This list follows the dependency tree of `mcp` 2.3; other versions may pull in slightly different
packages.

## Fonts

NookCut ships no fonts. It uses Impact when your operating system has it, or
[DejaVu Sans](https://dejavu-fonts.github.io/) Bold (free license, derived from Bitstream Vera),
which the Docker image installs. You can point `NOOKCUT_FONT` at any font you're allowed to use.

## Building, testing and docs

- [GitHub Actions](https://github.com/features/actions) with [actions/checkout](https://github.com/actions/checkout)
  and [actions/setup-python](https://github.com/actions/setup-python) (MIT) run the tests;
  [Homebrew](https://brew.sh/)'s `ffmpeg-full` and Debian's `ffmpeg` provide FFmpeg in CI.
- [setuptools](https://github.com/pypa/setuptools) (MIT) builds the package.
- [Contributor Covenant](https://www.contributor-covenant.org/) 2.1 (CC BY 4.0) is our Code of Conduct.
- [Keep a Changelog](https://keepachangelog.com/) and [Semantic Versioning](https://semver.org/)
  shape the changelog and version numbers.

## Ideas we learned from

This shaped a feature, but no code was copied from it:

- [Auto-Editor](https://github.com/WyattBlue/auto-editor) (Unlicense), for cutting silence out of
  recordings (NookCut's `tighten` template).

## Works with, not affiliated

NookCut writes files for [DaVinci Resolve](https://www.blackmagicdesign.com/products/davinciresolve),
a product and trademark of Blackmagic Design, and runs with many MCP clients and AI agents. NookCut
isn't affiliated with or endorsed by Blackmagic Design, OpenAI, any agent vendor, game publisher or
platform. Game and product names are trademarks of their owners.

---

Spotted something missing or wrong here? [Open an issue](https://github.com/nookverse/nookcut/issues)
or send a pull request.
