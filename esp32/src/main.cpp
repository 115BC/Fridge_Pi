// 冰箱锁控板固件：ESP32 做 BLE 外设，继电器脉冲开锁 + 锁舌反馈 + 实体按钮
//
// 只用 Arduino core 自带的 BLE 库（Bluedroid），不依赖外部库，离线也能编译。
//
// 接线（共地、共电源；锁的电流回路不经过本板，见 esp32/README.md）：
//   GPIO26 -> 继电器 IN（高=吸合，空闲必须为低，fail-secure）
//   GPIO32 <- 门锁干接点（内部上拉；短路=已上锁=低电平）  另一根接 GND，切勿接电源
//   GPIO33 <- 实体开锁按钮（内部上拉；按下=接地）        另一根接 GND
//
// GATT（服务 SVC_UUID）：
//   STATE(读+通知) 文本 "S,<locked>,<open_seq>,<btn_seq>,<reset_reason>,<fw>"
//   CMD  (写)      17 字节二进制: 0x55 | nonce[8] | sig[8]
//                  sig = HMAC_SHA256(secret, "<device>|U|<nonce_hex>") 的前 8 字节
//   二进制命令正好落在 BLE 默认 MTU(23) 的单包内，避免长写分片；板子不连 WiFi 没有时钟，
//   防重放靠 nonce 去重 + 命令最小间隔，不用时间戳。

#include <Arduino.h>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <esp_system.h>
#include "mbedtls/md.h"
#include "secrets.h"

static const int PIN_RELAY = 26;
static const int PIN_FEEDBACK = 32;
static const int PIN_BUTTON = 33;

static const uint16_t PULSE_MS = 1000;      // 开锁脉宽（锁说明书 0.5~35s）
static const uint32_t CMD_GAP_MS = 1500;    // 两条开锁命令最小间隔
static const uint32_t HEARTBEAT_MS = 10000; // 定时通知，让派能判断板子活着
static const char *FW_VER = "1.0";
static const uint8_t CMD_OP_UNLOCK = 0x55;

static const char *SVC_UUID = "6f1a1001-8d3b-4f0a-9a2e-5b7c9d0e1f01";
static const char *CHR_STATE_UUID = "6f1a1002-8d3b-4f0a-9a2e-5b7c9d0e1f01";
static const char *CHR_CMD_UUID = "6f1a1003-8d3b-4f0a-9a2e-5b7c9d0e1f01";

static volatile bool s_locked = true;
static volatile uint32_t s_openSeq = 0;
static volatile uint32_t s_btnSeq = 0;
static uint32_t s_pulseUntil = 0;
static bool s_pulseActive = false;
static uint32_t s_lastCmdAt = 0;
static bool s_connected = false;
static BLECharacteristic *s_stateCh = nullptr;

// 最近 16 条 nonce，防重放
static uint8_t s_nonces[16][8];
static bool s_nonceUsed[16] = {false};
static int s_nonceSlot = 0;

static void hmac8(const String &data, uint8_t out[8]) {
    uint8_t buf[32];
    memset(out, 0, 8);
    const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    if (!info) return;
    if (mbedtls_md_hmac(info, (const uint8_t *)LOCK_SECRET, strlen(LOCK_SECRET),
                        (const uint8_t *)data.c_str(), data.length(), buf) != 0) return;
    memcpy(out, buf, 8);
}

static String hex8(const uint8_t n[8]) {
    char s[17];
    for (int i = 0; i < 8; i++) sprintf(s + 2 * i, "%02x", n[i]);
    s[16] = '\0';
    return String(s);
}

static String stateString() {
    return String("S,") + (s_locked ? 1 : 0) + "," + s_openSeq + "," + s_btnSeq + ","
           + (int)esp_reset_reason() + "," + FW_VER;
}

static void notifyState() {
    if (!s_stateCh || !s_connected) return;   // 没连上时 notify 会失败，直接跳过
    std::string s = stateString().c_str();
    s_stateCh->setValue(s);
    s_stateCh->notify();
}

static void pulseUnlock() {
    s_pulseActive = true;
    s_pulseUntil = millis() + PULSE_MS;
    digitalWrite(PIN_RELAY, HIGH);
    Serial.println("[lock] 继电器脉冲开始");
}

