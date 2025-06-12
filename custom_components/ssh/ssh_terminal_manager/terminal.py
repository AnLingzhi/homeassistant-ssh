from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import re
import time

import paramiko
import socks
from terminal_manager import (
    AuthenticationError,
    CommandOutput,
    ConnectError,
    ExecutionError,
    Terminal,
)

from .error import HostKeyUnknownError
from .ping import Ping

DEFAULT_PORT = 22
DEFAULT_PING_TIMEOUT = 4
DEFAULT_SSH_TIMEOUT = 4
DEFAULT_ADD_HOST_KEYS = False
DEFAULT_LOAD_SYSTEM_HOST_KEYS = False
DEFAULT_INVOKE_SHELL = False

WIN_TITLE = re.compile(r"\x1b\]0\;.*?\x07")
WIN_NEWLINE = re.compile(r"\x1b\[\d+\;1H")
ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

END = "__exit_code__"
PS_CODE = "$LastExitCode"
LINUX_CODE = "$?"
CMD_CODE = r"%errorlevel%"

ECHO_STRING = f'echo "{END}|{PS_CODE}|{LINUX_CODE}|{CMD_CODE}"'
EXIT_STRING = "exit"
CMD_START = "\x1b[?25l\x1b[2J\x1b[m\x1b[H"
CMD_TEST = "Microsoft Windows"

logging.getLogger("paramiko").setLevel(logging.CRITICAL)


async def _run_in_executor(prefix: str, func: Callable, *args):
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(thread_name_prefix=prefix) as executor:
        return await loop.run_in_executor(executor, func, *args)


def _detect_cmd(stdout_file: paramiko.ChannelFile) -> bool:
    if stdout_file.read(16).decode() != CMD_START:
        return False

    test_line = ""
    char = stdout_file.read(1).decode()

    while char in ["\r", "\n"]:
        char = stdout_file.read(1).decode()

    while char not in ["\r", "\n"]:
        test_line += char
        char = stdout_file.read(1).decode()

    return CMD_TEST in test_line


class CustomRejectPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(
        self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey
    ) -> None:
        raise HostKeyUnknownError(hostname)


class ShellParser:
    def __init__(self, stdin: list[str]) -> None:
        self._stdin = stdin

    def _get_code(self, line: str) -> tuple[int, int]:
        if len(fields := line.split("|")) != 4:
            return 0

        for item in fields:
            if item.isnumeric() and (code := int(item)) != 0:
                return code
            if item == "False":
                return 1

        return 0

    def _get_lines(self, stdout_bytes: bytes) -> list[str]:
        string = stdout_bytes.decode()
        string = WIN_TITLE.sub("", string)
        string = WIN_NEWLINE.sub("\n", string)
        string = ANSI_ESCAPE.sub("", string)
        string = string.replace("\b", "").replace("\r", "").replace("\0", "")
        return string.splitlines()

    def parse(self, stdout_bytes: bytes) -> tuple[list[str], int]:
        """Get stdout and code."""
        stdout = []
        code = stdin_count = start = end = 0

        for i, line in enumerate(lines := self._get_lines(stdout_bytes)):
            if stdin_count > len(self._stdin) - 1:
                break
            if line.endswith(self._stdin[stdin_count]) or line in [
                *self._stdin,
                ECHO_STRING,
                EXIT_STRING,
            ]:
                start = end = i + 1
            elif line.endswith(ECHO_STRING):
                end = i
            elif line.startswith((END, f'"{END}')):
                stdout.extend(lines[start:end])
                code = code or self._get_code(line)
                start = end = i + 1
                stdin_count += 1

        return stdout, code


