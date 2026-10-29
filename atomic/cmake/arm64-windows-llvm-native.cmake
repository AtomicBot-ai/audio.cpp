# ggml-org/llama.cpp's cmake/arm64-windows-llvm.cmake (MIT) without CMAKE_SYSTEM_NAME and
# CMAKE_SYSTEM_PROCESSOR, for a NATIVE build on a Windows on Arm host (windows-11-arm). Naming the
# system makes CMake treat the build as cross-compiling even on an arm64 host, and audio.cpp's
# static eSpeak-ng refuses a cross build: its phoneme data is compiled by a host tool built in the
# same tree. Same compiler, target and flags as llama.cpp's cross toolchain otherwise.
set( CMAKE_C_COMPILER    clang )
set( CMAKE_CXX_COMPILER  clang++ )

set( target arm64-pc-windows-msvc )

set( CMAKE_C_COMPILER_TARGET   ${target} )
set( CMAKE_CXX_COMPILER_TARGET ${target} )
set( CMAKE_ASM_COMPILER_TARGET ${target} )

set( arch_c_flags "-march=armv8.7-a -fvectorize -ffp-model=fast -fno-finite-math-only" )
set( warn_c_flags "-Wno-format -Wno-unused-variable -Wno-unused-function -Wno-gnu-zero-variadic-macro-arguments" )

set( CMAKE_C_FLAGS_INIT   "${arch_c_flags} ${warn_c_flags}" )
set( CMAKE_CXX_FLAGS_INIT "${arch_c_flags} ${warn_c_flags}" )
