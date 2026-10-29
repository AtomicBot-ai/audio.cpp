#!/usr/bin/env bash
# package-linux.sh <build/bin> <out-dir> [cuda-home]
#
# Lay out a flat, symlink-free archive tree from a Linux build. Run from the source root.
#   - audiocpp_server and audiocpp_cli, and every unversioned shared library of build/bin
#     (libggml.so, libggml-base.so and the libggml-*.so backend modules, which ggml's loader finds
#     by that plain name); build-only tools such as audiocpp_espeak_pack stay out;
#   - with static eSpeak-ng, espeak-ng-data.bin and licenses/espeak-ng/ next to the executables
#     (the server finds the data relative to its own path);
#   - each versioned soname (libggml.so.0, ...) only where an ELF in the tree needs it;
#   - the CUDA runtime sonames the tree needs (cudart, cublas, cublasLt, and cufft for audio.cpp's
#     own CUDA iSTFT), copied from the toolkit;
#   - any other system library a needed soname resolves to (libgomp for OpenMP), because a bare
#     distro or container does not have it. Only BASE_SONAMES stay outside the archive.
# Symlinks are dereferenced because Atomic Chat's unzip writes a link out as a plain file.
# Fails if an ELF in the tree needs a soname that is neither inside it nor in BASE_SONAMES.
# Adapted from the stable-diffusion.cpp fork (AtomicBot-ai/stable-diffusion.cpp).
set -euo pipefail

bin="$1"
out="$2"
cuda_home="${3:-}"

rm -rf "$out"
mkdir -p "$out"

for name in audiocpp_server audiocpp_cli; do
  [ -f "$bin/$name" ] || { echo "::error::$name is missing from $bin"; exit 1; }
  cp -L "$bin/$name" "$out/$name"
done
for f in "$bin"/*.so; do
  if [ -f "$f" ]; then cp -L "$f" "$out/$(basename "$f")"; fi
done
if [ -f "$bin/espeak-ng-data.bin" ]; then
  cp -L "$bin/espeak-ng-data.bin" "$out/espeak-ng-data.bin"
  mkdir -p "$out/licenses"
  cp -RL "$bin/licenses/espeak-ng" "$out/licenses/espeak-ng"
fi

cuda_libs=()
if [ -n "$cuda_home" ]; then
  for d in "$cuda_home/targets/sbsa-linux/lib" "$cuda_home/targets/x86_64-linux/lib" "$cuda_home/lib64"; do
    [ -d "$d" ] && cuda_libs+=("$d")
  done
fi

# Present on every glibc distro; libcuda.so.1 comes with the NVIDIA driver and libvulkan.so.1 is
# the system Vulkan loader the GPU drivers register with.
BASE_SONAMES=" linux-vdso.so.1 libc.so.6 libm.so.6 libdl.so.2 libpthread.so.0 librt.so.1 ld-linux-aarch64.so.1 ld-linux-x86-64.so.2 libstdc++.so.6 libgcc_s.so.1 libcuda.so.1 libvulkan.so.1 "
is_base() { case "$BASE_SONAMES" in *" $1 "*) return 0 ;; esac; return 1; }
system_path() { /sbin/ldconfig -p | awk -v s="$1" '$1 == s { print $NF; exit }'; }

needed() {
  for e in "$out"/*; do
    [ -f "$e" ] || continue
    readelf -d "$e" 2>/dev/null | sed -n 's/.*(NEEDED).*\[\(.*\)\]/\1/p'
  done | sort -u
}

# A fixed point: a copied library can need another one.
for _ in 1 2 3 4; do
  added=0
  for so in $(needed); do
    [ -e "$out/$so" ] && continue
    if [ -e "$bin/$so" ]; then
      cp -L "$bin/$so" "$out/$so"
      added=1
      continue
    fi
    found=0
    for d in "${cuda_libs[@]}"; do
      if [ -e "$d/$so" ]; then
        cp -L "$d/$so" "$out/$so"
        added=1
        found=1
        break
      fi
    done
    [ "$found" = 1 ] && continue
    is_base "$so" && continue
    sys="$(system_path "$so")"
    if [ -n "$sys" ]; then
      cp -L "$sys" "$out/$so"
      echo "bundled system library $so ($sys)"
      added=1
    fi
  done
  [ "$added" = 0 ] && break
done

missing=0
for so in $(needed); do
  [ -e "$out/$so" ] && continue
  is_base "$so" && continue
  echo "::error::$so is needed but neither bundled nor part of the base system"
  missing=1
done
[ "$missing" = 0 ] || exit 1

cp LICENSE "$out/LICENSE"
