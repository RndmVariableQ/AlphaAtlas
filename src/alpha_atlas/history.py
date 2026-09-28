"""Internal one-calendar-year warmup dates; not a window or history cap."""

from datetime import date, timedelta


def one_year_before(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def warmup_period(train_start: date) -> dict:
    return {
        "start": one_year_before(train_start).isoformat(),
        "end": (train_start - timedelta(days=1)).isoformat(),
    }
