"""Helpers compartidos por los submodelos."""

import math
from datetime import date, datetime


def parse_date(date_str: str) -> date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def decay_weight(match_date: date, reference: date, lam: float) -> float:
    """Peso exponencial según antigüedad del partido."""
    days = max(0, (reference - match_date).days)
    return math.exp(-lam * days)


def safe(val, default=0):
    """Devuelve val si no es None; default en caso contrario."""
    return val if val is not None else default
