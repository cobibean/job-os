"""Opt-in external API credential loading; never persisted into public config."""

import os
import stat
from collections.abc import Mapping


def external_mcp_token(environment: Mapping[str, str]) -> str | None:
    token = environment.get("JOBOS_EXTERNAL_MCP_TOKEN")
    filename = environment.get("JOBOS_EXTERNAL_MCP_TOKEN_FILE")
    if token and filename:
        raise ValueError("Configure only one external MCP token source")
    if filename:
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_mode & 0o077
                    or metadata.st_uid != os.getuid()
                    or metadata.st_size > 4097
                ):
                    raise ValueError("External MCP token file must be owner-only and regular")
                token = stream.read(4098).decode("ascii").rstrip("\r\n")
        except (OSError, UnicodeError) as error:
            raise ValueError("External MCP token file is unavailable or invalid") from error
    if token is not None and (not 16 <= len(token) <= 4096 or any(c.isspace() for c in token)):
        raise ValueError("External MCP token is invalid")
    return token
