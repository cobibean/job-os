import json

import pytest
from jobos_api.tailscale_adapter import (
    TailscaleNode,
    build_remote_desktop_runtime,
    build_serve_command,
    build_serve_remove_command,
    configure_jobos_serve,
    parse_arguments,
    parse_tailscale_status,
    provision_remote_client,
    verify_jobos_serve_status,
)


def test_tailscale_status_requires_a_running_node_with_magic_dns():
    node = parse_tailscale_status(
        json.dumps(
            {
                "BackendState": "Running",
                "Self": {
                    "DNSName": "mini.private-tailnet.example.",
                    "TailscaleIPs": ["100.64.0.10", "fd7a:115c:a1e0::1"],
                },
            }
        )
    )

    assert node == TailscaleNode(
        dns_name="mini.private-tailnet.example",
        addresses=("100.64.0.10", "fd7a:115c:a1e0::1"),
    )
    with pytest.raises(ValueError, match="not running"):
        parse_tailscale_status('{"BackendState":"Stopped","Self":{}}')
    with pytest.raises(ValueError, match="DNS"):
        parse_tailscale_status('{"BackendState":"Running","Self":{"TailscaleIPs":[]}}')


def test_serve_command_is_private_additive_and_loopback_only():
    command = build_serve_command(
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        https_port=10448,
        api_port=8766,
    )

    assert command == [
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        "serve",
        "--bg",
        "--yes",
        "--https=10448",
        "http://127.0.0.1:8766",
    ]
    assert "funnel" not in " ".join(command).lower()
    assert "0.0.0.0" not in " ".join(command)
    with pytest.raises(ValueError, match="port"):
        build_serve_command("/tailscale", https_port=443, api_port=8766)


def test_remote_runtime_uses_discovered_private_https_endpoint_without_secrets():
    runtime = build_remote_desktop_runtime(
        TailscaleNode("mini.private-tailnet.example", ("100.64.0.10",)),
        https_port=10448,
        device_id="macbook-device",
    )

    assert runtime == {
        "schemaVersion": 1,
        "mode": "remote-client",
        "apiBaseUrl": "https://mini.private-tailnet.example:10448",
        "deviceId": "macbook-device",
    }
    assert "token" not in json.dumps(runtime).lower()


def test_serve_status_must_match_exact_jobos_port_and_loopback_backend():
    payload = json.dumps(
        {
            "TCP": {"10448": {"HTTPS": True}},
            "Web": {
                "mini.private-tailnet.example:10448": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8766"}}
                },
                "mini.private-tailnet.example:9443": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8788"}}
                },
            },
        }
    )

    assert verify_jobos_serve_status(payload, https_port=10448, api_port=8766) is True
    assert verify_jobos_serve_status(payload, https_port=10448, api_port=9999) is False
    unsafe = payload.replace("127.0.0.1:8766", "0.0.0.0:8766")
    assert verify_jobos_serve_status(unsafe, https_port=10448, api_port=8766) is False


def test_remote_provisioning_writes_only_non_secret_config_and_keychain_credential(tmp_path):
    keychain_writes = []
    config_path = provision_remote_client(
        TailscaleNode("mini.private-tailnet.example", ("100.64.0.10",)),
        home=tmp_path,
        https_port=10448,
        device_id="macbook-device",
        device_token="remote-device-secret",
        store_secret=lambda service, account, secret: keychain_writes.append(
            (service, account, secret)
        ),
        read_secret=lambda _service, _account: None,
        delete_secret=lambda _service, _account: None,
    )

    assert config_path == tmp_path / "Library/Application Support/JobOS/runtime.json"
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert "remote-device-secret" not in config_path.read_text(encoding="utf-8")
    assert keychain_writes == [
        (
            "com.cobibean.jobos.device-token",
            "macbook-device",
            "remote-device-secret",
        )
    ]


def test_configure_serve_discovers_identity_applies_one_route_and_verifies_it():
    binary = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    node_status = json.dumps(
        {
            "BackendState": "Running",
            "Self": {
                "DNSName": "mini.private-tailnet.example.",
                "TailscaleIPs": ["100.64.0.10"],
            },
        }
    )
    serve_status = json.dumps(
        {
            "TCP": {"10448": {"HTTPS": True}},
            "Web": {
                "mini.private-tailnet.example:10448": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8766"}}
                }
            },
        }
    )
    empty_status = json.dumps({"TCP": {}, "Web": {}})
    outputs = [node_status, empty_status, "", serve_status]
    commands = []

    endpoint = configure_jobos_serve(
        binary,
        https_port=10448,
        api_port=8766,
        run=lambda command: (commands.append(command), outputs.pop(0))[1],
    )

    assert endpoint == "https://mini.private-tailnet.example:10448"
    assert commands == [
        [binary, "status", "--json"],
        [binary, "serve", "status", "--json"],
        build_serve_command(binary, https_port=10448, api_port=8766),
        [binary, "serve", "status", "--json"],
    ]


def test_failed_serve_verification_rolls_back_only_selected_port():
    binary = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    node_status = json.dumps(
        {
            "BackendState": "Running",
            "Self": {
                "DNSName": "mini.private-tailnet.example.",
                "TailscaleIPs": ["100.64.0.10"],
            },
        }
    )
    prior_status = json.dumps(
        {
            "TCP": {"9443": {"HTTPS": True}},
            "Web": {
                "mini.private-tailnet.example:9443": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8788"}}
                }
            },
        }
    )
    outputs = [node_status, prior_status, "", prior_status, ""]
    commands = []

    with pytest.raises(RuntimeError, match="expected"):
        configure_jobos_serve(
            binary,
            https_port=10448,
            api_port=8766,
            run=lambda command: (commands.append(command), outputs.pop(0))[1],
        )

    assert commands[-1] == build_serve_remove_command(binary, https_port=10448)
    assert all("reset" not in " ".join(command).lower() for command in commands)
    assert all("funnel" not in " ".join(command).lower() for command in commands)


def test_tailscale_cli_keeps_machine_specific_values_in_arguments(tmp_path):
    options = parse_arguments(
        [
            "provision-remote",
            "--binary",
            "/opt/tailscale",
            "--https-port",
            "10448",
            "--device-id",
            "macbook-device",
            "--home",
            str(tmp_path),
        ]
    )

    assert options.command == "provision-remote"
    assert options.binary == "/opt/tailscale"
    assert options.https_port == 10448
    assert options.device_id == "macbook-device"
    assert options.home == tmp_path
