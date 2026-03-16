# Visual Streaming (VNC Preview)

RDC provides visual streaming for web applications using **neko** (browser-in-docker) with VNC/WebRTC support. This allows you to preview running web apps directly in the dashboard without opening multiple browser tabs.

## Overview

Visual streaming creates isolated browser sessions that automatically navigate to your dev servers. You can:

- **Preview UIs** directly in the dashboard
- **Control the browser** (click, type, scroll)
- **Share sessions** with team members
- **Record interactions** for debugging

## Architecture

```
RDC Dashboard (Port 8420)
    ↓
VNC Manager API
    ↓
Docker Container (m1k1o/neko:chromium)
    ↓
Your Web App (e.g., localhost:3000)
```

### Components

1. **VNC Manager** (`vnc.py`) - Manages Docker containers
2. **API Endpoints** - Create/control VNC sessions
3. **Dashboard UI** - Embedded VNC viewer
4. **Neko Container** - Chromium browser with VNC server

## Prerequisites

### Docker

Visual streaming requires Docker to be installed and running:

```bash
# Check Docker installation
docker --version

# Start Docker (if not running)
open -a Docker  # macOS
# or
systemctl start docker  # Linux

# Verify Docker is running
docker ps
```

### Neko Image

The first time you start a VNC session, Docker will pull the appropriate image (~500MB-1GB):

**For Apple Silicon (ARM64)**:
```bash
# Pre-pull the image (optional)
docker pull kasmweb/chrome:1.14.0-rolling
```

**For Intel/AMD (x86_64)**:
```bash
# Pre-pull the image (optional)
docker pull m1k1o/neko:chromium
```

RDC automatically detects your platform and uses the correct image.

## Usage

### From Dashboard

1. **Start a web process** (e.g., frontend, backend)
2. Wait for it to be **running** (green status)
3. Click **"Start Preview"** button
4. Wait 2-3 seconds for container to start
5. Click **"Preview"** to open VNC viewer

### API Usage

#### Create VNC Session

```bash
# Auto-detect URL from process port
curl -X POST "http://localhost:8420/vnc/sessions?process_id=documaker-frontend" \
  -H "Authorization: Bearer $RDC_TOKEN"

# Specify custom URL
curl -X POST "http://localhost:8420/vnc/sessions?process_id=myapp-web&target_url=http://host.docker.internal:3000" \
  -H "Authorization: Bearer $RDC_TOKEN"
```

Response:
```json
{
  "success": true,
  "session": {
    "id": "vnc-documaker-frontend",
    "process_id": "documaker-frontend",
    "target_url": "http://host.docker.internal:3000",
    "vnc_port": 8090,
    "viewer_url": "http://localhost:8090",
    "status": "running"
  }
}
```

#### List Sessions

```bash
curl "http://localhost:8420/vnc/sessions" \
  -H "Authorization: Bearer $RDC_TOKEN"
```

#### Stop Session

```bash
curl -X POST "http://localhost:8420/vnc/sessions/vnc-documaker-frontend/stop" \
  -H "Authorization: Bearer $RDC_TOKEN"
```

### Python API

```python
from remote_dev_ctrl.server.vnc import get_vnc_manager

vnc = get_vnc_manager()

# Create session
session = vnc.create_session(
    process_id="myapp-frontend",
    target_url="http://localhost:3000",
)

print(f"VNC viewer: http://localhost:{session.vnc_port}")

# Stop when done
vnc.stop_session(session.id)
```

## Configuration

### Container Settings

The browser container is configured with:

- **Screen Resolution**: 1280x720 @ 30fps
- **Password**: `neko` (default)
- **Browser**: Chromium (lightweight)
- **Memory**: 2GB shared memory
- **Network**: Host-accessible via `host.docker.internal`
- **Platform**: Auto-detected (ARM64 uses KasmWeb, x86_64 uses Neko)

### Port Allocation

VNC sessions use auto-assigned ports starting from **8090**:

- Port `8090-8189`: VNC viewer web interface (6901 in container)
- Additional ports handled internally by the container

### Security

- **Authentication**: Neko requires password (default: `neko`)
- **Isolation**: Each session runs in its own container
- **Network**: Containers only access your local dev servers

## Troubleshooting

### Docker Not Running

**Error**: `Docker unavailable: Docker daemon not responding`

**Solution**:
```bash
# macOS
open -a Docker

# Linux
sudo systemctl start docker

# Verify
docker ps
```

### Container Won't Start

**Error**: `Failed to start container: permission denied`

**Solution**:
```bash
# Ensure Docker has necessary permissions
docker run --rm hello-world

# If fails, reinstall Docker or check system settings
```

