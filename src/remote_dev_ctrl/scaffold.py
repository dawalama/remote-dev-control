"""Project scaffolding with templates."""

import logging
import subprocess
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def create_project(
    path: Path,
    name: str,
    config: dict,
    register: bool = True,
) -> dict:
    """Create a new project with the specified configuration."""

    path.mkdir(parents=True, exist_ok=True)

    created_files = []

    # Always create .ai directory
    ai_dir = path / ".ai"
    ai_dir.mkdir(exist_ok=True)

    # Create AI configuration files
    created_files.extend(create_ai_files(ai_dir, name, config))

    # Create .gitignore
    created_files.append(create_gitignore(path, config))

    # Create README
    created_files.append(create_readme(path, name, config))

    # Create stack-specific files
    proj_type = config.get("type", "backend")
    stack = config.get("stack", {})

    if proj_type in ("backend", "fullstack"):
        backend_stack = stack.get("backend", "fastapi")
        if backend_stack == "fastapi":
            created_files.extend(create_fastapi_backend(path, name, config))
        elif backend_stack == "express":
            created_files.extend(create_express_backend(path, name, config))

    if proj_type in ("frontend", "fullstack"):
        frontend_stack = stack.get("frontend", "react")
        if frontend_stack == "react":
            created_files.extend(create_react_frontend(path, name, config))

    # Create deployment files
    deployment = config.get("deployment", "docker")
    if deployment == "docker":
        created_files.extend(create_docker_files(path, name, config))
    elif deployment == "render":
        created_files.append(create_render_yaml(path, name, config))

    # Create GitHub workflows
    created_files.extend(create_github_workflows(path, name, config))

    # Create rdc.yaml with dev process definitions
    created_files.append(create_rdc_yaml(path, name, config))

    # Initialize git repo and make initial commit
    _init_git(path)

    # Install dependencies (best-effort, non-blocking)
    _install_deps(path, config)

    return {
        "path": str(path),
        "name": name,
        "config": config,
        "created_files": created_files,
    }


def create_rdc_yaml(path: Path, name: str, config: dict) -> str:
    """Create rdc.yml with dev process definitions based on the stack."""
    proj_type = config.get("type", "backend")
    stack = config.get("stack", {})
    backend = stack.get("backend", "fastapi")
    frontend = stack.get("frontend", "")
    processes = []

    if proj_type in ("backend", "fullstack"):
        if backend == "fastapi":
            processes.append({
                "name": "server",
                "command": "uv run uvicorn app.main:app --reload --port 8000",
                "description": f"{name} API server",
                "port": 8000,
            })
        elif backend == "express":
            processes.append({
                "name": "server",
                "command": "pnpm dev",
                "description": f"{name} API server",
                "port": 3000,
            })

    if proj_type in ("frontend", "fullstack"):
        if proj_type == "fullstack":
            processes.append({
                "name": "frontend",
                "command": "pnpm dev",
                "description": f"{name} frontend dev server",
                "port": 5173,
                "cwd": "frontend",
            })
        else:
            processes.append({
                "name": "dev",
                "command": "pnpm dev",
                "description": f"{name} dev server",
                "port": 5173,
            })

    # Build YAML by hand to keep it clean (no PyYAML dependency needed)
    lines = [f"# RDC process configuration for {name}", "processes:"]
    for proc in processes:
        lines.append(f"  - name: {proc['name']}")
        lines.append(f"    command: {proc['command']}")
        lines.append(f"    description: {proc['description']}")
        if proc.get("port"):
            lines.append(f"    port: {proc['port']}")
        if proc.get("cwd"):
            lines.append(f"    cwd: {proc['cwd']}")

    (path / "rdc.yaml").write_text("\n".join(lines) + "\n")
    return "rdc.yaml"


def _init_git(path: Path):
    """Initialize a git repo and make an initial commit."""
    try:
        subprocess.run(["git", "init"], cwd=path, capture_output=True, timeout=10)
        subprocess.run(["git", "add", "."], cwd=path, capture_output=True, timeout=10)
        subprocess.run(
            ["git", "commit", "-m", "Initial scaffold"],
            cwd=path, capture_output=True, timeout=10,
        )
        logger.info("Git repo initialized at %s", path)
    except Exception:
        logger.debug("Git init failed for %s", path, exc_info=True)


