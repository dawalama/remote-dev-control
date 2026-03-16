# Visual Streaming Quick Start

See running web apps directly in the RDC dashboard without opening browser tabs.

## 1. Prerequisites

Ensure Docker is running:

```bash
docker ps
```

If not running:
- **macOS**: Open Docker Desktop app
- **Linux**: `sudo systemctl start docker`

## 2. Start RDC Server

```bash
cd /Users/dawa/remote-dev-ctrl
rdc server start -d
```

Dashboard: http://127.0.0.1:8420

## 3. Start a Web Process

In the dashboard:
1. Go to **Processes** tab
2. Click **Auto-detect** (for your project)
3. Click **Start** on a web process (e.g., frontend)
4. Wait for green "running" status

## 4. Start Visual Preview

1. Click **"Start Preview"** button
2. Wait 5-10 seconds (Docker pulls image first time)
3. Click **"Preview"** button when ready
4. Browser window opens in dashboard!

## 5. Control the Browser

- **Click/Type**: Interact normally
- **Reload**: Click 🔄 button
- **Close**: Click ✕ button
- **Stop VNC**: Click "Stop VNC" button

## Architecture

```
Your Web App (localhost:3000)
    ↑
Docker Container (Chromium browser)
    ↑
RDC Dashboard (VNC viewer)
```

## Troubleshooting

### "Docker unavailable"
Start Docker Desktop

### "Container won't start"
Wait for image download on first run

### "Can't reach page"
Use `host.docker.internal` in URLs, not `localhost`

## Full Documentation

See [docs/visual-streaming.md](../docs/visual-streaming.md) for:
- API usage
- Advanced configuration
- Performance tuning
- Troubleshooting guide
