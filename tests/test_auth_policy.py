"""Authorization policy regression tests."""

from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient

from remote_dev_ctrl.server.app import app
from remote_dev_ctrl.server.auth import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    get_auth_manager,
    issue_websocket_ticket,
    validate_websocket_ticket,
)
from remote_dev_ctrl.server.middleware import get_required_permission, is_public_path

SENSITIVE_PREFIXES = (
    "/context",
    "/screenshots",
    "/vnc",
    "/terminals",
    "/processes",
    "/admin",
    "/config",
)


def test_sensitive_api_prefixes_are_not_public():
    for prefix in SENSITIVE_PREFIXES:
        assert not is_public_path(prefix)
        assert not is_public_path(f"{prefix}/resource")
    assert not is_public_path("/state")
    assert not is_public_path("/openapi.json")
    assert is_public_path("/browser/viewer")
    assert not is_public_path("/browser/sessions")
    assert not is_public_path("/browser/cdp-proxy")


def test_every_http_route_is_public_or_has_a_permission():
    uncovered = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or {"GET"}:
            if (
                not is_public_path(route.path)
                and get_required_permission(method, route.path) is None
            ):
                uncovered.append(f"{method} {route.path}")
    assert uncovered == []


def test_every_websocket_route_uses_shared_authenticator():
    # This guards the current monolithic app until sockets move into domain routers.
    missing = []
    for route in app.routes:
        if not isinstance(route, APIWebSocketRoute):
            continue
        function_source = route.endpoint.__code__
        if "authenticate_websocket" not in function_source.co_names:
            missing.append(route.path)
    assert missing == []


def test_viewer_cannot_control_host():
    viewer = ROLE_PERMISSIONS[Role.VIEWER]
    assert Permission.TERMINALS_CONTROL not in viewer
    assert Permission.PROCESSES_CONTROL not in viewer
    assert Permission.BROWSER_CONTROL not in viewer
    assert Permission.SYSTEM_CONTROL not in viewer


def test_unknown_route_fails_closed_to_admin_control():
    assert get_required_permission("POST", "/future-capability/action") == Permission.SYSTEM_CONTROL


def test_anonymous_sensitive_request_is_rejected():
    response = TestClient(app).get("/terminals")
    assert response.status_code == 401


def test_viewer_cannot_complete_session():
    token, _ = get_auth_manager().create_token("viewer-policy-test", Role.VIEWER)
    response = TestClient(app).post(
        "/sessions/not-a-session/complete",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_websocket_ticket_is_short_lived_and_path_bound():
    manager = get_auth_manager()
    _, info = manager.create_token("ticket-test", Role.OPERATOR)
    ticket = issue_websocket_ticket(info, "/ws/state")
    validated = validate_websocket_ticket(ticket, "/ws/state")
    assert validated and validated.id == info.id
    assert validate_websocket_ticket(ticket, "/ws/logs") is None
    assert validate_websocket_ticket(f"{ticket}x", "/ws/state") is None


def test_ticket_endpoint_requires_auth_and_returns_bound_ticket():
    client = TestClient(app)
    assert client.post("/auth/ws-ticket", json={"path": "/ws/state"}).status_code == 401
    token, _ = get_auth_manager().create_token("ticket-api-test", Role.VIEWER)
    response = client.post(
        "/auth/ws-ticket",
        headers={"Authorization": f"Bearer {token}"},
        json={"path": "/ws/state"},
    )
    assert response.status_code == 200
    assert validate_websocket_ticket(response.json()["ticket"], "/ws/state")
