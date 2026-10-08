import time

class ElectromagneticLock:
    """12V 断电上锁型电磁锁：GPIO 高电平 = 通电开锁，低电平 = 断电上锁。"""

    def __init__(self, gpio_pin: int, mock: bool = False):
        self._dev = None
        self.mock = mock
        if not mock:
            try:
                from gpiozero import OutputDevice
                self._dev = OutputDevice(gpio_pin)
            except Exception:
                print(f"[lock] gpiozero 不可用，退回模拟模式")
                self.mock = True
        self._is_locked = True

    @property
    def is_locked(self) -> bool:
        return self._is_locked

    def unlock(self):
        if self._dev:
            self._dev.on()
        self._is_locked = False
        print(f"[lock] {time.strftime('%H:%M:%S')} 已开锁")

    def lock(self):
        if self._dev:
            self._dev.off()
        self._is_locked = True
        print(f"[lock] {time.strftime('%H:%M:%S')} 已落锁")
