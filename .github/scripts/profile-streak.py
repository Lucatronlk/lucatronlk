"""Generate a dark streak card from the authenticated viewer's GitHub calendar.

Run from the repository root with PROFILE_STATS_TOKEN set. Only writes
profile/streak.svg.tmp; caller must validate and publish the card separately.
"""

from __future__ import annotations

import datetime as dt
import html
import http.client
import json
import os
from pathlib import Path
import tempfile


LOGIN = "lucatronlk"
HOST = "api.github.com"
OUTPUT = Path("profile/streak.svg.tmp")
MAX_RESPONSE = 1_000_000
QUERY = """query($login: String!, $from: DateTime!, $to: DateTime!) {
  viewer { login }
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar { weeks { contributionDays { date contributionCount } } }
    }
  }
}"""


class StreakError(Exception):
    """Calendar cannot be trusted or generated."""


def fetch_calendar(token: str, start: dt.date, today: dt.date) -> dict:
    """Fetch one bounded calendar; do not follow redirects or log responses."""
    variables = {
        "login": LOGIN,
        "from": f"{start.isoformat()}T00:00:00Z",
        "to": f"{today.isoformat()}T23:59:59Z",
    }
    body = json.dumps({"query": QUERY, "variables": variables}).encode("utf-8")
    conn = http.client.HTTPSConnection(HOST, timeout=15)
    try:
        conn.request(
            "POST", "/graphql", body=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "lucatronlk-profile-streak",
            },
        )
        response = conn.getresponse()
        if response.status != 200:
            raise StreakError("GitHub calendar request failed")
        payload = response.read(MAX_RESPONSE + 1)
        if len(payload) > MAX_RESPONSE:
            raise StreakError("GitHub calendar response too large")
        result = json.loads(payload)
        if not isinstance(result, dict) or result.get("errors") or not isinstance(result.get("data"), dict):
            raise StreakError("GitHub calendar returned errors or missing data")
        return result["data"]
    except (OSError, ValueError, RecursionError, http.client.HTTPException):
        raise StreakError("GitHub calendar request or response invalid") from None
    finally:
        conn.close()


def daily_counts(data: dict, start: dt.date, today: dt.date) -> list[int]:
    """Accept whole-week padding, but require every requested UTC date once."""
    try:
        if data["viewer"]["login"].lower() != LOGIN:
            raise StreakError("Token viewer does not match profile owner")
        calendar = data["user"]["contributionsCollection"]["contributionCalendar"]
        if not isinstance(calendar, dict):
            raise StreakError("Contribution calendar unavailable")
        weeks = calendar["weeks"]
        window_days = (today - start).days + 1
        if window_days < 1 or not isinstance(weeks, list) or len(weeks) > (window_days + 18) // 7:
            raise StreakError("Invalid calendar weeks")
        # GitHub weeks run Sunday through Saturday; only those edge dates are padding.
        lower = start.toordinal() - (start.weekday() + 1) % 7
        upper = today.toordinal() + (5 - today.weekday()) % 7
        counts: dict[dt.date, int] = {}
        seen: set[dt.date] = set()
        for week in weeks:
            days = week["contributionDays"]
            if not isinstance(days, list) or len(days) > 7:
                raise StreakError("Invalid calendar days")
            for day in days:
                date = dt.date.fromisoformat(day["date"])
                count = day["contributionCount"]
                if type(count) is not int or count < 0 or not lower <= date.toordinal() <= upper or date in seen:
                    raise StreakError("Invalid or repeated contribution day")
                seen.add(date)
                if start <= date <= today:
                    counts[date] = count
        expected = [start + dt.timedelta(days=n) for n in range(window_days)]
        if len(counts) != window_days or any(date not in counts for date in expected):
            raise StreakError("Calendar dates incomplete")
        return [counts[date] for date in expected]
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise StreakError("Invalid GitHub calendar structure") from None


def streaks(counts: list[int]) -> tuple[int, int]:
    """Return current (including yesterday if today is empty) and window best."""
    run = best = 0
    for count in counts:
        run = run + 1 if count > 0 else 0
        best = max(best, run)
    current = run
    if counts and counts[-1] == 0:
        current = 0
        for count in reversed(counts[:-1]):
            if count == 0:
                break
            current += 1
    return current, best


def render_svg(counts: list[int], start: dt.date, today: dt.date) -> str:
    """Render text-only SVG without external references or repository names."""
    current, best = streaks(counts)
    title = html.escape(f"Current Streak in window: {current} days", quote=True)
    period = html.escape(f"{start.isoformat()} to {today.isoformat()} (UTC)", quote=True)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="480" height="150" viewBox="0 0 480 150" role="img" aria-label="{title}">
<title>{title}</title>
<rect width="480" height="150" rx="12" fill="#0d1117"/>
<text x="24" y="34" fill="#e6edf3" font-size="18" font-family="sans-serif">Current Streak (window)</text>
<text x="24" y="78" fill="#58a6ff" font-size="32" font-family="sans-serif">{current} days</text>
<text x="245" y="67" fill="#e6edf3" font-size="15" font-family="sans-serif">Best in window: {best} days</text>
<text x="245" y="91" fill="#8b949e" font-size="14" font-family="sans-serif">Contributions: {sum(counts)}</text>
<text x="24" y="129" fill="#8b949e" font-size="12" font-family="sans-serif">Window: {period}</text>
</svg>
'''


def main() -> None:
    token = os.environ.get("PROFILE_STATS_TOKEN")
    if not token or not token.strip():
        raise StreakError("PROFILE_STATS_TOKEN is required")
    today = dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=364)
    counts = daily_counts(fetch_calendar(token, start, today), start, today)
    svg = render_svg(counts, start, today)
    # Profile directory is provisioned by the caller. Never create or publish cards here.
    if not OUTPUT.parent.is_dir() or OUTPUT.parent.is_symlink() or OUTPUT.is_symlink():
        raise StreakError("Profile output directory missing or unsafe")
    scratch: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=OUTPUT.parent,
                                         prefix=".streak-", suffix=".tmp", delete=False) as file:
            scratch = file.name
            file.write(svg)
            file.flush()
            os.fsync(file.fileno())
        os.replace(scratch, OUTPUT)
    finally:
        if scratch and os.path.exists(scratch):
            os.unlink(scratch)


if __name__ == "__main__":
    try:
        main()
    except (StreakError, OSError):
        raise SystemExit("Streak card generation failed; check token, calendar and output directory") from None
