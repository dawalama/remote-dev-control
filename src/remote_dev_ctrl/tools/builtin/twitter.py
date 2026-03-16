import os
import re
from urllib.parse import urlparse

import httpx

from ..base import Tool, ToolParam

X_API_BASE = "https://api.twitter.com/2"


def _get_bearer_token() -> str | None:
    try:
        from ...server.vault import get_secret
        token = get_secret("X_BEARER_TOKEN") or get_secret("TWITTER_BEARER_TOKEN")
        if token:
            return token
    except Exception:
        pass
    return os.environ.get("X_BEARER_TOKEN") or os.environ.get("TWITTER_BEARER_TOKEN")


def _tweet_id_from_url(url_or_id: str | int) -> str | None:
    s = str(url_or_id).strip()
    if re.match(r"^\d{15,}$", s):
        return s
    try:
        parsed = urlparse(s)
        if parsed.netloc and ("twitter.com" in parsed.netloc or "x.com" in parsed.netloc):
            parts = parsed.path.rstrip("/").split("/")
            if len(parts) >= 2 and parts[-2] == "status":
                return parts[-1]
    except Exception:
        pass
    return None


def _fetch_tweet(url_or_id: str | int) -> dict:
    token = _get_bearer_token()
    if not token:
        return {
            "error": "Twitter API not configured",
            "hint": "Store in vault: rdc config set-secret X_BEARER_TOKEN <token>. Or set env X_BEARER_TOKEN. Get a token at https://developer.x.com/.",
        }

    tweet_id = _tweet_id_from_url(url_or_id)
    if not tweet_id:
        return {"error": "Invalid tweet URL or ID", "input": url_or_id}

    url = f"{X_API_BASE}/tweets/{tweet_id}"
    params = {
        "tweet.fields": "created_at,text,author_id,public_metrics",
        "expansions": "author_id",
        "user.fields": "username,name",
    }
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(timeout=15) as client:
        resp = client.get(url, params=params, headers=headers)

    if resp.status_code == 401:
        return {"error": "Twitter API authentication failed", "hint": "Check bearer token."}
    if resp.status_code == 404:
        return {"error": "Tweet not found or not accessible", "tweet_id": tweet_id}
    if resp.status_code == 429:
        return {"error": "Twitter API rate limit exceeded", "tweet_id": tweet_id}
    if resp.status_code != 200:
        return {
            "error": f"Twitter API error {resp.status_code}",
            "body": resp.text[:500],
        }

    data = resp.json()
    tweet = data.get("data") or {}
    users = {u["id"]: u for u in (data.get("includes", {}).get("users") or [])}
    author = users.get(tweet.get("author_id") or "")

    return {
        "text": tweet.get("text", ""),
        "id": tweet.get("id"),
        "created_at": tweet.get("created_at"),
        "author": {
            "username": author.get("username"),
            "name": author.get("name"),
        } if author else None,
        "public_metrics": tweet.get("public_metrics"),
    }


twitter_read_tool = Tool(
    name="twitter_read",
    description="Fetch a single X (Twitter) post by URL or numeric ID. Requires X_BEARER_TOKEN or TWITTER_BEARER_TOKEN.",
    func=_fetch_tweet,
    params=[
        ToolParam(
            name="url_or_id",
            type="str",
            required=True,
            description="Tweet URL (e.g. https://x.com/user/status/123...) or numeric tweet ID",
        ),
    ],
    returns="dict",
    tags=["twitter", "fetch", "url"],
)
