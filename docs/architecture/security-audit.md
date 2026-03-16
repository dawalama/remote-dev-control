# RDC Security & Audit Architecture

## Threat Model

### Assets to Protect
1. **Secrets** - API keys, tokens (Telegram, LLM providers, etc.)
2. **Code** - Project source code agents can read/write
3. **Commands** - Ability to spawn agents, run arbitrary tasks
4. **Logs** - May contain sensitive output, credentials leaked by mistake
5. **Config** - System configuration, project paths

### Threat Actors
1. **External attacker** - Gains network access to exposed API
2. **Malicious agent output** - LLM generates code that exfiltrates data
3. **Compromised channel** - Attacker gains access to Telegram account
4. **Log scraper** - Searches logs for leaked secrets
5. **Insider** - Authorized user exceeds their permissions

### Attack Vectors
- Exposed API without auth → full system control
- Prompt injection → agent runs malicious commands
- Log exposure → secrets in output
- Telegram token theft → impersonate bot
- SSRF via agent → access internal network
- Path traversal → read files outside project

---

## Security Controls

### 1. Authentication & Authorization

#### API Authentication
```python
# Every request must have valid token
Authorization: Bearer <token>

# Token types
- API Key (long-lived, for automation)
- Session Token (short-lived, for dashboard)
- Channel Token (per-channel: Telegram, voice)
```

#### Role-Based Access Control (RBAC)
```yaml
roles:
  admin:
    - "*"  # Full access
  
  operator:
    - agents.spawn
    - agents.stop
    - tasks.create
    - tasks.cancel
    - logs.read
  
  viewer:
    - agents.list
    - tasks.list
    - logs.read
    - status.read
  
  agent:  # For agent-to-server communication
    - heartbeat
    - task.update
    - logs.write
```

#### User/Token Management
```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    name TEXT,
    role TEXT NOT NULL,
    created_at TIMESTAMP,
    created_by TEXT,
    disabled BOOLEAN DEFAULT FALSE
);

CREATE TABLE api_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id),
    token_hash TEXT NOT NULL,  -- bcrypt hash, never store plain
    name TEXT,                  -- "CI/CD token", "Mobile app"
    scopes JSON,                -- Optional scope restrictions
    created_at TIMESTAMP,
    expires_at TIMESTAMP,
    last_used_at TIMESTAMP,
    revoked BOOLEAN DEFAULT FALSE
);
```

### 2. Audit Logging

#### What to Log (Always)
```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Who
    actor_type TEXT NOT NULL,    -- 'user', 'agent', 'system', 'channel'
    actor_id TEXT,               -- user_id, agent project, 'telegram:123456'
    actor_ip TEXT,               -- Source IP if applicable
    
    -- What
    action TEXT NOT NULL,        -- 'agent.spawn', 'task.create', 'secret.read'
    resource_type TEXT,          -- 'agent', 'task', 'secret', 'project'
    resource_id TEXT,            -- Specific resource affected
    
    -- Context
    request_id TEXT,             -- Correlation ID for request tracing
    channel TEXT,                -- 'api', 'dashboard', 'telegram', 'cli'
    
    -- Outcome
    status TEXT,                 -- 'success', 'denied', 'error'
    error TEXT,                  -- Error message if failed
    
    -- Details (careful - no secrets!)
    metadata JSON                -- Sanitized request details
);

CREATE INDEX idx_audit_time ON audit_log(timestamp);
CREATE INDEX idx_audit_actor ON audit_log(actor_type, actor_id);
CREATE INDEX idx_audit_action ON audit_log(action);
CREATE INDEX idx_audit_resource ON audit_log(resource_type, resource_id);
```

#### Audit Events (Non-exhaustive)
```
# Authentication
auth.login.success
auth.login.failed
auth.token.created
auth.token.revoked

# Authorization
authz.denied              # Permission denied

# Agents
agent.spawn
agent.stop
agent.error
agent.task.assigned

# Tasks
task.created
task.started
task.completed
task.failed
task.cancelled

# Secrets
secret.read               # Log that it was accessed, not the value!
secret.write
secret.delete

# Config
config.updated
project.added
project.removed

# Channels
channel.telegram.command
channel.voice.command

# Security Events
security.rate_limit
security.blocked_ip
security.suspicious_prompt  # Potential prompt injection
```

#### Audit Log Immutability
```python
# Audit logs should be append-only
# Options:
# 1. Separate SQLite file with no DELETE permissions
# 2. Write to append-only file + periodic DB sync
# 3. Sign each entry with HMAC to detect tampering

class AuditLogger:
    def log(self, event: AuditEvent):
        # Include HMAC of previous entry for chain integrity
        event.prev_hash = self._last_hash
        event.hash = hmac(event.serialize(), secret_key)
        self._last_hash = event.hash
        self._write(event)
```

### 3. Secret Protection

#### In Storage
```python
# Already implemented: age encryption or base64 obfuscation
# Upgrade path: 
# - Use age with hardware key (YubiKey)
# - Or: HashiCorp Vault integration for enterprise
```

