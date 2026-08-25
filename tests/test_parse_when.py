from datetime import datetime

import click
import pytest

from bpq.cli import parse_when

NOW = datetime(2026, 8, 24, 20, 0)


def test_hhmm_today():
    assert parse_when("23:30", now=NOW) == datetime(2026, 8, 24, 23, 30)


def test_hhmm_already_passed_rolls_to_tomorrow():
    """20:00 提交「19:00」，指的是明天，不能立刻触发。"""
    assert parse_when("19:00", now=NOW) == datetime(2026, 8, 25, 19, 0)


def test_absolute():
    assert parse_when("2026-08-25 23:30", now=NOW) == datetime(2026, 8, 25, 23, 30)


def test_relative():
    assert parse_when("+2h", now=NOW) == datetime(2026, 8, 24, 22, 0)
    assert parse_when("+90m", now=NOW) == datetime(2026, 8, 24, 21, 30)


def test_garbage_rejected():
    with pytest.raises(click.BadParameter):
        parse_when("睡前", now=NOW)
