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

## 2026-09-05 — Adjusted prices violate the bar invariant by one part in 10^16

The first backtest run failed immediately on SPY, 2007-10-26:

```
close 108.84169006347656 outside [107.62304766194698, 108.84169006347655]
```

SPY closed at its high that day. Adjusted prices are the raw prices multiplied by a
dividend factor in float64, and that multiplication does not distribute exactly, so the
adjusted close came out one unit in the last place *above* the adjusted high. The
domain model's `Bar` validation caught it, correctly: a close above the high is
impossible.

**The fix is at the boundary, not in the invariant.** Prices are now quantized to eight
decimal places as they leave the store and become `Bar` objects. Loosening the
validation to a tolerance was the alternative and was rejected — the invariant is real
and worth enforcing exactly; what needed fixing was that the domain was being handed
seventeen significant digits of float noise in the first place.

Worth noticing what this says about the data. The discrepancy is 10^-14 dollars, so
nothing about any result changes. But it is direct evidence that adjusted prices are
computed rather than observed, which is the same fact that makes them get restated
every time a dividend is paid.

## 2026-09-05 — A daily bar is stamped before the day it describes

`Bar.ts` is documented as the instant the bar closed. It was not. Daily bars arrive
from every data source dated to the calendar day, which the store localises to midnight
UTC — thirteen and a half hours *before* the NYSE opens that morning.

Nothing was broken by it. The engine only reads prices at session closes, where a bar
stamped 00:00 and one stamped 21:00 are both legitimately visible. But the guarantee
the whole design rests on was holding by accident: had anything asked the feed for a
price at a session *open*, it would have been handed that day's closing price, and the
backtest would have been reading the future with no test failing.

Bars are now re-stamped to their session close as they are loaded into the engine's
feed. The live loop in Phase 5 has decision points the backtest does not, and this was
a trap sitting directly in its path.

**The general lesson**, which is the reason this is written down: a safety property
that holds because of what the code happens not to do yet is not a safety property. It
is a coincidence with a good reputation.

## 2026-09-05 — A per-position no-trade band orders things it cannot pay for

The first 60/40 backtest reported sixteen fills and **fifty-five rejected orders**,
every one of them "insufficient cash". The rejections were the system working; the
orders were the bug.

A no-trade band applied per position asks, for each holding independently, whether it
has drifted far enough to be worth trading. That looks obviously right and is wrong in
a fully invested portfolio, where a purchase is funded by a sale. On 2010-04-01 the
book was 12% off target in SPY — inside the band — and 21% off in IEF, outside it. So
the rebalancer ordered bonds and ordered no equities to pay for them.

**The band now decides whether to rebalance, not which legs to trade.** If any position
breaches it, every position is restored to target. Turnover is unchanged, because
rebalances are exactly as rare as before, but each trade set is self-funding by
construction.

**The part worth dwelling on: the bug flattered the results.** 60/40 over 2007–2026
returned 8.81% annualised with the bug and 8.39% without it. The failure mode was
systematically declining to buy bonds while continuing to sell them, which over a
period ending in a long equity bull market left the portfolio quietly overweight the
thing that went up. Forty-two basis points a year, in the right direction, from a bug
whose only visible symptom was a rejection count in a table nobody had to look at.

This is the second time in two days that the useful signal came from a diagnostic that
was easy to ignore — the first was `data check`'s flatline warning on BIL. Both suggest
the same thing: counts of anomalies are worth reading even when the headline number
looks reasonable, and probably especially then.

## 2026-09-05 — The repository did not contain the data package

`git status` showed `src/sillage/data/store.py` as unmodified after an edit that was
plainly there in the file. It was not tracked. Nothing under `src/sillage/data/` ever
had been.

`.gitignore` carried `data/` to keep market data and run artifacts out of the
repository. A gitignore pattern with no leading slash matches a directory of that name
at *any* depth, so it also matched the `data` **package** — the store, the providers,
the universe, the quality checks. Phase 0's two commits describe a data layer they do
not contain, and a fresh clone would fail on `import sillage.data` before reaching a
test.

Anchored to `/data/` and `/reports/`, which is what was meant.

**Why it went unnoticed for two days:** every check that could have caught it runs
against the working tree, and the working tree was complete. CI would have caught it on
the first push, but there had been no push. There is a general shape here — a class of
error invisible to every local check, waiting on an action nobody had taken yet — and
the cheap defence is to push early rather than to add another local check.

**And a second-order effect worth the note.** Ruff honours `.gitignore` by default, so
for two days it had not linted the data package either — the moment those files became
tracked, `make lint` found two things in them it had never looked at. Mypy has no such
behaviour and had been checking them all along, which is why this surfaced as a
formatting complaint rather than anything worse. One ignored directory quietly
disabling a tool on a quarter of the source tree is a good argument for verifying
against a fresh clone rather than the directory you have been working in.