class SSHTerminal(Terminal):
    def __init__(
        self,
        host: str,
        *,
        port: int = DEFAULT_PORT,
        username: str | None = None,
        password: str | None = None,
        key_filename: str | None = None,
        host_keys_filename: str | None = None,
        add_host_keys: bool = DEFAULT_ADD_HOST_KEYS,
        load_system_host_keys: bool = DEFAULT_LOAD_SYSTEM_HOST_KEYS,
        invoke_shell: bool = DEFAULT_INVOKE_SHELL,
        ssh_timeout: int = DEFAULT_SSH_TIMEOUT,
        ping_timeout: int = DEFAULT_PING_TIMEOUT,
        proxy_type: str | None = None,
        proxy_host: str | None = None,
        proxy_port: int | None = None,
        proxy_username: str | None = None,
        proxy_password: str | None = None,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._key_filename = key_filename
        self._host_keys_filename = host_keys_filename
        self._load_system_host_keys = load_system_host_keys
        self._invoke_shell = invoke_shell
        self._ssh_timeout = ssh_timeout
        self._ping = Ping(host, ping_timeout)
        self._proxy_type = proxy_type
        self._proxy_host = proxy_host
        self._proxy_port = proxy_port
        self._proxy_username = proxy_username
        self._proxy_password = proxy_password
        self._client = paramiko.SSHClient()
        self._client.set_log_channel("paramiko")
        self._client.set_missing_host_key_policy(
            paramiko.AutoAddPolicy if add_host_keys else CustomRejectPolicy
        )

    @property
    def host(self) -> str:
        return self._host

    def _execute_without_shell(self, string: str, timeout: int) -> CommandOutput:
        try:
            stdin, stdout, stderr = self._client.exec_command(
                string,
                timeout=float(timeout),
            )
        except Exception as exc:
            raise ExecutionError(f"Failed to execute command: {exc}") from exc

        try:
            return CommandOutput(
                string,
                time.time(),
                ["".join(line.splitlines()) for line in stdout],
                ["".join(line.splitlines()) for line in stderr],
                stdout.channel.recv_exit_status(),
            )
        except TimeoutError:
            stdin.channel.close()
            raise
        except Exception as exc:
            raise ExecutionError(f"Failed to read command output: {exc}") from exc

    def _execute_with_shell(self, string: str, timeout: int) -> CommandOutput:
        try:
            channel = self._client.invoke_shell(width=4095)
        except Exception as exc:
            raise ExecutionError(f"Failed to open channel: {exc}") from exc

        channel.settimeout(float(timeout))
        stdin_file = channel.makefile_stdin("wb")
        stdout_file = channel.makefile("r")

        try:
            cmd = _detect_cmd(stdout_file)
        except Exception as exc:
            raise ExecutionError(f"Failed to detect shell: {exc}") from exc

        try:
            for line in (stdin := string.splitlines()):
                stdin_file.write(line + "\r")
                if cmd:
                    time.sleep(1.5)
                stdin_file.write(ECHO_STRING + "\r")
            stdin_file.write(EXIT_STRING + "\r")
        except Exception as exc:
            raise ExecutionError(f"Failed to send command: {exc}") from exc

        try:
            stdout_bytes = stdout_file.read()
        except TimeoutError:
            raise
        except Exception as exc:
            raise ExecutionError(f"Failed to read command output: {exc}") from exc
        finally:
            channel.close()

        try:
            stdout, code = ShellParser(stdin).parse(stdout_bytes)
        except Exception as exc:
            raise ExecutionError(f"Failed to parse command output: {exc}") from exc

        return CommandOutput(
            string,
            time.time(),
            stdout,
            [],
            code,
        )

    def _create_proxy_socket(self) -> socks.socksocket | None:
        if not self._proxy_type or not self._proxy_host or not self._proxy_port:
            return None

        proxy_type = {
            "socks4": socks.SOCKS4,
            "socks5": socks.SOCKS5,
            "http": socks.HTTP,
        }.get(self._proxy_type.lower())

        if not proxy_type:
            return None

        sock = socks.socksocket()
        sock.set_proxy(
            proxy_type,
            self._proxy_host,
            self._proxy_port,
            username=self._proxy_username,
            password=self._proxy_password,
        )
        return sock

    def _connect(self) -> None:
        sock = self._create_proxy_socket()
        try:
            self._client.connect(
                self._host,
                self._port,
                self._username,
                self._password,
                key_filename=self._key_filename,
                timeout=self._ssh_timeout,
                allow_agent=False,
                sock=sock,
            )
        except HostKeyUnknownError:
            raise
        except paramiko.AuthenticationException as exc:
            if exc.__class__ == paramiko.AuthenticationException:
                raise AuthenticationError from exc
            raise AuthenticationError(str(exc)) from exc
        except (OSError, paramiko.SSHException) as exc:
            raise ConnectError(str(exc)) from exc
        except Exception as exc:
            raise ConnectError(str(exc)) from exc

    def _disconnect(self) -> None:
        self._client.close()

    def _execute(self, string: str, timeout: int) -> CommandOutput:
        if self._invoke_shell:
            return self._execute_with_shell(string, timeout)

        return self._execute_without_shell(string, timeout)

    def _load_host_keys(self) -> None:
        if self._load_system_host_keys:
            self._client.load_system_host_keys()
        if self._host_keys_filename:
            with Path.open(self._host_keys_filename, "a", encoding="utf-8"):
                pass
            self._client.load_host_keys(self._host_keys_filename)

    async def async_ping(self) -> None:
        await self._ping.async_ping()

    async def async_connect(self) -> None:
        await _run_in_executor("SSHConnect", self._connect)

    async def async_disconnect(self) -> None:
        await _run_in_executor("SSHDisconnect", self._disconnect)

    async def async_execute(self, string: str, timeout: int) -> CommandOutput:
        return await _run_in_executor("SSHExecute", self._execute, string, timeout)

    async def async_load_host_keys(self) -> None:
        """Load host keys."""
        return await _run_in_executor("SSHLoadHostKeys", self._load_host_keys)
