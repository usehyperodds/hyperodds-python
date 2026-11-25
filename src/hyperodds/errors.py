from __future__ import annotations

from typing import Any


class HyperoddsError(Exception):
    """Base class for every error this package raises."""


class APIConnectionError(HyperoddsError):
    """The API could not be reached, or did not answer in time, after every retry."""


class APIError(HyperoddsError):
    """The API answered with an error.

    ``status`` is the HTTP status, ``code`` the error's name when the API sent one (tRPC's
    ``BAD_REQUEST``, ``NOT_FOUND``, ``TOO_MANY_REQUESTS``, ..., or the forecast record's
    ``not_found``), and ``procedure`` the procedure that failed, or the path for a read outside
    tRPC (``udf/history``).
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str | None = None,
        procedure: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.procedure = procedure

    def __str__(self) -> str:
        where = f" ({self.procedure})" if self.procedure else ""
        return f"{self.status} {self.message}{where}"


class NotFoundError(APIError):
    """No market, question or cluster has that id, or the record has no such day."""


class RateLimitError(APIError):
    """The API's request budget for this address is spent.

    ``retry_after`` is the wait in seconds the API asked for, when it named one.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


def error_from(
    status: int,
    body: Any,
    retry_after: float | None = None,
    *,
    fallback: str = "The API sent no message.",
) -> APIError:
    """Build the error for one failed call from its HTTP status and tRPC error body.

    ``fallback`` is the message when the body carries none, such as a proxy's error page.
    """
    message, code, procedure = fallback, None, None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        message = str(err.get("message") or message)
        data = err.get("data")
        if isinstance(data, dict):
            code = data.get("code")
            procedure = data.get("path")
            status = int(data.get("httpStatus") or status)
    elif isinstance(err, str):
        message = err

    # tRPC also answers NOT_FOUND for a misspelled procedure: a caller bug, so a plain APIError.
    if code == "NOT_FOUND" and not message.startswith("No procedure found"):
        return NotFoundError(message, status=status, code=code, procedure=procedure)
    if status == 429:
        return RateLimitError(
            message, status=status, code=code, procedure=procedure, retry_after=retry_after
        )
    return APIError(message, status=status, code=code, procedure=procedure)
