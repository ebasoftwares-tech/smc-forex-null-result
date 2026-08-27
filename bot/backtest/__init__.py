"""The backtest engine (Phase 14).

Split three ways, along the line that decides which numbers are portfolio-dependent:

* ``engine`` — turns a setup stream into trades, rejections and shadow trades.
* ``metrics`` — BACKTEST_PROTOCOL section 4's headline figures and breakdowns.
* ``montecarlo`` — section 9's five resampling tests.
"""
