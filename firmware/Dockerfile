# Pinned ARM cross-compile toolchain for building custom Robox MicroPython
# firmware (frozen main.py + core libs -- see docs/RELEASE.md). Exists so a
# release build doesn't depend on whatever happens to be on a dev's PATH:
# every dev and CI gets the exact same compiler.
#
# Build once:   docker build -t robox-fw-build firmware/
# Used by:      ./tools/pico fw-build (docker run, repo mounted at /repo)
FROM debian:12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        python3 \
        gcc-arm-none-eabi \
        libnewlib-arm-none-eabi \
        libstdc++-arm-none-eabi-newlib \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /repo
