from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase

from app.main import _visible_on_daily_news


class DailyNewsDayScopeTests(TestCase):
    today = date(2026, 8, 27)

    def _entry(self, *, report_date: date, published_at) -> SimpleNamespace:
        return SimpleNamespace(report_date=report_date, published_at=published_at)

    def test_today_keeps_today_snapshot(self) -> None:
        entry = self._entry(
            report_date=self.today,
            published_at=datetime(2026, 8, 26, 18, 0),
        )
        self.assertTrue(
            _visible_on_daily_news(entry, view_day=self.today, today=self.today)
        )

    def test_today_keeps_yesterday_item_within_24h(self) -> None:
        entry = self._entry(
            report_date=self.today - timedelta(days=1),
            published_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        self.assertTrue(
            _visible_on_daily_news(entry, view_day=self.today, today=self.today)
        )

    def test_today_hides_older_than_24h_from_yesterday_snapshot(self) -> None:
        entry = self._entry(
            report_date=self.today - timedelta(days=1),
            published_at=datetime.now(timezone.utc) - timedelta(hours=30),
        )
        self.assertFalse(
            _visible_on_daily_news(entry, view_day=self.today, today=self.today)
        )

    def test_history_only_keeps_that_calendar_day(self) -> None:
        view_day = date(2026, 8, 25)
        same_day = self._entry(
            report_date=view_day,
            published_at=datetime(2026, 8, 25, 9, 0),
        )
        other_day = self._entry(
            report_date=view_day,
            published_at=datetime(2026, 8, 24, 21, 0),
        )
        self.assertTrue(_visible_on_daily_news(same_day, view_day=view_day, today=self.today))
        self.assertFalse(_visible_on_daily_news(other_day, view_day=view_day, today=self.today))
