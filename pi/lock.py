import threading
import time


class ElectromagneticLock:
    """瞬间通电开锁型电控锁（2026-10 换装）：
    - 红/黑 = 控制：待机断电！继电器通电 pulse_seconds 秒（说明书 0.5~35s）弹开锁扣，随后必须断开
    - 蓝/白 = 状态反馈：无电干接点，短路=已上锁，断开=开锁/锁舌弹出；
      一根接 feedback_gpio（软件内部上拉），一根接树莓派 GND，两根可互换，切勿接电源
    - 关门压回锁舌即自动重新上锁，无需软件"落锁"
    """

    def __init__(self, gpio_pin: int, mock: bool = False,
                 feedback_pin: int | None = None, pulse_seconds: float = 1.0,
                 relock_estimate: float = 60.0):
        self.mock = mock
        self._relay = None
        self._fb = None
        self._locked = True
        self._pulse = pulse_seconds
        self._relock_estimate = relock_estimate
        if not mock:
            try:
                from gpiozero import OutputDevice
                self._relay = OutputDevice(gpio_pin)     # 默认断开，锁只在脉冲期间通电
            except Exception:
                print("[lock] gpiozero 不可用，退回模拟模式")
                self.mock = True
            if feedback_pin is not None:
                try:
                    from gpiozero import Button
                    self._fb = Button(feedback_pin, pull_up=True, bounce_time=0.05)
                    print(f"[lock] 状态反馈已启用: GPIO{feedback_pin} 短路=上锁")
                except Exception:
                    print("[lock] 反馈 GPIO 不可用，锁状态改按时间估算")

    @property
    def has_feedback(self) -> bool:
        return self._fb is not None

    @property
    def is_locked(self) -> bool:
        if self._fb is not None:
            return self._fb.is_pressed      # 触点闭合(拉低)=上锁
        return self._locked

    def unlock(self):
        if self._relay:
            self._relay.on()
            threading.Timer(self._pulse, self._relay.off).start()
        if self._fb is None:
            self._locked = False            # 无反馈线时，按"关门时间"估算已回锁
            threading.Timer(self._relock_estimate, lambda: setattr(self, "_locked", True)).start()
        print(f"[lock] {time.strftime('%H:%M:%S')} 开锁脉冲 {self._pulse:.1f}s"
              + ("（反馈实时判定）" if self.has_feedback else "（无反馈，按时长估算）"))

    def lock(self):
        """关门自动回锁；保留接口兼容旧调用（立即切断继电器）。"""
        if self._relay:
            self._relay.off()
        self._locked = True
