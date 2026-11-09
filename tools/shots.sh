#!/usr/bin/env bash
# Скриншоты мини-аппа в демо-режиме через headless Edge (нужен tools/dev_server.py на 8099).
OUT="$1"; mkdir -p "$OUT"
EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
shot() {
  local url="http://127.0.0.1:8099/?demo&shot&$2"
  "$EDGE" --headless=new --disable-gpu --hide-scrollbars --window-size=500,1000 --virtual-time-budget=5000 \
    --screenshot="$(cygpath -w "$OUT/$1.png")" "$url" >/dev/null 2>&1
}
shot hold "hold=1"
shot sub "screen=subscribe"
for i in $(seq 0 11); do shot "s$i" "slide=$i"; done