#### In Logs (Critical!)
```python
class SecretScrubber:
    """Scrub secrets from log output before writing."""
    
    PATTERNS = [
        r'(?i)(api[_-]?key|token|secret|password|auth)\s*[=:]\s*["\']?[\w-]+',
        r'Bearer\s+[\w-]+',
        r'sk-[a-zA-Z0-9]+',  # OpenAI
        r'ghp_[a-zA-Z0-9]+', # GitHub
        # ... more patterns
    ]
    
    def scrub(self, text: str, known_secrets: list[str]) -> str:
        result = text
        
        # Scrub known secrets
        for secret in known_secrets:
            if len(secret) > 8:  # Don't scrub short strings
                result = result.replace(secret, '[REDACTED]')
        
        # Scrub pattern matches
        for pattern in self.PATTERNS:
            result = re.sub(pattern, '[REDACTED]', result)
        
        return result
```

#### In Memory
```python
# Don't keep secrets in memory longer than needed
# Use secure string handling where possible

class SecureString:
    def __init__(self, value: str):
        self._value = value
    
    def __del__(self):
        # Overwrite memory (best effort in Python)
        if hasattr(self, '_value'):
            self._value = 'x' * len(self._value)
    
    def __repr__(self):
        return '[SecureString]'
    
    def reveal(self) -> str:
        return self._value
```

### 4. Agent Sandboxing

#### Command Restrictions
```yaml
agent_security:
  # Paths agent can access
  allowed_paths:
    - "${PROJECT_PATH}"
    - "${PROJECT_PATH}/../.ai"  # Global knowledge
  
  # Commands agent cannot run
  blocked_commands:
    - "curl"      # Prevent exfiltration (unless explicitly allowed)
    - "wget"
    - "nc"
    - "ssh"
    - "scp"
  
  # Environment variables to hide from agent
  hidden_env:
    - "*_KEY"
    - "*_TOKEN"
    - "*_SECRET"
    - "AWS_*"
```

#### Prompt Injection Detection
```python
SUSPICIOUS_PATTERNS = [
    r"ignore previous instructions",
    r"disregard.*above",
    r"new instructions:",
    r"system prompt:",
    r"<\|.*\|>",  # Special tokens
    r"```.*curl.*\|.*bash",  # Pipe to bash
]

def check_prompt_injection(prompt: str) -> bool:
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            audit_log("security.suspicious_prompt", prompt=prompt[:200])
            return True
    return False
```

### 5. Network Security

#### Rate Limiting
```python
from collections import defaultdict
import time

class RateLimiter:
    def __init__(self, requests_per_minute: int = 60):
        self.rpm = requests_per_minute
        self.requests = defaultdict(list)
    
    def check(self, client_id: str) -> bool:
        now = time.time()
        minute_ago = now - 60
        
        # Clean old requests
        self.requests[client_id] = [
            t for t in self.requests[client_id] if t > minute_ago
        ]
        
        if len(self.requests[client_id]) >= self.rpm:
            audit_log("security.rate_limit", client=client_id)
            return False
        
        self.requests[client_id].append(now)
        return True
```

#### IP Allowlisting (Optional)
```yaml
security:
  ip_allowlist:
    enabled: false  # Enable for high-security deployments
    addresses:
      - "192.168.1.0/24"
      - "10.0.0.0/8"
```

#### TLS
```yaml
server:
  tls:
    enabled: true  # Always for external exposure
    cert_file: "/path/to/cert.pem"
    key_file: "/path/to/key.pem"
    # Or: auto via Let's Encrypt / Caddy proxy
```

### 6. Channel Security

#### Telegram
```python
# Already implemented: allowed_users list
# Additional:
# - Verify update authenticity (check secret_token)
# - Log all commands with user ID
# - Rate limit per user
# - Command confirmation for destructive actions

async def handle_spawn(update, context):
    user_id = update.effective_user.id
    
    # Audit
    audit_log("channel.telegram.command", 
              actor_id=f"telegram:{user_id}",
              action="agent.spawn",
              metadata={"args": context.args})
    
    # Confirmation for dangerous ops
    if is_destructive(context.args):
        await update.message.reply_text(
            f"Confirm spawn with task? Reply /confirm within 30s"
        )
        return await wait_for_confirmation(user_id, timeout=30)
```

#### Future: Voice (Twilio)
```python
# Voice requires extra care:
# - Caller ID verification
# - Voice PIN for sensitive commands
# - Read back commands before executing
# - No secrets spoken aloud
```

---

## Implementation Priority

### Phase 1 (Before External Exposure)
- [ ] API authentication (Bearer token)
- [ ] Audit logging for all actions
- [ ] Secret scrubbing in logs
- [ ] Rate limiting
- [ ] TLS termination

### Phase 2 (Multi-User)
- [ ] RBAC implementation
- [ ] User/token management UI
- [ ] Audit log viewer in dashboard
- [ ] IP allowlisting option

### Phase 3 (Enterprise)
- [ ] SSO/OIDC integration
- [ ] Vault integration for secrets
- [ ] Agent sandboxing
- [ ] Prompt injection detection
- [ ] Compliance reports from audit log

---

## Audit Log Retention

```yaml
audit:
  retention:
    # Keep detailed logs for 90 days
    detailed_days: 90
    
    # Keep summary/aggregates for 2 years
    summary_years: 2
    
    # Security events: keep forever (or per compliance)
    security_events: forever
    
  export:
    # For compliance: export to immutable storage
    enabled: false
    destination: "s3://audit-logs-bucket/"
    encryption: true
```
