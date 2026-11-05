#!/bin/sh
# 解除蓝牙 RF-Kill 软阻塞（派 3B 的 BCM4343 有时开机被软阻塞，BLE 中心连不上锁控板）
for d in /sys/class/rfkill/rfkill*; do
    [ -f "$d/type" ] || continue
    if [ "$(cat "$d/type")" = "bluetooth" ]; then
        echo 1 > "$d/state"
    fi
done
exit 0
