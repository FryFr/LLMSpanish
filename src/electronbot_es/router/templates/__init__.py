"""T2 template handlers — dynamic text replies without hitting the LLM."""

from electronbot_es.router.templates.datetime_handlers import (
    DATE_TEMPLATE,
    TIME_TEMPLATE,
)

DEFAULT_TEMPLATES = [TIME_TEMPLATE, DATE_TEMPLATE]

__all__ = ["DEFAULT_TEMPLATES", "TIME_TEMPLATE", "DATE_TEMPLATE"]
