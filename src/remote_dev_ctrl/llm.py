"""LLM integration for intelligent features."""

import json
import subprocess
from dataclasses import dataclass


@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "qwen3.5"
    timeout: int = 60


# Constrained valid values for project configuration
VALID_TYPES = {"backend", "frontend", "fullstack"}
VALID_BACKENDS = {"fastapi", "express", "django", "none"}
VALID_FRONTENDS = {"react", "vue", "nextjs", "none"}
VALID_DATABASES = {"postgres", "mongodb", "sqlite", "none"}
VALID_DEPLOYMENTS = {"docker", "render", "vercel", "aws", "none"}
VALID_FEATURES = {
    "auth", "pdf", "email", "file-upload", "payments", "search",
    "websocket", "caching", "notifications", "analytics", "admin",
    "api", "graphql", "queue", "scheduler", "storage"
}

# Keywords that map to features (for validation/fallback)
FEATURE_KEYWORDS = {
    "auth": ["auth", "login", "user", "sso", "oauth", "jwt", "session", "password"],
    "pdf": ["pdf", "document", "report", "invoice", "generate", "export"],
    "email": ["email", "mail", "notification", "send", "smtp"],
    "file-upload": ["upload", "file", "storage", "s3", "attachment", "image"],
    "payments": ["payment", "stripe", "billing", "subscription", "checkout"],
    "search": ["search", "elasticsearch", "filter", "query", "find"],
    "websocket": ["realtime", "websocket", "live", "chat", "stream", "real-time"],
    "caching": ["cache", "redis", "memcache", "fast"],
    "notifications": ["notification", "push", "alert"],
    "analytics": ["analytics", "metrics", "dashboard", "monitor", "tracking"],
    "admin": ["admin", "backoffice", "management", "cms"],
    "queue": ["queue", "worker", "background", "celery", "job"],
    "scheduler": ["schedule", "cron", "periodic", "timer"],
    "storage": ["storage", "blob", "s3", "bucket", "cdn"],
}


def ollama_generate(prompt: str, model: str = None, format_json: bool = False) -> str | dict | None:
    """Generate text using Ollama."""
    if model is None:
        try:
            from .server.intent import load_nanobot_config
            cfg = load_nanobot_config()
            model = cfg.get("ollama_model", "qwen3.5")
        except Exception:
            model = "qwen3.5"
    try:
        cmd = ["ollama", "run", model]
        if format_json:
            cmd.extend(["--format", "json"])
        
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        if result.returncode != 0:
            return None
        
        output = result.stdout.strip()
        
        if format_json:
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                # Try to extract JSON from the output
                start = output.find("{")
                end = output.rfind("}") + 1
                if start >= 0 and end > start:
                    return json.loads(output[start:end])
                return None
        
        return output
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        return None


def llm_generate(prompt: str, format_json: bool = False) -> str | dict | None:
    """Generate text using OpenRouter/OpenAI (if configured), falling back to Ollama."""
    # Respect llm_provider setting — skip cloud entirely when user chose "ollama"
    try:
        from .server.intent import load_nanobot_config
        provider = load_nanobot_config().get("llm_provider", "cloud")
    except Exception:
        provider = "cloud"

    if provider == "ollama":
        return ollama_generate(prompt, format_json=format_json)

    try:
        import os
        from .server.vault import get_secret

        openrouter_key = get_secret("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        openai_key = get_secret("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

        api_key = openrouter_key or openai_key

        if api_key:
            try:
                from openai import OpenAI
            except ImportError:
                pass
            else:
                client = OpenAI(
                    api_key=api_key,
                    base_url="https://openrouter.ai/api/v1" if openrouter_key else None
                )
                
                # Get model from nanobot config if possible
                try:
                    from .server.intent import load_nanobot_config
                    cfg = load_nanobot_config()
                    model = cfg.get("model_fast", "google/gemini-2.0-flash-001" if openrouter_key else "gpt-4o-mini")
                except Exception:
                    model = "google/gemini-2.0-flash-001" if openrouter_key else "gpt-4o-mini"
                    
                kwargs = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                }
                
                # Note: Not all OpenRouter models support response_format={"type": "json_object"}
                # So we just rely on prompt instructions to return JSON.
                
                response = client.chat.completions.create(**kwargs)
                output = response.choices[0].message.content
                
                if format_json:
                    import json
                    try:
                        return json.loads(output)
                    except json.JSONDecodeError:
                        start = output.find("{")
                        end = output.rfind("}") + 1
                        if start >= 0 and end > start:
                            return json.loads(output[start:end])
                        return None
                return output
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"Cloud LLM failed, falling back to Ollama: {e}")
        
    return ollama_generate(prompt, format_json=format_json)


