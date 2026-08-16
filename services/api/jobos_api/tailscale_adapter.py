from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jobos_api.macos_keychain import (
    delete_keychain_secret,
    read_keychain_secret,
    store_keychain_secret,
)

_DEVICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_DNS_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


@dataclass(frozen=True)
class TailscaleNode:
    dns_name: str
    addresses: tuple[str, ...]


def parse_tailscale_status(payload: str) -> TailscaleNode:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError("Tailscale status is invalid") from error
    if not isinstance(value, dict) or value.get("BackendState") != "Running":
        raise ValueError("Tailscale is not running")
    node = value.get("Self")
    if not isinstance(node, dict):
        raise ValueError("Tailscale DNS identity is unavailable")
    dns_name = node.get("DNSName")
    addresses = node.get("TailscaleIPs")
    if not isinstance(dns_name, str):
        raise ValueError("Tailscale DNS identity is unavailable")
    dns_name = dns_name.rstrip(".")
    if not _DNS_PATTERN.fullmatch(dns_name):
        raise ValueError("Tailscale DNS identity is invalid")
    if (
        not isinstance(addresses, list)
        or not addresses
        or any(not isinstance(address, str) or not address for address in addresses)
    ):
        raise ValueError("Tailscale addresses are unavailable")
    return TailscaleNode(dns_name=dns_name, addresses=tuple(addresses))


def _validate_port(value: int, *, private_https: bool = False) -> int:
    minimum = 1024 if private_https else 1
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= 65535:
        raise ValueError("Tailscale port is invalid")
    return value


def build_serve_command(
    binary: str,
    *,
    https_port: int,
    api_port: int,
) -> list[str]:
    if not binary.startswith("/") or "\0" in binary:
        raise ValueError("Tailscale binary path is invalid")
    _validate_port(https_port, private_https=True)
    _validate_port(api_port)
    return [
        binary,
        "serve",
        "--bg",
        "--yes",
        f"--https={https_port}",
        f"http://127.0.0.1:{api_port}",
    ]


def build_serve_remove_command(binary: str, *, https_port: int) -> list[str]:
    if not binary.startswith("/") or "\0" in binary:
        raise ValueError("Tailscale binary path is invalid")
    _validate_port(https_port, private_https=True)
    return [binary, "serve", f"--https={https_port}", "off"]


def _selected_port_proxy(payload: str, https_port: int) -> str | None:
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError("Tailscale Serve status is invalid") from error
    if not isinstance(value, dict):
        raise ValueError("Tailscale Serve status is invalid")
    tcp = value.get("TCP", {})
    web = value.get("Web", {})
    if not isinstance(tcp, dict) or not isinstance(web, dict):
        raise ValueError("Tailscale Serve status is invalid")
    if str(https_port) not in tcp:
        return None
    proxies = []
    for address, route in web.items():
        if not isinstance(address, str) or not address.endswith(f":{https_port}"):
            continue
        handlers = route.get("Handlers") if isinstance(route, dict) else None
        root = handlers.get("/") if isinstance(handlers, dict) else None
        proxy = root.get("Proxy") if isinstance(root, dict) else None
        if not isinstance(proxy, str):
            raise ValueError("selected Tailscale Serve port cannot be restored safely")
        proxies.append(proxy)
    if len(proxies) != 1:
        raise ValueError("selected Tailscale Serve port cannot be restored safely")
    return proxies[0]


