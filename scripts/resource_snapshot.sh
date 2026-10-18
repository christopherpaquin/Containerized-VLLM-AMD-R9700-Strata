#!/usr/bin/env bash
set -euo pipefail
date --iso-8601=seconds
free -h
vmstat 1 3
for device in /sys/class/drm/card*/device; do
  if [[ -f "$device/mem_info_vram_total" ]]; then
    echo "$device"
    for metric in mem_info_vram_total mem_info_vram_used gpu_busy_percent; do
      [[ ! -f "$device/$metric" ]] || { echo "$metric"; cat "$device/$metric"; }
    done
  fi
done
ps -eo pid,rss,vsz,comm --sort=-rss | head -12
