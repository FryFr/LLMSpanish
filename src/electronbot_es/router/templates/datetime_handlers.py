"""Time & date T2 template handlers — Colombian Spanish."""

from __future__ import annotations

import re
from datetime import datetime

from electronbot_es.router.intent_router import TemplateEntry


_DAYS = [
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
]
_MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


def _build_time(_: re.Match) -> str:
    now = datetime.now()
    h = now.hour % 12 or 12
    m = now.minute
    am_pm = "de la mañana" if now.hour < 12 else "de la tarde" if now.hour < 19 else "de la noche"
    if m == 0:
        return f"Son las {h} en punto {am_pm}, parce."
    return f"Son las {h} y {m} {am_pm}."


def _build_date(_: re.Match) -> str:
    now = datetime.now()
    day_name = _DAYS[now.weekday()]
    month_name = _MONTHS[now.month - 1]
    return f"Hoy es {day_name}, {now.day} de {month_name}."


TIME_TEMPLATE = TemplateEntry(
    id="time_now",
    patterns=(
        re.compile(r"\bque hora\b", re.IGNORECASE),
        re.compile(r"\btienes la hora\b", re.IGNORECASE),
        re.compile(r"\bme dices la hora\b", re.IGNORECASE),
        re.compile(r"\bdime la hora\b", re.IGNORECASE),
    ),
    build=_build_time,
)

DATE_TEMPLATE = TemplateEntry(
    id="date_today",
    patterns=(
        re.compile(r"\bque (dia|fecha)\b", re.IGNORECASE),
        re.compile(r"\bque fecha es hoy\b", re.IGNORECASE),
        re.compile(r"\bfecha de hoy\b", re.IGNORECASE),
    ),
    build=_build_date,
)
