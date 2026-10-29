"""批次编号：按存入日期分两组 1-8 / 11-18，16 天一轮，相邻周期不撞号。

公式必须与树莓派离线登记里的实现完全一致（pi/app.py 内嵌同款），
联网后后端按 stored_at 重算，不信任派传来的编号。
"""
from datetime import date, datetime, timedelta

EPOCH = date(2026, 1, 1)


def batch_no(on) -> int:
    if isinstance(on, str):
        on = datetime.strptime(on[:10], "%Y-%m-%d").date()
    d = (on - EPOCH).days % 16
    return d + 1 if d < 8 else d + 3


def keep_until(stored_at, days: int) -> str:
    """存放至 = 存入日 + 天数（默认 6 天，当天算第 1 天，存放至当天仍有效）。"""
    if isinstance(stored_at, str):
        stored_at = datetime.strptime(stored_at[:10], "%Y-%m-%d").date()
    return (stored_at + timedelta(days=max(1, days))).strftime("%Y-%m-%d")
