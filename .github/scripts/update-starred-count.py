"""Refresh only the public starred-repository count in README.md.

The starred list itself is curated by hand; this keeps the headline number
honest. The count comes from the Link header's last page rather than paging
every repository, so it stays cheap and accurate for any list size.
"""

from __future__ import annotations

import http.client
import os
from pathlib import Path
import re
import tempfile

README = Path("README.md")
HOST = "api.github.com"
PATH = "/users/Lucatronlk/starred?per_page=1"
OPEN = "<!--STARRED_COUNT-->"
CLOSE = "<!--/STARRED_COUNT-->"
MAX_COUNT = 100_000
LAST_PAGE = re.compile(r'[?&]page=(\d+)>;\s*rel="last"')


class StarredCountError(Exception):
    """API response or README structure failed verification."""


def fetch_count() -> int:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Lucatronlk-justsec-profile",
    }
    token = os.environ.get("STARRED_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn = http.client.HTTPSConnection(HOST, timeout=15)
    try:
        conn.request("GET", PATH, headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            raise StarredCountError(f"GitHub star lookup returned HTTP {response.status}")
        link = response.getheader("Link") or ""
        body = response.read(4096)
    except (OSError, http.client.HTTPException) as error:
        raise StarredCountError("GitHub star lookup failed") from error
    finally:
        conn.close()

    match = LAST_PAGE.search(link)
    if match:
        count = int(match.group(1))
    elif link == "" and body.lstrip().startswith(b"["):
        count = 1 if body.lstrip().startswith(b"[{") else 0
    else:
        raise StarredCountError("Unexpected GitHub star response")
    if not 0 <= count <= MAX_COUNT:
        raise StarredCountError("Implausible starred count")
    return count


def update(contents: str, count: int) -> str:
    if contents.count(OPEN) != 1 or contents.count(CLOSE) != 1:
        raise StarredCountError("starred count markers missing or duplicated")
    start = contents.index(OPEN) + len(OPEN)
    end = contents.index(CLOSE)
    if start > end:
        raise StarredCountError("starred count markers out of order")
    return contents[:start] + str(count) + contents[end:]


def main() -> None:
    if not README.is_file() or README.is_symlink():
        raise StarredCountError("Unsafe or missing README.md")
    old = README.read_text(encoding="utf-8")
    new = update(old, fetch_count())
    if new == old:
        print("Starred count unchanged")
        return
    scratch: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=README.parent,
            prefix=".justsec-count-", suffix=".tmp", delete=False,
        ) as file:
            scratch = file.name
            file.write(new)
            file.flush()
            os.fsync(file.fileno())
        os.replace(scratch, README)
    finally:
        if scratch and os.path.exists(scratch):
            os.unlink(scratch)
    print("Refreshed starred repository count")


if __name__ == "__main__":
    try:
        main()
    except (StarredCountError, OSError):
        raise SystemExit("Unable to refresh starred count; existing README kept") from None