def analyze_project_description(description: str) -> dict:
    """Use LLM to analyze a project description and suggest configuration."""
    
    # List valid options explicitly in prompt
    features_list = ", ".join(sorted(VALID_FEATURES))
    
    prompt = f"""Analyze this project description and suggest configuration.

Description: "{description}"

Respond with ONLY valid JSON:
{{
    "suggested_name": "kebab-case-name if mentioned in description, otherwise null",
    "type": "backend" or "frontend" or "fullstack",
    "stack": {{
        "backend": "fastapi" or "express" or "django" or "none",
        "frontend": "react" or "vue" or "nextjs" or "none"
    }},
    "database": "postgres" or "mongodb" or "sqlite" or "none",
    "deployment": "docker" or "render" or "vercel" or "none",
    "features": [],
    "reasoning": "brief explanation"
}}

Rules:
- If the description mentions a project name (like "call it X" or "named X"), extract it as suggested_name in kebab-case
- For "features", ONLY use values from: {features_list}
- If a feature is not mentioned, do not include it"""

    result = llm_generate(prompt, format_json=True)
    
    if result and isinstance(result, dict):
        result = validate_and_fix_config(result, description)
        
        # If LLM didn't extract a name, try regex fallback
        if not result.get("suggested_name"):
            extracted = extract_project_name(description)
            if extracted:
                result["suggested_name"] = extracted
        
        return result
    
    # Fallback to heuristics if LLM fails
    fallback = analyze_with_heuristics(description)
    
    # Try to extract name even in fallback
    extracted = extract_project_name(description)
    if extracted:
        fallback["suggested_name"] = extracted
    
    return fallback