def _install_deps(path: Path, config: dict):
    """Best-effort dependency installation."""
    proj_type = config.get("type", "backend")
    stack = config.get("stack", {})
    backend = stack.get("backend", "fastapi")

    try:
        if proj_type in ("backend", "fullstack") and backend in ("fastapi", "django"):
            subprocess.run(["uv", "sync"], cwd=path, capture_output=True, timeout=120)
            logger.info("Python deps installed for %s", path)
        if proj_type in ("backend",) and backend == "express":
            subprocess.run(["pnpm", "install"], cwd=path, capture_output=True, timeout=120)
            logger.info("Node deps installed for %s", path)
        if proj_type in ("frontend", "fullstack"):
            fe_dir = path / "frontend" if proj_type == "fullstack" else path
            subprocess.run(["pnpm", "install"], cwd=fe_dir, capture_output=True, timeout=120)
            logger.info("Frontend deps installed for %s", fe_dir)
    except Exception:
        logger.debug("Dep install failed for %s", path, exc_info=True)


def create_ai_files(ai_dir: Path, name: str, config: dict) -> list[str]:
    """Create .ai directory files."""
    files = []
    
    # rules.md
    rules_content = f"""# {name} Rules

> Project-specific rules and patterns for AI assistants.

## Stack

- **Type:** {config.get('type', 'backend')}
- **Backend:** {config.get('stack', {}).get('backend', 'N/A')}
- **Frontend:** {config.get('stack', {}).get('frontend', 'N/A')}
- **Database:** {config.get('database', 'N/A')}
- **Deployment:** {config.get('deployment', 'N/A')}

## Conventions

- Follow the patterns established in this codebase
- Use existing utilities before creating new ones
- Check `.ai/learnings.md` for past corrections

## Key Directories

<!-- Fill in your project structure -->

## Common Tasks

<!-- Document common development tasks -->
"""
    (ai_dir / "rules.md").write_text(rules_content)
    files.append(".ai/rules.md")
    
    # learnings.md
    learnings_content = f"""# {name} Learnings

> Project-specific corrections and lessons learned.

<!-- New entries are added below this line -->

---

*No entries yet.*
"""
    (ai_dir / "learnings.md").write_text(learnings_content)
    files.append(".ai/learnings.md")
    
    # context.md
    context_content = f"""# {name} Context

> Quick reference for AI assistants.

## Overview

{config.get('description', 'A new project scaffolded with rdc.')}

## Project Type

{config.get('type', 'backend').title()} application.

## Features

{chr(10).join(f'- {f}' for f in config.get('features', [])) or '- Core functionality'}

## Getting Started

```bash
# Install dependencies
{_get_install_command(config)}

# Run development server
{_get_dev_command(config)}
```

## Key Files

<!-- Document important files and their purpose -->
"""
    (ai_dir / "context.md").write_text(context_content)
    files.append(".ai/context.md")
    
    return files


def _get_install_command(config: dict) -> str:
    proj_type = config.get("type", "backend")
    stack = config.get("stack", {})
    
    if proj_type == "fullstack":
        return "uv sync && cd frontend && pnpm install"
    elif proj_type == "frontend":
        return "pnpm install"
    elif stack.get("backend") == "express":
        return "pnpm install"
    else:
        return "uv sync"


def _get_dev_command(config: dict) -> str:
    proj_type = config.get("type", "backend")
    stack = config.get("stack", {})
    
    if proj_type == "fullstack":
        return "make dev  # or run backend and frontend separately"
    elif proj_type == "frontend":
        return "pnpm dev"
    elif stack.get("backend") == "express":
        return "pnpm dev"
    else:
        return "uv run uvicorn app.main:app --reload"


def create_gitignore(path: Path, config: dict) -> str:
    """Create .gitignore file."""
    content = """# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
.venv/
venv/
ENV/
.eggs/
*.egg-info/
dist/
build/

# Node
node_modules/
npm-debug.log*
.npm

# Environment
.env
.env.local
.env.*.local
*.local

# IDE
.idea/
.vscode/
*.swp
*.swo
.DS_Store

# Testing
.coverage
htmlcov/
.pytest_cache/
.tox/

# Build
*.log
tmp/
temp/

# Local
local_tmp/
"""
    (path / ".gitignore").write_text(content)
    return ".gitignore"


