# Patch series

`*.patch` files here are applied to the checked-out upstream tag by `atomic/ci/apply-patches.sh`, in
name order, at the repository root (audio.cpp vendors ggml in-tree, so a ggml patch touches
`external/ggml/...` like any other). Name them `NNNN-short-description.patch`. Keep each patch
small, explain why in its header, and offer it upstream: a patch that lands upstream is deleted here
on the next tag.

`apply-patches.sh --check`, run from the root of a checkout of the tag, changes nothing: it lists
every patch that does not apply on top of the ones before it, with git's reason, and flags the ones
that reverse cleanly as already upstream.

Current series (build system only; no source code differs from the upstream tag):

| Patch | Why |
| --- | --- |
| `0001-build-backend-dl-without-cpu-variants.patch` | Adds `ENGINE_ENABLE_BACKEND_DL` (default OFF): ggml backends as run-time loaded modules with one CPU backend. Upstream reaches `GGML_BACKEND_DL` only through `ENGINE_ENABLE_CPU_ALL_VARIANTS`, and ggml has no CPU variant set for Windows on Arm, so `win-cuda13-arm64` could not drop its separately built `ggml-cuda.dll` into the clang tree. |
