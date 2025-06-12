from __future__ import annotations

from terminal_manager import AuthenticationError


class HostKeyUnknownError(AuthenticationError):
    """Error to indicate that the host key is unknown."""

    def __init__(self, host: str) -> None:
        super().__init__()
        self.host = host
        self.message = f"Host key of {host} is unknown"
