"""Exercise the real FastAPI startup and shutdown lifecycle in isolation."""

from fastapi.testclient import TestClient

from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import Role, get_auth_manager


def test_server_lifecycle_and_authenticated_session_surface():
    token, _ = get_auth_manager().create_token("lifecycle-test", Role.ADMIN)
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200

        identity = client.get("/auth/me", headers=headers)
        assert identity.status_code == 200
        assert identity.json()["role"] == "admin"

        sessions = client.get("/sessions", headers=headers)
        assert sessions.status_code == 200


def test_viewer_state_socket_uses_ticket_and_rejects_control_messages():
    token, _ = get_auth_manager().create_token("lifecycle-viewer", Role.VIEWER)
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        ticket_response = client.post(
            "/auth/ws-ticket", headers=headers, json={"path": "/ws/state"}
        )
        assert ticket_response.status_code == 200
        ticket = ticket_response.json()["ticket"]

        with client.websocket_connect(f"/ws/state?ticket={ticket}") as socket:

            def receive_type(expected: str):
                for _ in range(5):
                    message = socket.receive_json()
                    if message["type"] == expected:
                        return message
                raise AssertionError(f"Did not receive {expected}")

            initial = socket.receive_json()
            assert initial["type"] == "state"
            socket.send_json(
                {"type": "register", "client_id": "viewer-test", "client_name": "Viewer"}
            )
            receive_type("registered")
            socket.send_json({"type": "client_action", "data": {"action": "restart_server"}})
            denied = receive_type("error")
            assert "state.control" in denied["data"]["message"]
