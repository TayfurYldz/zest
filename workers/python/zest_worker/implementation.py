"""Static Worker implementation map. No dynamic import, eval, or shell dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Mapping

from .browser_page import execute_browser_page
from .http_authentication import execute_http_authentication
from .http_authorization import execute_http_authorization
from .http_raw_exchange import execute_http_raw_exchange
from .http_state_transition import execute_http_state_transition
from .http_transaction import execute_http_transaction

Executor = Callable[
    [Mapping[str, Any]], tuple[str, dict[str, Any], dict[str, Any] | None]
]


def _execute_echo(
    request: Mapping[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    arguments = request.get("arguments")
    if not isinstance(arguments, Mapping):
        arguments = {}
    message = arguments.get("message", "")
    if not isinstance(message, str):
        return (
            "EXECUTION_FAILED",
            {},
            {"error": "diagnostic.echo message must be a string"},
        )
    return (
        "SUCCEEDED",
        {"echoed": message, "capability": "diagnostic.echo"},
        None,
    )


IMPLEMENTATION_EXECUTORS: dict[str, Executor] = {
    "diagnostic.echo": _execute_echo,
    "http.authorization.differential": execute_http_authorization,
    "http.state_transition": execute_http_state_transition,
    "http.transaction": execute_http_transaction,
    "http.raw_exchange": execute_http_raw_exchange,
    "http.authentication": execute_http_authentication,
    "browser.page": execute_browser_page,
}