### Port Already in Use

**Error**: Bind for 0.0.0.0:8090 failed: port is already allocated

**Solution**: The VNC manager will auto-assign a different port. Check the session details for the actual port.

### Browser Not Loading

**Issue**: VNC viewer shows "connection failed"

**Solutions**:
1. Wait 5-10 seconds for container to fully start
2. Check if your web app is actually running
3. Use `host.docker.internal` instead of `localhost` in URLs
4. Check Docker logs: `docker logs rdc-vnc-{process_id}`

### URL Not Working in Container

**Issue**: Browser shows "can't reach this page"

**Solution**: Use `host.docker.internal` instead of `localhost`:
```bash
# ✗ Won't work from container
http://localhost:3000

# ✓ Works from container
http://host.docker.internal:3000
```

## Advanced Usage

### Custom Resolution

Modify `vnc.py` to change screen resolution:

```python
"-e", "NEKO_SCREEN=1920x1080@60",  # Full HD at 60fps
```

### Auto-Start VNC

To automatically start VNC when a web process starts, modify `processes.py`:

```python
# In ProcessManager.start() method
if state.port and state.port < 10000:
    from .vnc import get_vnc_manager
    vnc_manager = get_vnc_manager()
    vnc_manager.create_session(
        process_id=process_id,
        target_url=f"http://host.docker.internal:{state.port}"
    )
```

### Recording Sessions

Neko supports recording browser sessions:

```python
# Add to container args in vnc.py
"-e", "NEKO_BROADCAST_URL=rtmp://your-server/live",
```

### Multiple Browsers

Use different neko images:

```python
# Firefox
"m1k1o/neko:firefox"

# Chrome
"m1k1o/neko:google-chrome"

# Edge
"m1k1o/neko:microsoft-edge"
```

## Performance

### Resource Usage

Each VNC session consumes:
- **CPU**: ~5-10% idle, ~20-40% active
- **Memory**: ~200-400MB per container
- **Disk**: ~500MB for image (shared)

### Limits

Recommended limits:
- **Max sessions**: 5-10 concurrent (depending on hardware)
- **Resolution**: 1280x720 for better performance
- **Framerate**: 30fps default, 60fps for smooth interactions

### Optimization

1. **Stop unused sessions** - Free up resources
2. **Lower resolution** - Faster rendering
3. **Use Chromium** - Lighter than Chrome/Firefox
4. **Cleanup regularly** - Remove stopped sessions

```bash
# Cleanup all stopped sessions
curl -X POST "http://localhost:8420/vnc/cleanup" \
  -H "Authorization: Bearer $RDC_TOKEN"
```

## Integration Examples

### With Process Manager

```python
from remote_dev_ctrl.server.processes import get_process_manager
from remote_dev_ctrl.server.vnc import get_vnc_manager

pm = get_process_manager()
vnc = get_vnc_manager()

# Start process
pm.start("documaker-frontend")

# Wait for process to be ready (poll until port is accessible)
import time
time.sleep(3)

# Start VNC
session = vnc.create_session(
    process_id="documaker-frontend",
    target_url="http://host.docker.internal:3000"
)

print(f"Preview at: http://localhost:{session.vnc_port}")
```

### With Task Queue

Create tasks that automatically show previews:

```python
task = task_repo.create(
    project="myapp",
    description="Fix the login button styling",
    metadata={
        "auto_preview": True,
        "preview_url": "http://localhost:3000/login"
    }
)

# When task starts, auto-create VNC session
# (implement in orchestrator.py)
```

## Comparison with Alternatives

| Feature | RDC VNC | DevTools | ngrok |
|---------|---------|----------|-------|
| Local preview | ✓ | ✓ | ✗ |
| Shareable | ✓ | ✗ | ✓ |
| Isolated browser | ✓ | ✗ | ✗ |
| No browser tabs | ✓ | ✗ | ✗ |
| Recording | ✓ | ✓ | ✗ |
| Mobile simulation | ✓ | ✓ | ✗ |

## Future Enhancements

Planned features:

- [ ] Auto-start VNC for web processes
- [ ] Mobile device emulation
- [ ] Session recording/replay
- [ ] Multiple viewers per session
- [ ] Touch gesture support
- [ ] Clipboard sync
- [ ] File upload/download
- [ ] Performance metrics overlay

## References

- [Neko GitHub](https://github.com/m1k1o/neko)
- [Docker Documentation](https://docs.docker.com/)
- [WebRTC Protocol](https://webrtc.org/)
- [VNC Protocol](https://en.wikipedia.org/wiki/Virtual_Network_Computing)
