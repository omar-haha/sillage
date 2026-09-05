# Research log

Dated findings, including the ones that went nowhere. Kept because a strategy is only
as trustworthy as the record of what was tried before it looked good — a result that
survived twenty untracked experiments is not the same as one that worked first time,
and there is no way to tell them apart after the fact.

---

## 2026-09-05 — First data sync, and a flatline that turned out to be real

Synced 21.7 years of adjusted daily bars for the 13-symbol `core` universe from Yahoo.
Zero missing sessions against the NYSE calendar and zero price jumps above 25% — better
than expected for a free source.

`data check` raised one warning: **BIL showed 18 consecutive sessions with an unchanged
close.** A flatline is normally the signature of a stale or forward-filled feed, so it
was worth chasing.

It is real. The run ends 2011-08-17, in the middle of the US debt-ceiling standoff and
the S&P downgrade. Short-term Treasury yields went to zero and briefly negative; BIL
holds 1–3 month T-bills, so with no yield to accrue its NAV genuinely did not move for
a month. Price sat at 73.2407 with two one-tick blips either side.

**Consequences.**
- The check is behaving correctly and stays as-is. It flagged an anomaly, and a human
  established it was a property of the asset rather than of the data. Auto-suppressing
  flatlines for cash-like instruments would have hidden a genuinely useful signal.
- Worth remembering when the strategy is running: during zero-rate periods the cash
  proxy earns nothing, so the trend filter's "retreat to safety" is capital preservation
  only, with no carry. Any backtest spanning 2009–2015 or 2020–2021 gets a materially
  different cash return than one spanning 2005–2007 or 2023–2026.

## 2026-09-05 — exchange_calendars silently defaults to a 20-year window

`exchange_calendars.get_calendar("XNYS")` with no bounds builds sessions only for
roughly the last 20 years. Today that means the calendar began in September 2006, so
every 2005 bar looked like it fell on a non-session.

It failed loudly with `DateOutOfBounds` rather than quietly reporting hundreds of
missing sessions, which is the good outcome. Bounds are now set explicitly
(`CALENDAR_START = 1990-01-01`, end rounded to two years ahead so the cache key stays
stable across days).

**Lesson worth carrying:** this class of bug — a library defaulting to a window that
happens to exclude the history you care about — would have been nearly invisible if the
library had returned an empty result instead of raising. The gap check in
`data/quality.py` is the backstop for the version of this that does not raise.
