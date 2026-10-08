"""三色紧急度：红=超期，黄=临期(warn_days 内)，绿=正常。默认按紧急度排序。"""
from datetime import date, datetime

RED, YELLOW, GREEN = "red", "yellow", "green"
_ORDER = {RED: 0, YELLOW: 1, GREEN: 2}
_LABEL = {RED: "超期", YELLOW: "临期", GREEN: "正常"}


def color_of(expire_at: str, warn_days: int = 2, today: date | None = None) -> str:
    today = today or date.today()
    try:
        exp = datetime.strptime(expire_at[:10], "%Y-%m-%d").date()
    except ValueError:
        return GREEN
    if exp < today:
        return RED
    if (exp - today).days <= warn_days:
        return YELLOW
    return GREEN


def label(color: str) -> str:
    return _LABEL.get(color, color)


def decorate(items: list[dict], warn_days: int = 2) -> list[dict]:
    """给每条记录加 color/color_label，并按 红→黄→绿、到期日升序 排序。"""
    for it in items:
        c = color_of(it.get("expire_at", ""), warn_days)
        it["color"] = c
        it["color_label"] = label(c)
    items.sort(key=lambda x: (_ORDER[x["color"]], x.get("expire_at", "")))
    return items
