"""Refresh only the public repositories starred by Lucatronlk in README.md.

No third-party renderer and no token are required. All links point to validated
github.com/OWNER/REPO paths, and API-provided descriptions are escaped.
"""

from __future__ import annotations

import html
import http.client
import json
import os
from pathlib import Path
import re
import tempfile


README = Path("README.md")
HOST = "api.github.com"
PATH = "/users/Lucatronlk/starred?sort=created&direction=desc&per_page=30"
START = "<!-- JUSTSEC_STARRED_START -->"
END = "<!-- JUSTSEC_STARRED_END -->"
MAX_BYTES = 500_000
MAX_ITEMS = 8
REPO_NAME = re.compile(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+\Z")


class StarredError(Exception):
    """API response or README structure failed verification."""


def fetch_starred() -> list[dict]:
    """Fetch the public-star feed from the fixed GitHub API hostname."""
    conn = http.client.HTTPSConnection(HOST, timeout=15)
    try:
        conn.request(
            "GET",
            PATH,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Lucatronlk-justsec-profile",
            },
        )
        response = conn.getresponse()
        if response.status != 200:
            raise StarredError(f"GitHub public star lookup returned HTTP {response.status}")
        payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise StarredError("GitHub public star response exceeded size limit")
        data = json.loads(payload)
        if not isinstance(data, list) or len(data) > 30:
            raise StarredError("Unexpected GitHub public star response")
        return data
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise StarredError("GitHub public star lookup failed") from error
    finally:
        conn.close()


def safe_text(value: object, max_length: int = 125) -> str:
    if not isinstance(value, str):
        return "—"
    value = " ".join(value.split())[:max_length].strip()
    if not value:
        return "—"
    # HTML escaping plus neutralizing Markdown table and inline-link syntax.
    escaped = html.escape(value, quote=True)
    return escaped.replace("|", "&#124;").replace("[", "&#91;").replace("]", "&#93;").replace("`", "&#96;")


def render_starred(items: list[dict]) -> str:
    rows = ["| Repository | Description | Language |", "| :--- | :--- | :--- |"]
    found: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        repo = raw.get("repo", raw)
        if not isinstance(repo, dict) or repo.get("private") is not False:
            continue
        name = repo.get("full_name")
        if not isinstance(name, str) or not REPO_NAME.fullmatch(name) or name.lower() in found:
            continue
        found.add(name.lower())
        rows.append(
            f"| [{name}](https://github.com/{name}) | "
            f"{safe_text(repo.get('description'))} | "
            f"{safe_text(repo.get('language'), max_length=32)} |"
        )
        if len(found) >= MAX_ITEMS:
            break
    if len(rows) == 2:
        return "_No public starred repositories are currently available._"
    return "\n".join(rows)


def update_readme(contents: str, table: str) -> str:
    if contents.count(START) != 1 or contents.count(END) != 1:
        raise StarredError("README starred-section markers missing or duplicated")
    start = contents.index(START) + len(START)
    end = contents.index(END)
    if start >= end:
        raise StarredError("README starred-section markers out of order")
    return contents[:start] + "\n" + table + "\n" + contents[end:]


def main() -> None:
    if not README.is_file() or README.is_symlink():
        raise StarredError("Unsafe or missing README.md")
    old = README.read_text(encoding="utf-8")
    new = update_readme(old, render_starred(fetch_starred()))
    if new == old:
        print("Public starred repositories unchanged")
        return
    scratch: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=README.parent, prefix=".justsec-stars-",
            suffix=".tmp", delete=False,
        ) as file:
            scratch = file.name
            file.write(new)
            file.flush()
            os.fsync(file.fileno())
        os.replace(scratch, README)
    finally:
        if scratch and os.path.exists(scratch):
            os.unlink(scratch)
    print("Refreshed README with up to 8 public repositories starred by Lucatronlk")


if __name__ == "__main__":
    try:
        main()
    except (StarredError, OSError):
        raise SystemExit("Unable to refresh public stars; existing README kept") from None