def create_readme(path: Path, name: str, config: dict) -> str:
    """Create README.md file."""
    proj_type = config.get("type", "backend")
    
    content = f"""# {name}

{config.get('description', 'A new project.')}

## Stack

| Component | Technology |
|-----------|------------|
| Type | {proj_type.title()} |
| Backend | {config.get('stack', {}).get('backend', 'N/A')} |
| Frontend | {config.get('stack', {}).get('frontend', 'N/A')} |
| Database | {config.get('database', 'N/A')} |
| Deployment | {config.get('deployment', 'N/A')} |

## Getting Started

### Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/) (for backend)
- Node.js 20+ and [pnpm](https://pnpm.io/) (for frontend)
- Docker (for containerized deployment)

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd {name}

# Install dependencies
{_get_install_command(config)}

# Set up environment
cp .env.example .env
# Edit .env with your configuration

# Run development server
{_get_dev_command(config)}
```

## Project Structure

```
{name}/
├── .ai/                # AI assistant configuration
│   ├── rules.md        # Project-specific rules
│   ├── learnings.md    # Corrections and lessons
│   └── context.md      # Quick reference
├── .github/            # GitHub workflows
└── ...                 # Application code
```

## Development

### Using AI Assistance

This project is configured for AI-assisted development with `rdc`:

```bash
# Run code review
rdc run skill review

# Find technical debt
rdc run skill techdebt

# Generate commit message
rdc run skill commit
```

## License

MIT
"""
    (path / "README.md").write_text(content)
    return "README.md"


def create_fastapi_backend(path: Path, name: str, config: dict) -> list[str]:
    """Create FastAPI backend structure."""
    files = []
    
    # Create directories
    app_dir = path / "app"
    app_dir.mkdir(exist_ok=True)
    (app_dir / "api").mkdir(exist_ok=True)
    (app_dir / "core").mkdir(exist_ok=True)
    (app_dir / "models").mkdir(exist_ok=True)
    (app_dir / "services").mkdir(exist_ok=True)
    
    # pyproject.toml
    pyproject = f'''[project]
name = "{name}"
version = "0.1.0"
description = "{config.get('description', 'A FastAPI application')}"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
'''
    
    if config.get("database") == "postgres":
        pyproject += '''    "sqlalchemy>=2.0.0",
    "asyncpg>=0.29.0",
    "alembic>=1.13.0",
'''
    
    if "auth" in config.get("features", []):
        pyproject += '''    "python-jose[cryptography]>=3.3.0",
    "passlib[bcrypt]>=1.7.0",
'''
    
    pyproject += ''']

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.26.0",
    "ruff>=0.3.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]
'''
    (path / "pyproject.toml").write_text(pyproject)
    files.append("pyproject.toml")
    
    # app/__init__.py
    (app_dir / "__init__.py").write_text("")
    files.append("app/__init__.py")
    
    # app/main.py
    main_py = '''"""Main FastAPI application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api import router

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
'''
    (app_dir / "main.py").write_text(main_py)
    files.append("app/main.py")
    
    # app/core/__init__.py
    (app_dir / "core" / "__init__.py").write_text("")
    files.append("app/core/__init__.py")
    
    # app/core/config.py
    config_py = '''"""Application configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "API"
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    
    # Database
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/db"
    
    class Config:
        env_file = ".env"


settings = Settings()
'''
    (app_dir / "core" / "config.py").write_text(config_py)
    files.append("app/core/config.py")
    
    # app/api/__init__.py
    api_init = '''"""API routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def root():
    return {"message": "API is running"}
'''
    (app_dir / "api" / "__init__.py").write_text(api_init)
    files.append("app/api/__init__.py")
    
    # app/models/__init__.py
    (app_dir / "models" / "__init__.py").write_text("")
    files.append("app/models/__init__.py")
    
    # app/services/__init__.py
    (app_dir / "services" / "__init__.py").write_text("")
    files.append("app/services/__init__.py")
    
    # .env.example
    env_example = '''# Application
APP_NAME=API
DEBUG=true

# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/db

# Security
SECRET_KEY=your-secret-key-here
'''
    (path / ".env.example").write_text(env_example)
    files.append(".env.example")
    
    # Makefile
    makefile = '''# Development commands

.PHONY: dev test lint format install

install:
\tuv sync

dev:
\tuv run uvicorn app.main:app --reload --port 8000

test:
\tuv run pytest -v

lint:
\tuv run ruff check .

format:
\tuv run ruff format .

# Shortcuts
run: dev
'''
    (path / "Makefile").write_text(makefile)
    files.append("Makefile")
    
    return files