static bool nonceSeen(const uint8_t n[8]) {
    for (int i = 0; i < 16; i++) {
        if (s_nonceUsed[i] && memcmp(s_nonces[i], n, 8) == 0) return true;
    }
    return false;
}

static void nonceRemember(const uint8_t n[8]) {
    memcpy(s_nonces[s_nonceSlot], n, 8);
    s_nonceUsed[s_nonceSlot] = true;
    s_nonceSlot = (s_nonceSlot + 1) % 16;
}

class CmdCallbacks : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *ch) override {
        std::string v = ch->getValue();
        if (v.size() != 17 || (uint8_t)v[0] != CMD_OP_UNLOCK) return;
        uint8_t nonce[8], want[8];
        memcpy(nonce, &v[1], 8);
        hmac8(String(LOCK_DEVICE) + "|U|" + hex8(nonce), want);
        if (memcmp(want, &v[9], 8) != 0) return;        // 签名不对：静默丢弃
        if (nonceSeen(nonce)) return;                   // 重放
        if (millis() - s_lastCmdAt < CMD_GAP_MS) return;
        s_lastCmdAt = millis();
        nonceRemember(nonce);
        pulseUnlock();
    }
};

class SrvCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer *) override {
        s_connected = true;
        BLEDevice::stopAdvertising();   // 连上后停广播，省电也避免第三台设备抢连
        Serial.println("[ble] 树莓派已连接");
        notifyState();
    }
    void onDisconnect(BLEServer *) override {
        s_connected = false;
        BLEDevice::startAdvertising();  // 断开后恢复广播，等派重连
        Serial.println("[ble] 连接断开，恢复广播");
    }
};

static void pollFeedback() {
    bool locked = (digitalRead(PIN_FEEDBACK) == LOW);   // 触点闭合=上锁
    if (locked == s_locked) return;
    s_locked = locked;
    if (!locked) s_openSeq++;    // 锁舌弹开一次，派据此判定"真的开过门"
    notifyState();
}

static void pollButton() {
    static bool stable = false;
    static bool rawPrev = true;
    static uint32_t changeAt = 0;
    bool raw = (digitalRead(PIN_BUTTON) == LOW);
    uint32_t now = millis();
    if (raw != rawPrev) {
        rawPrev = raw;
        changeAt = now;
    }
    if (now - changeAt < 30) return;
    if (raw == stable) return;
    stable = raw;
    if (stable) {                 // 按下沿：本地立即开锁（断连时也能应急开门），事件靠 btnSeq 补报
        s_btnSeq++;
        pulseUnlock();
        notifyState();
    }
}

void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.printf("[lock] boot fw=%s reason=%d\n", FW_VER, (int)esp_reset_reason());

    pinMode(PIN_RELAY, OUTPUT);
    digitalWrite(PIN_RELAY, LOW);          // 待机断电：上电即处于安全态
    pinMode(PIN_FEEDBACK, INPUT_PULLUP);
    pinMode(PIN_BUTTON, INPUT_PULLUP);
    s_locked = (digitalRead(PIN_FEEDBACK) == LOW);

    BLEDevice::setMTU(128);
    BLEDevice::init(BLE_NAME);
    BLEServer *server = BLEDevice::createServer();
    server->setCallbacks(new SrvCallbacks());
    BLEService *svc = server->createService(SVC_UUID);
    s_stateCh = svc->createCharacteristic(CHR_STATE_UUID,
                                          BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
    s_stateCh->addDescriptor(new BLE2902());
    s_stateCh->setValue(std::string(stateString().c_str()));
    BLECharacteristic *cmdCh = svc->createCharacteristic(
        CHR_CMD_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
    cmdCh->setCallbacks(new CmdCallbacks());
    svc->start();

    BLEAdvertising *adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(SVC_UUID);
    adv->setScanResponse(true);
    BLEDevice::startAdvertising();
    Serial.printf("[lock] 广播名=%s 等待树莓派连接\n", BLE_NAME);
}

void loop() {
    pollFeedback();
    pollButton();
    if (s_pulseActive && (int32_t)(millis() - s_pulseUntil) >= 0) {
        digitalWrite(PIN_RELAY, LOW);
        s_pulseActive = false;
    }
    static uint32_t lastBeat = 0;
    if (millis() - lastBeat > HEARTBEAT_MS) {
        lastBeat = millis();
        notifyState();
    }
    delay(10);
}
