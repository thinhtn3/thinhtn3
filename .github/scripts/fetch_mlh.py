#!/usr/bin/env python3
"""
fetch_mlh.py
Fetches hackathon/profile data from the MyMLH MCP server (with fallback
to the MyMLH REST API v4) and updates the hackathons section in README.md.

Requires: MLH_ACCESS_TOKEN environment variable (MyMLH OAuth access token).
"""

import json
import os
import re
import sys
import pathlib
import requests

MCP_URL    = "https://mymlh-mcp.git.ci/mcp"
MLH_API    = "https://my.mlh.io/api/v4"
README_PATH = pathlib.Path(__file__).resolve().parents[2] / "README.md"
START_MARKER = "<!-- DEVPOST:START -->"
END_MARKER   = "<!-- DEVPOST:END -->"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_MARKDOWN = "_[View projects on Devpost](https://devpost.com/thinhtn3)_\n"

def get_token() -> str:
    return os.environ.get("MLH_ACCESS_TOKEN", "").strip()


def parse_response(resp: requests.Response) -> dict:
    """Handle both plain JSON and text/event-stream (SSE) responses."""
    ct = resp.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                data = line[6:].strip()
                if data and data != "[DONE]":
                    try:
                        return json.loads(data)
                    except json.JSONDecodeError:
                        continue
        return {}
    return resp.json()


def mcp_post(method: str, params: dict, token: str, req_id: int = 1) -> dict:
    resp = requests.post(
        MCP_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
        timeout=30,
    )
    resp.raise_for_status()
    return parse_response(resp)


# ---------------------------------------------------------------------------
# Data fetching — MCP path
# ---------------------------------------------------------------------------

def fetch_via_mcp(token: str) -> dict | None:
    """
    Tries to initialize the MCP server, list its tools, and call each one.
    Returns a merged dict of all tool results, or None on failure.
    """
    try:
        init = mcp_post("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "readme-updater", "version": "1.0"},
        }, token, req_id=1)

        if "error" in init or not init.get("result"):
            print(f"[warn] MCP init failed: {init.get('error', 'empty result')}", file=sys.stderr)
            return None

        tools_resp = mcp_post("tools/list", {}, token, req_id=2)
        tools = tools_resp.get("result", {}).get("tools", [])
        if not tools:
            print("[warn] MCP returned no tools.", file=sys.stderr)
            return None

        print(f"[info] MCP tools: {[t['name'] for t in tools]}")

        merged: dict = {}
        for i, tool in enumerate(tools):
            call_resp = mcp_post("tools/call", {
                "name": tool["name"],
                "arguments": {},
            }, token, req_id=10 + i)
            result = call_resp.get("result", {})
            # MCP tools/call returns content blocks
            if isinstance(result, dict) and "content" in result:
                for block in result["content"]:
                    if block.get("type") == "text":
                        try:
                            merged[tool["name"]] = json.loads(block["text"])
                        except (json.JSONDecodeError, KeyError):
                            merged[tool["name"]] = block.get("text", "")
            elif result:
                merged[tool["name"]] = result

        return merged if merged else None

    except Exception as exc:
        print(f"[warn] MCP path failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Data fetching — direct REST API fallback
# ---------------------------------------------------------------------------

def fetch_via_api(token: str) -> dict | None:
    """Calls MyMLH REST API v4 /user endpoint directly."""
    try:
        resp = requests.get(
            f"{MLH_API}/user",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        # v4 wraps data under a "data" key
        return payload.get("data", payload)
    except Exception as exc:
        print(f"[warn] MLH REST API failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def build_markdown(data: dict) -> str:
    """
    Generates the README section from whatever MLH data is available.
    `data` is either the merged MCP tool results or the /user API response.
    """
    # Flatten nested dicts from MCP tool responses into a single lookup
    flat: dict = {}
    for v in data.values():
        if isinstance(v, dict):
            flat.update(v)
    if not flat:
        flat = data  # already flat (direct API response)

    lines: list[str] = []

    name = " ".join(filter(None, [flat.get("first_name"), flat.get("last_name")]))
    school = flat.get("school") or {}
    school_name = school.get("name") if isinstance(school, dict) else school
    major = flat.get("major")
    grad_year = flat.get("graduation_year")
    level = flat.get("level_of_study")
    hackathons = flat.get("hackathons_attended")

    if hackathons is not None:
        lines.append(f"**Hackathons attended (via MLH):** {hackathons}")
        lines.append("")

    rows: list[tuple[str, str]] = []
    if school_name:
        rows.append(("School", school_name))
    if major:
        rows.append(("Major", major))
    if grad_year:
        rows.append(("Graduation", str(grad_year)))
    if level:
        rows.append(("Level", level))

    if rows:
        lines.append("| | |")
        lines.append("|---|---|")
        for label, value in rows:
            lines.append(f"| **{label}** | {value} |")
        lines.append("")

    if not lines:
        lines.append("_MLH profile data unavailable._")

    import time
    timestamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    lines.append(f"_Last updated: {timestamp}_")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# README injection
# ---------------------------------------------------------------------------

def update_readme(content: str) -> bool:
    text = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(" + re.escape(START_MARKER) + r"\n)"
        r".*?"
        r"(" + re.escape(END_MARKER) + r")",
        re.DOTALL,
    )
    updated, count = pattern.subn(r"\g<1>" + content + r"\g<2>", text)
    if count == 0:
        print("[error] Sentinel markers not found in README.md.", file=sys.stderr)
        sys.exit(1)
    if updated == text:
        print("[info] README.md unchanged.")
        return False
    README_PATH.write_text(updated, encoding="utf-8")
    print("[info] README.md updated.")
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    token = get_token()

    data = None
    if token:
        print("[info] Trying MyMLH MCP server...")
        data = fetch_via_mcp(token)

        if data is None:
            print("[info] Falling back to MyMLH REST API...")
            data = fetch_via_api(token)
    else:
        print("[warn] No MLH_ACCESS_TOKEN set — using default content.", file=sys.stderr)

    if not data:
        print("[info] Using default markdown.")
        update_readme(DEFAULT_MARKDOWN)
        return

    markdown = build_markdown(data)
    update_readme(markdown)


if __name__ == "__main__":
    main()
