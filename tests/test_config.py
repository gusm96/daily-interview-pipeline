from datetime import date, datetime
import config


def test_parse_iso_date_accepts_real_date():
    assert config.parse_iso_date("2026-08-10") == date(2026, 8, 10)


def test_parse_iso_date_rejects_fake_but_well_formed_date():
    # 정규식 \d{4}-\d{2}-\d{2}는 만족하지만 존재하지 않는 날짜.
    # 형식 검사가 아니라 실제 파싱으로 검증하는 이유가 이것이다.
    assert config.parse_iso_date("2026-13-99") is None


def test_parse_iso_date_rejects_garbage_without_raising():
    for bad in ["invalid", "2026_07_28", "forever", "", None, 20260728, True]:
        assert config.parse_iso_date(bad) is None


def test_shift_date_iso_crosses_month_end():
    assert config.shift_date_iso("2026-07-30", 3) == "2026-08-02"


def test_shift_date_iso_handles_leap_year():
    assert config.shift_date_iso("2028-02-27", 3) == "2028-03-01"


def test_shift_date_iso_zero_returns_same_day():
    assert config.shift_date_iso("2026-08-05", 0) == "2026-08-05"


def test_days_left_counts_today_inclusive():
    # 오늘이 마지막 정지일이면 남은 일수는 1(오늘 하루)
    assert config.days_left("2026-08-05", "2026-08-05") == 1
    assert config.days_left("2026-08-07", "2026-08-05") == 3


def test_days_left_forever_returns_none():
    assert config.days_left("forever", "2026-08-05") is None


def test_today_kst_iso_uses_kst_not_utc(monkeypatch):
    """Cloud Functions 런타임은 UTC라 date.today()를 쓰면 07:00 KST 실행이 전날로 찍힌다
    (2026-06-30 실관측). KST 2026-07-27 00:30 = UTC 2026-07-26 15:30 인 시점을 고정해
    날짜가 KST 기준으로 나오는지 확인한다."""
    fixed = datetime(2026, 7, 27, 0, 30, tzinfo=config.KST)
    monkeypatch.setattr(config, "_now_kst", lambda: fixed)
    assert config.today_kst_iso() == "2026-07-27"


def test_stop_n_last_day_computed_from_kst_today(monkeypatch):
    """정지 마지막 날 계산이 KST 오늘을 타는지 (UTC였다면 2026-07-28이 나온다)."""
    fixed = datetime(2026, 7, 27, 0, 30, tzinfo=config.KST)
    monkeypatch.setattr(config, "_now_kst", lambda: fixed)
    assert config.shift_date_iso(config.today_kst_iso(), 3 - 1) == "2026-07-29"
