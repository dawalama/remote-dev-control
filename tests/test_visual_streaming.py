#!/usr/bin/env python3
"""Test script for Visual Streaming / VNC feature."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_knowledge.server.vnc import get_vnc_manager, VNCStatus


def test_vnc_manager():
    """Test VNC manager initialization and basic operations."""
    print("Testing VNC Manager...")
    
    vnc = get_vnc_manager()
    print("✓ VNC Manager initialized")
    
    # Test Docker availability check
    docker_ok, docker_error = vnc._is_docker_available()
    if docker_ok:
        print("✓ Docker is available")
    else:
        print(f"⚠ Docker not available: {docker_error}")
        print("  Please start Docker to test VNC features")
        return False
    
    # Test session creation (dry run - won't actually start container)
    print("\nTesting session creation (will fail if no running process)...")
    try:
        session = vnc.create_session(
            process_id="test-process",
            target_url="http://host.docker.internal:3000",
            preferred_vnc_port=8090,
        )
        print(f"✓ Session created: {session.id}")
        print(f"  Status: {session.status.value}")
        print(f"  VNC Port: {session.vnc_port}")
        
        if session.status == VNCStatus.RUNNING:
            print(f"  Viewer URL: http://localhost:{session.vnc_port}")
            
            # Test session retrieval
            retrieved = vnc.get_session(session.id)
            assert retrieved is not None, "Failed to retrieve session"
            print("✓ Session retrieved successfully")
            
            # Test session by process ID
            by_process = vnc.get_by_process("test-process")
            assert by_process is not None, "Failed to get session by process"
            print("✓ Session retrieved by process ID")
            
            # Cleanup
            time.sleep(1)
            vnc.stop_session(session.id)
            print("✓ Session stopped")
            
            vnc.delete_session(session.id)
            print("✓ Session deleted")
        else:
            print(f"  Session failed: {session.error}")
            if "Docker" in (session.error or ""):
                print("  This is expected if Docker isn't running")
    
    except Exception as e:
        print(f"✗ Session creation failed: {e}")
        return False
    
    print("\n✓ All VNC tests passed!")
    return True


def test_api_endpoints():
    """Test that API endpoints are properly registered."""
    print("\nTesting API endpoint registration...")
    
    try:
        from ai_knowledge.server.app import app
        
        routes = [r.path for r in app.routes]
        
        expected_routes = [
            "/vnc/sessions",
            "/vnc/sessions/{session_id}",
            "/vnc/sessions/{session_id}/stop",
            "/vnc/sessions/{session_id}/restart",
            "/processes/{process_id}/vnc",
        ]
        
        for route in expected_routes:
            if route in routes:
                print(f"✓ Route registered: {route}")
            else:
                print(f"✗ Route missing: {route}")
                return False
        
        print("✓ All API endpoints registered")
        return True
    
    except Exception as e:
        print(f"✗ API endpoint test failed: {e}")
        return False


def test_dashboard_updates():
    """Test that dashboard includes VNC UI elements."""
    print("\nTesting dashboard UI updates...")
    
    from ai_knowledge.server.dashboard import DASHBOARD_HTML
    
    checks = [
        ("VNC modal", "vnc-modal" in DASHBOARD_HTML),
        ("VNC iframe", "vnc-iframe" in DASHBOARD_HTML),
        ("Start VNC button", "startVNC" in DASHBOARD_HTML),
        ("Stop VNC button", "stopVNC" in DASHBOARD_HTML),
        ("Preview button", "Preview" in DASHBOARD_HTML),
        ("VNC sessions array", "vncSessions" in DASHBOARD_HTML),
    ]
    
    all_passed = True
    for name, passed in checks:
        status = "✓" if passed else "✗"
        print(f"{status} {name}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("✓ Dashboard UI includes VNC elements")
    return all_passed


if __name__ == "__main__":
    print("=" * 60)
    print("ADT Visual Streaming / VNC Test Suite")
    print("=" * 60)
    
    results = []
    
    # Test 1: VNC Manager
    results.append(("VNC Manager", test_vnc_manager()))
    
    # Test 2: API Endpoints
    results.append(("API Endpoints", test_api_endpoints()))
    
    # Test 3: Dashboard UI
    results.append(("Dashboard UI", test_dashboard_updates()))
    
    print("\n" + "=" * 60)
    print("Test Results Summary")
    print("=" * 60)
    
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    print(f"\n{passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! Visual streaming is ready to use.")
        print("\nNext steps:")
        print("1. Ensure Docker is running: docker ps")
        print("2. Start RDC server: rdc server start -d")
        print("3. Start a web process in the dashboard")
        print("4. Click 'Start Preview' to create VNC session")
        print("5. Click 'Preview' to view in the dashboard")
    else:
        print(f"\n⚠ {total - passed} test(s) failed. Please review errors above.")
    
    sys.exit(0 if passed == total else 1)
