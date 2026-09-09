from starlette.testclient import TestClient
from test_remote_http import BASE, CALLBACK, app_for, authorize, exchange


def test_abandoned_registrations_do_not_block_owner_or_evict_connected_client(tmp_path):
    with TestClient(app_for(tmp_path), base_url=BASE) as client:
        connected, code = authorize(client)
        original = exchange(client, connected, code).json()
        for _ in range(105):
            result = client.post(
                "/register",
                json={
                    "redirect_uris": [CALLBACK],
                    "token_endpoint_auth_method": "client_secret_post",
                },
            )
            assert result.status_code == 201, result.text
        refresh = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": original["refresh_token"],
                "client_id": connected["client_id"],
                "client_secret": connected["client_secret"],
            },
        )
        assert refresh.status_code == 200
        new, code = authorize(client)
        assert exchange(client, new, code).status_code == 200