def extract_project_name(description: str) -> str | None:
    """Try to extract a project name from description using patterns."""
    import re
    
    desc_lower = description.lower()
    
    # Patterns that indicate a name is being given
    patterns = [
        r"(?:call it|called|named|name it|let's call it|let's name it)\s+['\"]?([a-z][a-z0-9-_]+)['\"]?",
        r"([a-z][a-z0-9-]+(?:-[a-z0-9]+)+)\s+(?:is also|would be|could be)",  # "foo-bar is also a good name"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, desc_lower)
        if match:
            name = match.group(1).strip("'\".,")
            # Convert to kebab-case if needed
            name = re.sub(r'[_\s]+', '-', name)
            return name
    
    return None


def validate_and_fix_config(config: dict, description: str) -> dict:
    """Validate LLM output and fix any invalid values."""
    
    # Validate type
    if config.get("type") not in VALID_TYPES:
        config["type"] = "backend"
    
    # Validate stack
    stack = config.get("stack", {})
    if not isinstance(stack, dict):
        stack = {}
    if stack.get("backend") not in VALID_BACKENDS:
        stack["backend"] = "fastapi" if config["type"] in ("backend", "fullstack") else "none"
    if stack.get("frontend") not in VALID_FRONTENDS:
        stack["frontend"] = "react" if config["type"] in ("frontend", "fullstack") else "none"
    config["stack"] = stack
    
    # Validate database
    if config.get("database") not in VALID_DATABASES:
        config["database"] = "postgres"
    
    # Validate deployment
    if config.get("deployment") not in VALID_DEPLOYMENTS:
        config["deployment"] = "docker"
    
    # Validate features - only keep valid ones, and cross-check with description
    llm_features = config.get("features", [])
    if not isinstance(llm_features, list):
        llm_features = []
    
    # Filter to only valid features
    valid_features = [f.lower() for f in llm_features if f.lower() in VALID_FEATURES]
    
    # Also detect features from description keywords (hybrid approach)
    desc_lower = description.lower()
    detected_features = set()
    for feature, keywords in FEATURE_KEYWORDS.items():
        if any(kw in desc_lower for kw in keywords):
            detected_features.add(feature)
    
    # Merge: LLM features that are valid + keyword-detected features
    # But prioritize keyword detection for accuracy
    final_features = list(detected_features)
    
    config["features"] = final_features
    
    return config


def analyze_with_heuristics(description: str) -> dict:
    """Fallback heuristics when LLM is unavailable."""
    desc_lower = description.lower()
    
    # Detect type
    is_backend = any(w in desc_lower for w in ["api", "rest", "backend", "server", "database", "crud"])
    is_frontend = any(w in desc_lower for w in ["ui", "frontend", "dashboard", "interface", "web app", "webapp"])
    
    if is_backend and is_frontend:
        proj_type = "fullstack"
    elif is_frontend:
        proj_type = "frontend"
    else:
        proj_type = "backend"
    
    # Detect backend stack
    backend_stack = "none"
    if proj_type in ("backend", "fullstack"):
        if any(w in desc_lower for w in ["python", "fastapi", "pdf", "ml", "ai", "data"]):
            backend_stack = "fastapi"
        elif any(w in desc_lower for w in ["node", "express", "javascript", "typescript"]):
            backend_stack = "express"
        elif any(w in desc_lower for w in ["django", "admin"]):
            backend_stack = "django"
        else:
            backend_stack = "fastapi"  # default
    
    # Detect frontend stack
    frontend_stack = "none"
    if proj_type in ("frontend", "fullstack"):
        if any(w in desc_lower for w in ["next", "nextjs", "ssr"]):
            frontend_stack = "nextjs"
        elif any(w in desc_lower for w in ["vue"]):
            frontend_stack = "vue"
        else:
            frontend_stack = "react"  # default
    
    # Detect database
    database = "none"
    if any(w in desc_lower for w in ["postgres", "postgresql", "relational", "sql"]):
        database = "postgres"
    elif any(w in desc_lower for w in ["mongo", "nosql", "document"]):
        database = "mongodb"
    elif any(w in desc_lower for w in ["database", "store", "persist", "crud"]):
        database = "postgres"  # default
    
    # Detect features
    features = []
    feature_keywords = {
        "auth": ["auth", "login", "user", "sso", "oauth"],
        "file-upload": ["upload", "file", "storage", "s3", "attachment"],
        "pdf": ["pdf", "document", "report", "invoice"],
        "email": ["email", "notification", "mail"],
        "payments": ["payment", "stripe", "billing", "subscription"],
        "search": ["search", "elasticsearch", "filter"],
        "websocket": ["realtime", "websocket", "live", "chat"],
        "caching": ["cache", "redis"],
    }
    
    for feature, keywords in feature_keywords.items():
        if any(kw in desc_lower for kw in keywords):
            features.append(feature)
    
    return {
        "type": proj_type,
        "stack": {
            "backend": backend_stack,
            "frontend": frontend_stack,
        },
        "database": database,
        "deployment": "docker",  # sensible default
        "features": features,
        "reasoning": "Inferred from keywords in description (heuristic fallback)",
    }


def is_ollama_available() -> bool:
    """Check if Ollama is running and available."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def analyze_existing_project(path) -> dict:
    """Analyze an existing directory to infer project name, description and config."""
    from pathlib import Path
    
    # Gather files context
    path_obj = Path(path)
    if not path_obj.exists() or not path_obj.is_dir():
        # Fallback if invalid path
        return {
            "name": path_obj.name,
            "description": f"Imported project",
            "type": "backend",
            "stack": {"backend": "none", "frontend": "none"},
            "database": "none",
            "deployment": "none",
            "features": [],
            "reasoning": "Path does not exist or is not a directory"
        }
        
    file_names = [f.name for f in path_obj.iterdir() if f.is_file() or (f.is_dir() and not f.name.startswith("."))]
    
    context_parts = []
    context_parts.append(f"Directory Name: {path_obj.name}")
    context_parts.append(f"Root Files/Folders: {', '.join(file_names[:30])}")
    
    # Try to read interesting files
    for file_to_read in ["package.json", "pyproject.toml", "README.md", "docker-compose.yml", "Cargo.toml", "go.mod"]:
        target = path_obj / file_to_read
        if target.exists():
            try:
                content = target.read_text(errors='ignore')
                # truncate to max 1500 chars to save context
                if len(content) > 1500:
                    content = content[:1500] + "... (truncated)"
                context_parts.append(f"--- {file_to_read} ---\n{content}\n")
            except Exception:
                pass
                
    context = "\n".join(context_parts)
    
    features_list = ", ".join(sorted(VALID_FEATURES))
    prompt = f"""Analyze this existing codebase and infer its configuration.

Codebase Context:
{context}

Respond with ONLY valid JSON:
{{
    "name": "project-name",
    "description": "Short description of what the project is/does",
    "type": "backend" or "frontend" or "fullstack",
    "stack": {{
        "backend": "fastapi" or "express" or "django" or "none",
        "frontend": "react" or "vue" or "nextjs" or "none"
    }},
    "database": "postgres" or "mongodb" or "sqlite" or "none",
    "deployment": "docker" or "render" or "vercel" or "none",
    "features": [],
    "reasoning": "brief explanation"
}}

Rules:
- Infer the best `name` from package.json, pyproject.toml, or directory name. Use kebab-case.
- Keep `description` under 2 sentences.
- For "features", ONLY use values from: {features_list}
"""
    result = llm_generate(prompt, format_json=True)
    
    if result and isinstance(result, dict):
        result = validate_and_fix_config(result, result.get("description", ""))
        
        if not result.get("name"):
            result["name"] = path_obj.name
        if not result.get("description"):
            result["description"] = f"A {result.get('type', 'project')} imported from {path_obj.name}"
            
        return result
        
    # Fallback heuristics
    return {
        "name": path_obj.name,
        "description": f"Imported project from {path_obj.name}",
        "type": "backend",
        "stack": {"backend": "none", "frontend": "none"},
        "database": "none",
        "deployment": "none",
        "features": [],
        "reasoning": "Fallback (LLM failed)"
    }