def _run_tailscale(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Tailscale operation failed") from error
    return result.stdout


def configure_jobos_serve(
    binary: str,
    *,
    https_port: int,
    api_port: int,
    run: Callable[[list[str]], str] = _run_tailscale,
) -> str:
    node = parse_tailscale_status(run([binary, "status", "--json"]))
    previous_status = run([binary, "serve", "status", "--json"])
    previous_proxy = _selected_port_proxy(previous_status, https_port)
    run(build_serve_command(binary, https_port=https_port, api_port=api_port))
    try:
        status = run([binary, "serve", "status", "--json"])
        if not verify_jobos_serve_status(
            status,
            https_port=https_port,
            api_port=api_port,
        ):
            raise RuntimeError("Tailscale Serve did not expose the expected JobOS route")
    except Exception:
        if previous_proxy is None:
            run(build_serve_remove_command(binary, https_port=https_port))
        else:
            run(
                [
                    binary,
                    "serve",
                    "--bg",
                    "--yes",
                    f"--https={https_port}",
                    previous_proxy,
                ]
            )
        raise
    return f"https://{node.dns_name}:{https_port}"


def build_remote_desktop_runtime(
    node: TailscaleNode,
    *,
    https_port: int,
    device_id: str,
) -> dict[str, object]:
    _validate_port(https_port, private_https=True)
    if not _DEVICE_PATTERN.fullmatch(device_id):
        raise ValueError("remote device identifier is invalid")
    if not _DNS_PATTERN.fullmatch(node.dns_name):
        raise ValueError("Tailscale DNS identity is invalid")
    return {
        "schemaVersion": 1,
        "mode": "remote-client",
        "apiBaseUrl": f"https://{node.dns_name}:{https_port}",
        "deviceId": device_id,
    }


def verify_jobos_serve_status(payload: str, *, https_port: int, api_port: int) -> bool:
    _validate_port(https_port, private_https=True)
    _validate_port(api_port)
    try:
        value = json.loads(payload)
    except json.JSONDecodeError:
        return False
    if not isinstance(value, dict):
        return False
    tcp = value.get("TCP")
    web = value.get("Web")
    if not isinstance(tcp, dict) or not isinstance(web, dict):
        return False
    listener = tcp.get(str(https_port))
    if not isinstance(listener, dict) or listener.get("HTTPS") is not True:
        return False
    expected_proxy = f"http://127.0.0.1:{api_port}"
    matching_routes = 0
    for address, route in web.items():
        if not isinstance(address, str) or not address.endswith(f":{https_port}"):
            continue
        if not isinstance(route, dict):
            return False
        handlers = route.get("Handlers")
        root = handlers.get("/") if isinstance(handlers, dict) else None
        if not isinstance(root, dict) or root.get("Proxy") != expected_proxy:
            return False
        matching_routes += 1
    return matching_routes == 1


def provision_remote_client(
    node: TailscaleNode,
    *,
    home: Path,
    https_port: int,
    device_id: str,
    device_token: str,
    store_secret: Callable[[str, str, str], None] = store_keychain_secret,
    read_secret: Callable[[str, str], str | None] = read_keychain_secret,
    delete_secret: Callable[[str, str], None] = delete_keychain_secret,
) -> Path:
    if not 16 <= len(device_token) <= 4096 or any(char in device_token for char in "\r\n\0"):
        raise ValueError("remote device credential is invalid")
    runtime = build_remote_desktop_runtime(
        node,
        https_port=https_port,
        device_id=device_id,
    )
    path = home / "Library/Application Support/JobOS/runtime.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    previous_config = path.read_bytes() if path.exists() else None
    previous_secret = read_secret(
        "com.cobibean.jobos.device-token",
        device_id,
    )
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(runtime, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        store_secret("com.cobibean.jobos.device-token", device_id, device_token)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        if previous_secret is None:
            delete_secret("com.cobibean.jobos.device-token", device_id)
        else:
            store_secret(
                "com.cobibean.jobos.device-token",
                device_id,
                previous_secret,
            )
        if previous_config is None:
            path.unlink(missing_ok=True)
        else:
            restore = path.with_name(f".{path.name}.{os.getpid()}.restore")
            restore.write_bytes(previous_config)
            restore.chmod(0o600)
            os.replace(restore, path)
        raise RuntimeError("JobOS remote provisioning rolled back") from None
    finally:
        temporary.unlink(missing_ok=True)
    return path


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JobOS private Tailscale adapter")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser(
        "configure",
        help="add and verify the private JobOS Serve route",
    )
    configure.add_argument("--binary", required=True)
    configure.add_argument("--https-port", type=int, required=True)
    configure.add_argument("--api-port", type=int, required=True)

    status = subparsers.add_parser("status", help="verify the private JobOS Serve route")
    status.add_argument("--binary", required=True)
    status.add_argument("--https-port", type=int, required=True)
    status.add_argument("--api-port", type=int, required=True)

    remove = subparsers.add_parser("remove", help="remove only the selected Serve port")
    remove.add_argument("--binary", required=True)
    remove.add_argument("--https-port", type=int, required=True)

    provision = subparsers.add_parser(
        "provision-remote",
        help="write remote desktop config and store its device token",
    )
    provision.add_argument("--binary", required=True)
    provision.add_argument("--https-port", type=int, required=True)
    provision.add_argument("--device-id", required=True)
    provision.add_argument("--home", type=Path, default=Path.home())
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(arguments if arguments is not None else sys.argv[1:])
    try:
        if options.command == "configure":
            endpoint = configure_jobos_serve(
                options.binary,
                https_port=options.https_port,
                api_port=options.api_port,
            )
            print(f"JobOS private endpoint configured: {endpoint}")
        elif options.command == "status":
            status = _run_tailscale([options.binary, "serve", "status", "--json"])
            if not verify_jobos_serve_status(
                status,
                https_port=options.https_port,
                api_port=options.api_port,
            ):
                raise RuntimeError("JobOS private Serve route is not configured")
            print("JobOS private Serve route verified")
        elif options.command == "remove":
            _run_tailscale(
                build_serve_remove_command(
                    options.binary,
                    https_port=options.https_port,
                )
            )
            print("JobOS private Serve route removed")
        elif options.command == "provision-remote":
            device_token = os.environ.get("JOBOS_DEVICE_TOKEN", "")
            if not device_token:
                raise RuntimeError("JOBOS_DEVICE_TOKEN is required for provisioning")
            node = parse_tailscale_status(_run_tailscale([options.binary, "status", "--json"]))
            path = provision_remote_client(
                node,
                home=options.home,
                https_port=options.https_port,
                device_id=options.device_id,
                device_token=device_token,
            )
            print(f"JobOS remote client configured: {path}")
    except (RuntimeError, ValueError) as error:
        print(f"JobOS Tailscale setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
