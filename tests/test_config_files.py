"""Berkas YAML di repo harus valid dan JUJUR: semuanya masih berlabel belum terverifikasi."""

from __future__ import annotations

from pathlib import Path

from config.common import UNVERIFIED_LABEL
from config.market_rules import load_market_rules
from config.trading_calendar import load_trading_calendar
from config.watchlist import load_watchlist
from data.providers.global_macro import load_global_macro_config
from data.providers.news import load_news_sources

CONFIG = Path("config")


def test_all_repo_configs_load_and_are_unverified() -> None:
    rules = load_market_rules(CONFIG / "market_rules.yaml")
    cal = load_trading_calendar(CONFIG / "trading_calendar.yaml")
    wl = load_watchlist(CONFIG / "watchlist.yaml")
    macro = load_global_macro_config(CONFIG / "global_macro.yaml")
    news = load_news_sources(CONFIG / "news_sources.yaml")
    for cfg in (rules, cal, wl, macro, news):
        assert cfg.meta.verified is False
    assert rules.label == UNVERIFIED_LABEL and cal.label == UNVERIFIED_LABEL
    assert "CONTOH" in rules.meta.label and "CONTOH" in cal.meta.label


def test_watchlist_symbols_canonical_and_unique() -> None:
    wl = load_watchlist(CONFIG / "watchlist.yaml")
    assert wl.codes and len(set(wl.codes)) == len(wl.codes)
    assert all(code == code.upper() and not code.endswith(".JK") for code in wl.codes)