def create_express_backend(path: Path, name: str, config: dict) -> list[str]:
    """Create Express.js backend structure."""
    files = []
    
    # Create directories
    src_dir = path / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "routes").mkdir(exist_ok=True)
    (src_dir / "middleware").mkdir(exist_ok=True)
    (src_dir / "services").mkdir(exist_ok=True)
    
    # package.json
    package_json = f'''{{
  "name": "{name}",
  "version": "0.1.0",
  "description": "{config.get('description', 'An Express.js application')}",
  "type": "module",
  "main": "src/index.js",
  "packageManager": "pnpm@9.0.0",
  "scripts": {{
    "start": "node src/index.js",
    "dev": "node --watch src/index.js",
    "test": "vitest",
    "lint": "eslint src/"
  }},
  "dependencies": {{
    "express": "^4.21.0",
    "cors": "^2.8.5"
  }},
  "devDependencies": {{
    "eslint": "^9.0.0",
    "vitest": "^2.0.0"
  }}
}}
'''
    (path / "package.json").write_text(package_json)
    files.append("package.json")
    
    # src/index.js (ES modules)
    index_js = '''import express from 'express';
import cors from 'cors';
import { router } from './routes/index.js';

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

app.use('/api', router);

app.get('/health', (req, res) => {
  res.json({ status: 'healthy' });
});

app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
'''
    (src_dir / "index.js").write_text(index_js)
    files.append("src/index.js")
    
    # src/routes/index.js
    routes_js = '''import { Router } from 'express';

export const router = Router();

router.get('/', (req, res) => {
  res.json({ message: 'API is running' });
});
'''
    (src_dir / "routes" / "index.js").write_text(routes_js)
    files.append("src/routes/index.js")
    
    # .env.example
    env_example = '''PORT=3000
NODE_ENV=development
'''
    (path / ".env.example").write_text(env_example)
    files.append(".env.example")
    
    return files


def create_react_frontend(path: Path, name: str, config: dict) -> list[str]:
    """Create React frontend structure (Vite-based)."""
    files = []
    
    # For fullstack, put frontend in subdirectory
    proj_type = config.get("type", "frontend")
    if proj_type == "fullstack":
        frontend_dir = path / "frontend"
        frontend_dir.mkdir(exist_ok=True)
        prefix = "frontend/"
    else:
        frontend_dir = path
        prefix = ""
    
    # Create directories
    src_dir = frontend_dir / "src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / "components").mkdir(exist_ok=True)
    (src_dir / "pages").mkdir(exist_ok=True)
    (src_dir / "hooks").mkdir(exist_ok=True)
    (src_dir / "utils").mkdir(exist_ok=True)
    
    # package.json
    package_json = f'''{{
  "name": "{name}-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "packageManager": "pnpm@9.0.0",
  "scripts": {{
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "lint": "eslint src/"
  }},
  "dependencies": {{
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "react-router-dom": "^6.26.0"
  }},
  "devDependencies": {{
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "eslint": "^9.0.0",
    "eslint-plugin-react": "^7.35.0",
    "vite": "^5.4.0"
  }}
}}
'''
    (frontend_dir / "package.json").write_text(package_json)
    files.append(f"{prefix}package.json")
    
    # vite.config.js
    vite_config = '''import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
'''
    (frontend_dir / "vite.config.js").write_text(vite_config)
    files.append(f"{prefix}vite.config.js")
    
    # index.html
    index_html = '''<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>App</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
'''
    (frontend_dir / "index.html").write_text(index_html)
    files.append(f"{prefix}index.html")
    
    # src/main.jsx
    main_jsx = '''import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
'''
    (src_dir / "main.jsx").write_text(main_jsx)
    files.append(f"{prefix}src/main.jsx")
    
    # src/App.jsx
    app_jsx = '''import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Home from './pages/Home'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
'''
    (src_dir / "App.jsx").write_text(app_jsx)
    files.append(f"{prefix}src/App.jsx")
    
    # src/pages/Home.jsx
    home_jsx = f'''function Home() {{
  return (
    <div className="container">
      <h1>{name}</h1>
      <p>Welcome to your new project!</p>
    </div>
  )
}}

export default Home
'''
    (src_dir / "pages" / "Home.jsx").write_text(home_jsx)
    files.append(f"{prefix}src/pages/Home.jsx")
    
    # src/index.css
    index_css = '''* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
  line-height: 1.6;
  color: #333;
}

.container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem;
}
'''
    (src_dir / "index.css").write_text(index_css)
    files.append(f"{prefix}src/index.css")
    
    return files


