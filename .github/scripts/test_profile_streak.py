"""Offline checks for profile-streak.py; run with python -m unittest discover."""

import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET


SPEC = importlib.util.spec_from_file_location("profile_streak", Path(__file__).with_name("profile-streak.py"))
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)
START = dt.date(2026, 1, 1)
TODAY = dt.date(2026, 1, 3)


def fixture(counts=(2, 1, 0)):
    return {
        "viewer": {"login": "Lucatronlk"},
        "user": {"contributionsCollection": {"contributionCalendar": {
            "weeks": [{"contributionDays": [
                {"date": (START + dt.timedelta(days=i)).isoformat(), "contributionCount": count}
                for i, count in enumerate(counts)
            ]}]
        }}},
    }


class CalendarTests(unittest.TestCase):
    def test_valid_and_streaks(self):
        counts = profile.daily_counts(fixture(), START, TODAY)
        self.assertEqual((2, 2), profile.streaks(counts))
        self.assertEqual((0, 2), profile.streaks([1, 1, 0, 0]))
        self.assertEqual((0, 0), profile.streaks([0, 0]))
        svg = profile.render_svg(counts, START, TODAY)
        self.assertIn("Current Streak", "".join(ET.fromstring(svg).itertext()))
        self.assertNotIn("http://", svg.replace('xmlns="http://www.w3.org/2000/svg"', ""))

    def test_whole_week_padding_is_ignored(self):
        data = fixture()
        weeks = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
        weeks[0]["contributionDays"][:0] = [
            {"date": "2025-12-28", "contributionCount": 99},
            {"date": "2025-12-29", "contributionCount": 99},
            {"date": "2025-12-30", "contributionCount": 99},
            {"date": "2025-12-31", "contributionCount": 99},
        ]
        self.assertEqual([2, 1, 0], profile.daily_counts(data, START, TODAY))
        end = dt.date(2026, 1, 6)
        weeks.append({"contributionDays": [
            {"date": (dt.date(2026, 1, 4) + dt.timedelta(days=i)).isoformat(),
             "contributionCount": i + 1} for i in range(7)
        ]})
        self.assertEqual([2, 1, 0, 1, 2, 3], profile.daily_counts(data, START, end))

    def test_reject_missing_duplicate_and_outlandish_days(self):
        for value in (None, {}):
            with self.subTest(calendar=value), self.assertRaises(profile.StreakError):
                data = fixture()
                data["user"]["contributionsCollection"]["contributionCalendar"] = value
                profile.daily_counts(data, START, TODAY)
        with self.assertRaises(profile.StreakError):
            data = fixture()
            del data["user"]["contributionsCollection"]["contributionCalendar"]
            profile.daily_counts(data, START, TODAY)
        for extra in ("2025-12-27", "2026-01-04", "2000-01-01", "9999-12-31"):
            with self.subTest(extra=extra), self.assertRaises(profile.StreakError):
                data = fixture()
                data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"].append(
                    {"contributionDays": [{"date": extra, "contributionCount": 0}]}
                )
                profile.daily_counts(data, START, TODAY)
        data = fixture()
        days = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"][0]["contributionDays"]
        days.pop(1)
        days.append({"date": "2025-12-31", "contributionCount": 1})
        with self.assertRaises(profile.StreakError):
            profile.daily_counts(data, START, TODAY)
        for repeated in ("2025-12-28", START.isoformat()):
            with self.subTest(repeated=repeated), self.assertRaises(profile.StreakError):
                data = fixture()
                days = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"][0]["contributionDays"]
                days.extend([{"date": repeated, "contributionCount": 0}] * 2)
                profile.daily_counts(data, START, TODAY)
        data = fixture()
        data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"].extend(
            {"contributionDays": []} for _ in range(100)
        )
        with self.assertRaises(profile.StreakError):
            profile.daily_counts(data, START, TODAY)

    def test_utc_query_and_calendar_selection(self):
        self.assertIn("contributionCalendar { weeks { contributionDays { date contributionCount } } }", profile.QUERY)

        class FakeConnection:
            def __init__(self, host, timeout):
                self.host, self.timeout = host, timeout
            def request(self, method, path, body, headers):
                type(self).body = json.loads(body)
            def getresponse(self):
                return self
            status = 200
            def read(self, size):
                return json.dumps({"data": fixture()}).encode("utf-8")
            def close(self):
                pass

        with patch.object(profile.http.client, "HTTPSConnection", FakeConnection):
            data = profile.fetch_calendar("test-token", START, TODAY)
            self.assertEqual([2, 1, 0], profile.daily_counts(data, START, TODAY))
            self.assertEqual("2026-01-01T00:00:00Z", FakeConnection.body["variables"]["from"])
            self.assertEqual("2026-01-03T23:59:59Z", FakeConnection.body["variables"]["to"])

    def test_reject_bad_calendar(self):
        bad = fixture()
        bad["viewer"]["login"] = "other"
        with self.assertRaises(profile.StreakError):
            profile.daily_counts(bad, START, TODAY)
        for counts in ((1, 1), (1, True, 0), (1, -1, 0)):
            with self.subTest(counts=counts), self.assertRaises(profile.StreakError):
                profile.daily_counts(fixture(counts), START, TODAY)
        bad = fixture()
        bad["user"]["contributionsCollection"]["contributionCalendar"]["weeks"][0]["contributionDays"].append(
            {"date": START.isoformat(), "contributionCount": 1}
        )
        with self.assertRaises(profile.StreakError):
            profile.daily_counts(bad, START, TODAY)

    def test_http_rejects_partial_errors_and_redirects(self):
        class FakeResponse:
            status = 200
            def read(self, size):
                return b'{"errors":[{"message":"denied"}],"data":{"viewer":{"login":"lucatronlk"}}}'

        class FakeConnection:
            def __init__(self, host, timeout):
                self.host, self.timeout = host, timeout
            def request(self, method, path, body, headers):
                self.method, self.path = method, path
            def getresponse(self):
                return FakeResponse()
            def close(self):
                pass

        with patch.object(profile.http.client, "HTTPSConnection", FakeConnection):
            with self.assertRaises(profile.StreakError):
                profile.fetch_calendar("test-token", START, TODAY)
            FakeResponse.status = 302
            with self.assertRaises(profile.StreakError):
                profile.fetch_calendar("test-token", START, TODAY)
            FakeResponse.status = 200
            FakeResponse.read = lambda self, size: b"x" * size
            with self.assertRaises(profile.StreakError):
                profile.fetch_calendar("test-token", START, TODAY)

    def test_http_success_and_missing_data(self):
        class FakeConnection:
            def __init__(self, host, timeout):
                self.host, self.timeout = host, timeout
            def request(self, method, path, body, headers):
                self.method, self.path = method, path
                self.headers = headers
            def getresponse(self):
                return self
            status = 200
            def read(self, size):
                return self.payload
            def close(self):
                pass

        with patch.object(profile.http.client, "HTTPSConnection", FakeConnection):
            FakeConnection.payload = b'{"data":{"viewer":{"login":"lucatronlk"}}}'
            self.assertEqual(profile.fetch_calendar("test-token", START, TODAY)["viewer"]["login"], "lucatronlk")
            FakeConnection.payload = b'{"data":null}'
            with self.assertRaises(profile.StreakError):
                profile.fetch_calendar("test-token", START, TODAY)
            FakeConnection.payload = b'{bad json}'
            with self.assertRaises(profile.StreakError):
                profile.fetch_calendar("test-token", START, TODAY)

    def test_main_uses_utc_date_without_network(self):
        real_datetime = dt.datetime
        case = self

        class FixedDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                case.assertIs(tz, dt.timezone.utc)
                return real_datetime(2026, 1, 3, 0, 5, tzinfo=tz)

        with patch.dict(profile.os.environ, {"PROFILE_STATS_TOKEN": "offline-placeholder"}), patch.object(
            profile.dt, "datetime", FixedDatetime
        ), patch.object(profile, "fetch_calendar", side_effect=profile.StreakError("offline")) as fetch:
            with self.assertRaises(profile.StreakError):
                profile.main()
            fetch.assert_called_once_with("offline-placeholder", dt.date(2025, 1, 4), TODAY)

    def test_missing_token_no_network(self):
        with patch.dict(profile.os.environ, {}, clear=True), patch.object(
            profile, "fetch_calendar", side_effect=AssertionError("network called")
        ):
            with self.assertRaises(profile.StreakError):
                profile.main()


if __name__ == "__main__":
    unittest.main()
