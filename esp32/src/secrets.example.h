// 锁控板密钥配置：复制成 secrets.h 再填真实值（secrets.h 不入库）
// 与派上 pi/config.yaml 的 security.secret 保持一致
#pragma once

#define LOCK_SECRET "change-me"      // HMAC 密钥，与派共用
#define LOCK_DEVICE "lock-01"        // 命令签名里的设备标识
#define BLE_NAME    "FRIDGE-LOCK-01" // 广播名，派按这个前缀找设备
