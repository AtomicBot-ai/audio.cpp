# Atomic builds of audio.cpp

This fork builds **every** audio.cpp engine [Atomic Chat](https://github.com/AtomicBot-ai/Atomic-Chat)
ships, from an unmodified [0xShug0/audio.cpp](https://github.com/0xShug0/audio.cpp) release tag
(`vX.Y.Z`; side tags such as `v0.9.2-drama3-base-small` are not engine releases):

| Backend id | Archive | Built on |
| --- | --- | --- |
| `macos-arm64` | `audio-<tag>-bin-macos-arm64.zip`: universal arm64 + x86_64, Metal | macos-26 |
| `win-cpu-x64` | `audio-<tag>-bin-win-cpu-x64.zip` | windows-2022, MSVC |
| `win-vulkan-x64` | `audio-<tag>-bin-win-vulkan-x64.zip` | windows-2022, MSVC |
| `win-cuda12-x64` | `audio-<tag>-bin-win-cuda12-x64.zip`, needs the companion below | windows-2022, MSVC, CUDA 12.8 |
| `win-cudart-cu12` | `audio-<tag>-cudart-win-cu12-x64.zip` (companion: cudart, cuBLAS, cuBLASLt, cuFFT) | same job |
| `win-cuda13-x64` | `audio-<tag>-bin-win-cuda13-x64.zip`, CUDA runtime inside | windows-2022, MSVC, CUDA 13.4 |
| `linux-cpu-x64` | `audio-<tag>-bin-linux-cpu-x64.zip` | ubuntu-24.04 |
| `linux-vulkan-x64` | `audio-<tag>-bin-linux-vulkan-x64.zip` | ubuntu-24.04 |
| `linux-cpu-arm64` | `audio-<tag>-bin-linux-cpu-arm64.zip` | ubuntu-24.04-arm, GCC 14 |
| `linux-cuda13-arm64` | `audio-<tag>-bin-linux-cuda13-arm64.zip` (DGX Spark / GB10) | ubuntu-24.04-arm, CUDA 13.0 SBSA |
| `win-cpu-arm64` | `audio-<tag>-bin-win-cpu-arm64.zip` | windows-11-arm, clang |
| `win-cuda13-arm64` | `audio-<tag>-bin-win-cuda13-arm64.zip` (N1X) | the win-cpu-arm64 tree + ggml-cuda.dll from windows-2022 |

plus `SHA256SUMS`: 13 assets, checked by name before anything is published. They are consumed
through `atomic-chat-conf/backends/audiocpp-manifest.json`; the conf repo's `mirror-audiocpp.yml`
re-signs the Windows and macOS ones with Atomic Chat's certificates.

## What differs from upstream's own binaries

The source is the upstream tag plus [`atomic/patches/`](patches/) (build system only). The builds
differ in what they turn on and how they are packed:

- **No eSpeak-ng, as upstream's own binaries** (`static_espeak` off by default,
  `-DAUDIOCPP_STATIC_ESPEAK=OFF`): the archives stay Apache-2.0, and Kokoro, Piper, SanoTTS and
  Inflect v2 cannot speak. See [eSpeak-ng](#espeak-ng-not-shipped) below.
- **Flat zip archives.** `audiocpp_server` and `audiocpp_cli` sit at the root next to every runtime
  library they need and `LICENSE` (only with `static_espeak`: `espeak-ng-data.bin` and
  `licenses/espeak-ng/` as well).
  No `tools/`, no `audiocpp_gguf`, no `model_specs/`: `-DAUDIOCPP_DEPLOYMENT_BUILD=ON` compiles the
  specs into the binaries. Linux archives are symlink-free (Atomic Chat's unzip writes a link out as
  a plain file), Windows ones carry the VC++ runtime app-locally.
- **No model manager, no frontends:** `AUDIOCPP_BUILD_NATIVE_MODEL_MANAGER=OFF` (the core places
  models itself; no BoringSSL, no downloads from the server) and `AUDIOCPP_BUILD_SERVER_FRONTENDS=OFF`
  (the `external/audio.cpp-server-frontends` submodule is never checked out).
- **Portable CPU code everywhere.** `ENGINE_ENABLE_NATIVE_CPU=OFF` (audio.cpp forces `GGML_NATIVE`
  from it); Linux and Windows x64 use `ENGINE_ENABLE_CPU_ALL_VARIANTS=ON`: shared ggml, every backend a
  module loaded at run time, one CPU module per ISA level. Upstream ships both native and
  "portable" archives; Atomic Chat only wants the portable kind.
- **One universal macOS binary** instead of upstream's separate arm64 (Metal) and x64 (no Metal)
  archives.
- **CUDA:** the toolkits of the stable-diffusion.cpp fork (12.8 instead of upstream's 12.4; 13.4
  instead of 13.3), trimmed architecture lists (below), NCCL off.
- **Targets upstream does not publish:** `linux-cpu-arm64`, `linux-cuda13-arm64`, `win-cpu-arm64`,
  `win-cuda13-arm64`.

## Branches

| Branch   | What it is |
| -------- | ---------- |
| `main`   | A fast-forward mirror of upstream `main`. Never commit here. |
| `atomic` | The default branch: upstream plus **only added files** (`atomic/`, `.github/workflows/release-atomic.yml`). |
| `upstream-sync/<tag>` | Planned, as in the stable-diffusion.cpp fork: `atomic` with an upstream tag merged in, built, tested, merged back. The hatchet pipeline does not drive audio.cpp yet; until it does, sync by hand. |

Source code is never changed on a branch. Every build checks out the **upstream tag** and applies
the patch series in [`atomic/patches/`](patches/), so a sync is a merge of upstream files only and
the release notes say exactly what differs.

### Upstream workflows that must stay disabled

Upstream's workflow files come with every sync and stay in the tree (the branch only adds files),
so they are disabled in this fork's **Actions** settings, each one, before anything is pushed:

| Workflow | Why |
| --- | --- |
| `release.yml` | Runs on every pushed `v*` tag, builds upstream's archives, **pushes a tag and publishes a GitHub release under the same tag** this fork releases to, with differently named assets. |
| `release-hip.yml` | Called by `release.yml`. |
| `docker.yml` | Daily schedule; builds and pushes images to `ghcr.io/<this repo>`. |
| `linux-build.yml`, `mac-build.yml`, `windows-build.yml`, `nix-build.yml` | Run on pushes to `main` (the mirror) and on pull requests: hours of runner time for nothing. |
| `server-memory-guard.yml` | Pull requests touching the server. |

A tag push runs the workflow file of the **tagged commit**, which is upstream's, so deleting the
files on `atomic` would not help anyway; only the Actions setting does.

## Building a release

1. Fast-forward `main` to upstream (`gh repo sync AtomicBot-ai/audio.cpp -b main`, or the
   merge-upstream API), so the tag's commit exists here: the release is created at it.
2. Actions -> **Release (Atomic)** on `atomic`:
   - `upstream_tag`: e.g. `v0.9.3` (must match `^v\d+\.\d+\.\d+$`);
   - `targets`: `all`, or a comma-separated list of backend ids to rebuild while fixing one
     (`win-cuda13-arm64` builds `win-cpu-arm64` too);
   - `publish`: creates the release `<tag>` here, titled `audio.cpp <tag> (Atomic)`, and needs `all`.
     If that release exists, the run fails at once, unless
   - `replace` is on: the build's archives overwrite the release's, and archives this build no
     longer names are removed;
   - `static_espeak`: off by default, and leave it off; see [eSpeak-ng](#espeak-ng-not-shipped);
   - `candidate_id`: goes into the run title as `[cid:<id>]`, which is how a pipeline finds its run;
     `manual` by default.

   Run it once with `publish` off first: every archive is built and smoke-tested, nothing is
   published.
3. In atomic-chat-conf, mirror it: `<rev>` is the first seven characters of the `atomic` commit the
   build ran at (the run's `GITHUB_SHA`, also in the release notes).

   ```bash
   gh workflow run mirror-audiocpp.yml -R AtomicBot-ai/atomic-chat-conf \
     -f tag=v0.9.3 -f atomic_rev=<rev> -f manifest_path=staging
   ```

   conf mirrors and signs the archives as its release `<tag>-a<rev>` (tag regex
   `^v\d+\.\d+\.\d+(-a[0-9a-f]{7})?$`) and writes `backends/audiocpp-manifest.staging.json`; the
   core's engine-candidate check (`engine=audio-cpp`) and conf's approve workflow move it to the live
   manifest. Without `atomic_rev` conf mirrors under the bare tag.

The first job, `patches`, runs `atomic/ci/apply-patches.sh --check` on the tag; nothing is built
if a patch does not apply. Keep its id and name: a pipeline tells a patch conflict from a broken
build by it.

Every archive is smoke-tested on a machine without a GPU (`atomic/ci/smoke.py`): `--version` must
name its backends, `--list-devices` must show a CPU device, the archive must carry no eSpeak-ng
files, and `audiocpp_server --config <generated> --no-ui --backend cpu` must load
**MOSS-TTS-Nano 100M** q8 (needs neither eSpeak-ng nor a reference voice) and answer
`POST /v1/audio/speech` with a RIFF/WAVE body that is not silent (48 kHz stereo). Any other answer
fails with the server's error. With `static_espeak` the smoke instead requires `espeak-ng-data.bin`
and `licenses/espeak-ng/` and also speaks with Kokoro 82M q8, which is what catches a static build
whose eSpeak-ng does not work. On Linux this runs in a bare `python:3.12-slim` container, so a
missing runtime library fails CI. `smoke.py` pins the models to one revision of
`audio-cpp/audio.cpp-gguf` with size and SHA-256 and downloads them into `--models-dir` (193 MB,
Kokoro another 190 MB), which CI caches per runner OS.

To run it on an unpacked archive by hand:

```bash
python3 -I atomic/ci/smoke.py --dir <unpacked archive> --models-dir ~/audio-smoke-models \
  --backend cpu   # or metal / vulkan / cuda on a machine that has one
```

### Fixing a build by hand

`atomic` holds upstream `main`, not the tag, so check the series against the tag in a worktree;
the script reads the patches next to itself and applies them in the current directory:

```bash
git fetch upstream --tags
git worktree add --detach ../audio.cpp-v0.9.3 v0.9.3
cd ../audio.cpp-v0.9.3
bash ../audio.cpp/atomic/ci/apply-patches.sh --check   # what the patches job saw, changes nothing
```

A patch is reworked there (`git apply --3way`, resolve, write the diff of the touched files back
into the patch keeping its header). Commit only `atomic/` or `release-atomic.yml` on `atomic` (or
on a sync branch: a dispatch on a branch runs that branch's copy of the workflow).

## eSpeak-ng: not shipped

**Decision (2026-10-29): Atomic Chat's audio.cpp builds carry no eSpeak-ng.** eSpeak-ng is
GPL-3.0-or-later: linking it into `audiocpp_server` makes the server a combined work distributed
under the GPL, and the server is to stay Apache-2.0, as upstream audio.cpp is. So `static_espeak` is
off by default, releases are built without it (exactly as upstream's own binaries are), and the smoke
requires that no eSpeak-ng file is in an archive.

**Consequence.** Every model whose text frontend phonemizes with eSpeak-ng fails its speech
request with HTTP 500 `Could not load eSpeak-ng; install the shared library or provide its path`,
unless the machine happens to have an eSpeak-ng install the server finds (Homebrew or the distro's
library directories, or the `espeak_library_path` / `espeak_data_path` model options); Atomic Chat
does not rely on that.

| Cannot speak (eSpeak-ng frontends) | Speaks (no eSpeak-ng needed) |
| --- | --- |
| Kokoro, Piper, SanoTTS, Inflect v2 (and KittenTTS; ZipVoice's English text) | Qwen3-TTS, MOSS-TTS-Nano, ACE-Step, Stable Audio |

Atomic Chat's catalog must not offer the left column with these builds, and the smoke speaks with
MOSS-TTS-Nano instead of Kokoro.

### Opt-in: static eSpeak-ng (GPL-3.0-or-later)

What `static_espeak: true` does, kept working for the case the decision changes. The build downloads
eSpeak-ng 1.52.0 (SHA-256 pinned by upstream's `tools/cmake/espeak_static.cmake`), links it
statically into `audiocpp_server` and `audiocpp_cli`, and stages `espeak-ng-data.bin` (about
17.5 MiB) and `licenses/espeak-ng/` next to them; the server finds the data relative to its own path
and unpacks it into a per-user cache on first use (`%LOCALAPPDATA%\audio.cpp\espeak-data` on
Windows, `$XDG_CACHE_HOME` or `$HOME/.cache` elsewhere): whoever starts the server must give it a
writable one. The packaging steps copy and check those files only with `static_espeak`; the smoke
then requires them and speaks with Kokoro as well as MOSS-TTS-Nano, and the release notes state the
GPL terms.

**Licensing.** eSpeak-ng is GPL-3.0-or-later, so the binaries that link it are a combined work
distributed under the GPL. The obligation is met by:
- `licenses/espeak-ng/` in every archive: eSpeak-ng's `COPYING`, its original source archive
  (`espeak-ng-1.52.0.tar.gz`) and audio.cpp's build integration (`espeak_static.cmake`,
  `espeak_patch.cmake`);
- this public repository: the `atomic` commit named in the release notes and the upstream tag it
  builds (audio.cpp is Apache-2.0, which is GPL-3.0-compatible) are the rest of the corresponding source.

**Where static eSpeak-ng works.** Upstream's `espeak_static.cmake` refuses a cross build
(`CMAKE_CROSSCOMPILING`): it compiles the phoneme data with an eSpeak tool built in the same tree.

| Target | Build | Static eSpeak-ng |
| --- | --- | --- |
| `macos-arm64` | native: universal on an arm64 host is not a cross build | yes; `CMAKE_OSX_ARCHITECTURES` and `MACOSX_DEPLOYMENT_TARGET` go in through the **environment**, because the eSpeak ExternalProject is only given the compiler; without them its library is arm64-only and the x86_64 slice does not link (checked locally: both slices speak) |
| `linux-*-x64`, `linux-*-arm64` | native on x64 / arm64 runners | yes |
| `win-*-x64` | native, MSVC | yes (the configuration upstream validated) |
| `win-cpu-arm64` | **native on windows-11-arm** with clang (the stable-diffusion.cpp fork cross-compiles it on windows-2022, which static eSpeak-ng refuses) | yes, if the image's clang builds eSpeak-ng (unverified until the first run) |
| `win-cuda13-arm64` | the win-cpu-arm64 tree; only ggml-cuda.dll is cross-compiled | inherited from win-cpu-arm64 |

If windows-11-arm cannot build it, the fallback for a `static_espeak` release is the
stable-diffusion.cpp fork's cross build (`arm64-windows-llvm.cmake` on windows-2022) without
eSpeak-ng for the two arm64 Windows targets, and that gap must be stated in the release.

eSpeak-ng's phoneme compiler truncates paths at 160 bytes: keep the build directory short
(`/Users/runner/work/audio.cpp/audio.cpp/build` is fine; a deep scratch path is not).

## Choices that are easy to get wrong

- **CUDA architectures** (trimmed against upstream's all-real lists, to keep ggml-cuda small): SASS
  for the GPUs Atomic Chat users have (Ampere 86, Ada 89, Blackwell 120a; GB10/N1X 121a), PTX
  floors the driver JITs for the rest (Pascal `61-virtual` on CUDA 12 only, Turing 75, A100 80;
  Hopper 90 on arm64). ggml rewrites 12X to 12Xa itself. A PTX-only GPU pays a one-time JIT on
  first load.
- **audiocpp_server links cudart and cuFFT itself** on the CUDA builds (audio.cpp's own CUDA iSTFT
  and torch-compatible RNG), not only ggml-cuda: it does not start without those DLLs. So the
  `win-cudart-cu12` companion carries `cufft64_*.dll` too, and the app must unpack it beside
  `win-cuda12-x64` (the smoke does exactly that). Without an NVIDIA driver ggml-cuda fails to load
  and the CPU remains.
- **Windows on Arm is two builds merged**, as llama.cpp does it. ggml-cpu refuses MSVC on ARM, so
  the tree is clang (`atomic/cmake/arm64-windows-llvm-native.cmake`), and `ggml-cuda.dll` is
  cross-compiled with MSVC + x64 nvcc against NVIDIA's arm64 libraries
  (`atomic/cmake/arm64-windows-msvc-cuda.cmake`) from the vendored `external/ggml` with the same
  `GGML_*` options audio.cpp sets (it sets no compile definitions that would change ggml's
  structures). Dropping it in needs a run-time loaded CPU backend, which only
  `ENGINE_ENABLE_CPU_ALL_VARIANTS` gives upstream, and ggml has no CPU variant set for Windows on
  Arm: hence patch `0001` (`ENGINE_ENABLE_BACKEND_DL`). **Known gaps** of the merged build: the clang
  `audiocpp_server` has no `ENGINE_ENABLE_CUDA`, so `--version` lists `backends: cpu` only (the
  core must not gate CUDA on that line for this id), and audio.cpp's own CUDA kernels (iSTFT,
  torch RNG) run on their CPU path; ggml's CUDA backend is selected by registry name and works.
- **OpenMP** follows upstream: on for Linux and Windows x64 CPU/Vulkan (`/openmp:experimental`,
  `vcomp140.dll` shipped), off for Windows CUDA, macOS and Windows on Arm.
- **The CRT** is the DLL one on every Windows build, stated explicitly; the merged arm64 build shares
  heaps between clang and MSVC halves.
- **`--backend` and `--no-ui` must always be passed:** the server defaults to `cuda` and serves its
  WebUI when given a config.

The toolchain files come from ggml-org/llama.cpp `cmake/` (MIT): `arm64-windows-msvc-cuda.cmake`
verbatim, `arm64-windows-llvm-native.cmake` without the system name. `install-cuda-windows.ps1`,
`package-*.{sh,ps1}`, `check-pe.ps1` and `apply-patches.sh` are adapted from the stable-diffusion.cpp
fork.

## Unverified until the first CI run

Checked locally (macOS, M4 Pro, v0.9.3): the `macos-arm64` job's exact CMake flags without
eSpeak-ng build a universal binary in about 3 minutes, the job's own package step packs it
(`audiocpp_server`, `audiocpp_cli`, `LICENSE`; 65 MB zipped), and that archive passed `smoke.py`
(MOSS-TTS-Nano) on CPU, on Metal, and with the x86_64 slice alone under Rosetta. An earlier
`static_espeak` build of the same job spoke Kokoro the same three ways. Patch `0001`'s
`ENGINE_ENABLE_BACKEND_DL` built on macOS arm64 loads `libggml-cpu.so` from beside the server and
speaks. Everything else is first exercised by CI: the windows-11-arm image's Visual Studio, clang,
CMake and Ninja, audio.cpp under clang for Windows (audio.cpp compiles with `-pedantic-errors`,
which upstream never ran with clang on Windows; eSpeak-ng too with `static_espeak`), the standalone
ggml-cuda cross build, CUDA 13.4's arm64 redist, MOSS-TTS-Nano's speed on the CPU runners, and the
archive sizes with cuFFT.
