from __future__ import annotations

from flask import current_app, flash

from ..services.common import BulkResult, OperationResult
from ..services.container import Services


def svc() -> Services:
    return current_app.extensions["deviceadmin"]


def to_int(value: str | None, default: int | None = None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def ids_from_form(form, key: str) -> list[int]:
    return [i for i in (to_int(v) for v in form.getlist(key)) if i is not None]


def flash_result(result: OperationResult) -> None:
    flash(result.message, "success" if result.ok else "danger")


def flash_bulk(result: BulkResult, verb: str) -> None:
    if not result.total and not result.skipped:
        flash("Nothing selected.", "warning")
        return
    category = "success" if result.ok else ("warning" if result.succeeded else "danger")
    message = result.summary(verb)
    if result.failed:
        first = "; ".join(f"{name}: {reason}" for name, reason in result.failed[:3])
        message += f". First failures - {first}" + (" ..." if len(result.failed) > 3 else "")
    flash(message, category)
