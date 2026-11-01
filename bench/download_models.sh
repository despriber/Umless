#!/usr/bin/env bash
# 下载 ASR 对比所用的 sherpa-onnx 模型（经 gh-proxy 加速，sha256 校验）
set -u
cd "$(dirname "$0")/models"
BASE="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
while read -r name sha; do
  [ -z "$name" ] && continue
  dir="${name%.tar.bz2}"
  if [ -d "$dir" ]; then echo "[skip] $dir"; continue; fi
  ok=0
  for url in "https://gh-proxy.com/$BASE/$name" "$BASE/$name"; do
    echo "[get] $url"
    curl -L --fail --retry 3 -sS -o "$name" "$url" || continue
    if echo "$sha  $name" | sha256sum -c --status; then ok=1; break; fi
    echo "[sha256 mismatch] $name"; rm -f "$name"
  done
  [ $ok = 1 ] || { echo "[FAIL] $name"; continue; }
  tar xjf "$name" && rm -f "$name" && echo "[ok] $dir"
done <<'LIST'
sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09.tar.bz2 7305f7905bfcf77fa0b39388a313f3da35c68d971661a65475b56fb2162c8e63
sherpa-onnx-paraformer-zh-int8-2025-10-07.tar.bz2 a071ee5419e14adb34d7f970ab98105a45e6608018b168f023ca2e4810744abe
sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2 393f8a14e2f5fb96746aaab342997a40641001fbd5bf9592a080a8329178ee96
sherpa-onnx-funasr-nano-int8-2025-12-30.tar.bz2 eb43d7ccc2e86b243f6a03b7df361033dda66db9523d1a92bf6aca2b50c9476b
LIST
echo "[done]"