def create_docker_files(path: Path, name: str, config: dict) -> list[str]:
    """Create Docker-related files."""
    files = []
    proj_type = config.get("type", "backend")
    stack = config.get("stack", {})
    
    # Dockerfile
    if proj_type == "backend" and stack.get("backend") == "fastapi":
        dockerfile = '''FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

COPY app/ ./app/

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
'''
    elif proj_type == "backend" and stack.get("backend") == "express":
        dockerfile = '''FROM node:20-slim

WORKDIR /app

# Enable pnpm
RUN corepack enable pnpm

COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile --prod

COPY src/ ./src/

CMD ["node", "src/index.js"]
'''
    else:
        dockerfile = '''# Multi-stage build
FROM python:3.12-slim AS backend
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev
COPY app/ ./app/

FROM node:20-slim AS frontend
WORKDIR /app
RUN corepack enable pnpm
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=backend /app /app
COPY --from=frontend /app/dist ./static

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
'''
    
    (path / "Dockerfile").write_text(dockerfile)
    files.append("Dockerfile")
    
    # docker-compose.yml
    compose = f'''services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://user:password@db:5432/{name}
    depends_on:
      - db

  db:
    image: postgres:16-alpine
    environment:
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=password
      - POSTGRES_DB={name}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

volumes:
  postgres_data:
'''
    (path / "docker-compose.yml").write_text(compose)
    files.append("docker-compose.yml")
    
    return files


def create_render_yaml(path: Path, name: str, config: dict) -> str:
    """Create Render deployment configuration."""
    render_yaml = f'''services:
  - type: web
    name: {name}
    runtime: python
    buildCommand: |
      curl -LsSf https://astral.sh/uv/install.sh | sh
      uv sync --frozen
    startCommand: uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: {name}-db
          property: connectionString

databases:
  - name: {name}-db
    plan: free
'''
    (path / "render.yaml").write_text(render_yaml)
    return "render.yaml"


def create_github_workflows(path: Path, name: str, config: dict) -> list[str]:
    """Create GitHub Actions workflows."""
    files = []
    
    workflows_dir = path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    
    stack = config.get("stack", {})
    backend = stack.get("backend", "fastapi")
    
    if backend in ("fastapi", "django"):
        ci_yaml = '''name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Install uv
        uses: astral-sh/setup-uv@v4
      
      - name: Set up Python
        run: uv python install 3.12
      
      - name: Install dependencies
        run: uv sync --all-extras
      
      - name: Run linter
        run: uv run ruff check .
      
      - name: Run tests
        run: uv run pytest -v
'''
    else:
        ci_yaml = '''name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Install pnpm
        uses: pnpm/action-setup@v4
        with:
          version: 9
      
      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "pnpm"
      
      - name: Install dependencies
        run: pnpm install --frozen-lockfile
      
      - name: Run linter
        run: pnpm lint
      
      - name: Run tests
        run: pnpm test
'''
    
    (workflows_dir / "ci.yml").write_text(ci_yaml)
    files.append(".github/workflows/ci.yml")
    
    return files
