"""Refresh the public repositories starred by Lucatronlk in README.md.

The list is ranked by stars plus forks, so the most useful references surface
first rather than the most recently starred. No third-party renderer is needed.
All links point to validated github.com/OWNER/REPO paths, and API-provided
descriptions are escaped. If the API is unavailable, a curated fallback list is
rendered instead of leaving the section empty.
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
BASE_PATH = "/users/Lucatronlk/starred?per_page=100&page="
PAGES = 3
START = "<!-- JUSTSEC_STARRED_START -->"
END = "<!-- JUSTSEC_STARRED_END -->"
MAX_BYTES = 2_000_000
MAX_ITEMS = 8
MAX_ITEMS_CAP = 12
REPO_NAME = re.compile(r"[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+\Z")

# Curated fallback: used only when the live ranking cannot be fetched.
FALLBACK: tuple[tuple[str, str, str], ...] = (
    ("aquasecurity/trivy", "Vulnerability, misconfiguration and secret scanner for images and IaC", "Go"),
    ("gitleaks/gitleaks", "Secret scanning for git history and working trees", "Go"),
    ("trufflesecurity/trufflehog", "Credential discovery across code, CI and artifacts", "Go"),
    ("open-policy-agent/opa", "Policy engine for admission, CI and infrastructure guardrails", "Go"),
    ("sigstore/cosign", "Container signing and signature verification", "Go"),
    ("anchore/syft", "SBOM generation for container images and filesystems", "Go"),
    ("semgrep/semgrep", "Static analysis for security rules across many languages", "OCaml"),
    ("ossf/scorecard", "Automated checks against open source supply chain practice", "Go"),
)


class StarredError(Exception):
    """API response or README structure failed verification."""


def fetch_page(page: int) -> list[dict]:
    """Fetch one page of the public star feed from the fixed API hostname."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Lucatronlk-justsec-profile",
    }
    token = os.environ.get("STARRED_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn = http.client.HTTPSConnection(HOST, timeout=20)
    try:
        conn.request("GET", f"{BASE_PATH}{page}", headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            raise StarredError(f"GitHub public star lookup returned HTTP {response.status}")
        payload = response.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise StarredError("GitHub public star response exceeded size limit")
        data = json.loads(payload)
        if not isinstance(data, list) or len(data) > 100:
            raise StarredError("Unexpected GitHub public star response")
        return data
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise StarredError("GitHub public star lookup failed") from error
    finally:
        conn.close()


def fetch_starred() -> list[dict]:
    items: list[dict] = []
    for page in range(1, PAGES + 1):
        batch = fetch_page(page)
        items.extend(batch)
        if len(batch) < 100:
            break
    return items


def safe_text(value: object, max_length: int = 125) -> str:
    if not isinstance(value, str):
        return "—"
    value = " ".join(value.split())[:max_length].strip()
    if not value:
        return "—"
    # HTML escaping plus neutralizing Markdown table and inline-link syntax.
    escaped = html.escape(value, quote=True)
    return escaped.replace("|", "&#124;").replace("[", "&#91;").replace("]", "&#93;").replace("`", "&#96;")


def score(repo: dict) -> tuple[int, int]:
    stars = repo.get("stargazers_count")
    forks = repo.get("forks_count")
    stars = stars if isinstance(stars, int) else 0
    forks = forks if isinstance(forks, int) else 0
    return stars + forks, stars


def rank(items: list[dict]) -> list[dict]:
    """Return public, well-formed repositories ordered by stars plus forks."""
    seen: set[str] = set()
    ranked: list[tuple[tuple[int, int], dict]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        repo = raw.get("repo", raw)
        if not isinstance(repo, dict) or repo.get("private") is not False:
            continue
        name = repo.get("full_name")
        if not isinstance(name, str) or not REPO_NAME.fullmatch(name) or name.lower() in seen:
            continue
        seen.add(name.lower())
        ranked.append((score(repo), repo))
    ranked.sort(key=lambda pair: pair[1]["full_name"].lower())
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [repo for _, repo in ranked]


def limit() -> int:
    raw = os.environ.get("STARRED_LIMIT", "")
    if raw.isdigit() and int(raw) > 0:
        return min(int(raw), MAX_ITEMS_CAP)
    return MAX_ITEMS


def render(rows: list[tuple[str, str, str, object, object]], fallback: bool) -> str:
    lines = [
        "Public repositories I've starred, ranked by **stars + forks**.",
        "",
        "| Repository | Description | Language | Stars | Forks |",
        "| :--- | :--- | :--- | ---: | ---: |",
    ]
    for name, description, language, stars, forks in rows:
        lines.append(
            f"| [{name}](https://github.com/{name}) | {description} | {language} | {stars} | {forks} |"
        )
    if fallback:
        lines += ["", "<sub>Curated fallback — live ranking unavailable on the last refresh.</sub>"]
    return "\n".join(lines)


def build_table() -> str:
    try:
        ranked = rank(fetch_starred())[: limit()]
    except StarredError:
        ranked = []
    if ranked:
        rows = [
            (
                repo["full_name"],
                safe_text(repo.get("description")),
                safe_text(repo.get("language"), max_length=32),
                repo.get("stargazers_count") or 0,
                repo.get("forks_count") or 0,
            )
            for repo in ranked
        ]
        return render(rows, fallback=False)
    rows = [
        (name, safe_text(description), safe_text(language, max_length=32), "—", "—")
        for name, description, language in FALLBACK[: limit()]
        if REPO_NAME.fullmatch(name)
    ]
    if not rows:
        return "_No public starred repositories are currently available._"
    return render(rows, fallback=True)


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
    new = update_readme(old, build_table())
    if new == old:
        print("Public starred repositories unchanged")
        return
    scratch: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=README.parent, prefix=".justsec-stars-",
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
    print(f"Refreshed README starred list (top {limit()} by stars + forks)")


if __name__ == "__main__":
    try:
        main()
    except (StarredError, OSError):
        raise SystemExit("Unable to refresh public stars; existing README kept") from None
