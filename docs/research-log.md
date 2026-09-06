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

## 2026-09-05 — Metrics agree with quantstats exactly, except where they should not

The metrics module is validated against `quantstats`, an independent implementation
nobody here had sight of. Sharpe, Sortino, volatility and maximum drawdown agree to
machine precision on a 3,000-day series. Sharpe in particular has several defensible
definitions differing by a few percent, and a figure nobody else can reproduce is not
evidence.

Two figures disagree by about 1.3 basis points, and the disagreement is kept: CAGR and
Calmar. quantstats divides elapsed days by 365; this divides by 365.25. Over a twenty
year sample containing five leap days, 365.25 is right. The test asserts agreement to
three decimals and says why, rather than adopting the other convention to make a
number match.

Separately, **"longest drawdown" is measured peak-to-recovery, not days-spent-below-
water.** The two differ by the length of the final leg, and the first is what the
question actually means: not "how many bad days" but "how long until I was whole
again". For SPY over this sample it is 1,773 days — peak 2007-10-09, trough 2009-03-09
at −55.1%, back to even 2012-08-16. Those are the real dates, which is the strongest
evidence so far that the whole pipeline is wired correctly.

## 2026-09-05 — Rebalance timing luck, measured before being fixed

A critique of the design named rebalance timing luck as its biggest fixable flaw, and
it is a real, documented effect (Hoffstein). A strategy rebalancing monthly has to pick
a day of the month; nothing makes the last session better than the third-to-last; and
two runs differing only in that choice can diverge by a percent a year or more. A
backtest on one date reports one draw from a distribution as if it were the answer.

The prescribed remedy is tranching — several sub-portfolios on staggered schedules,
averaged. It is cheap here because `target_weights` is already a pure function of
`as_of`.

**It is deliberately not built yet, and measuring first is why.** `Monthly(offset)` and
`sillage timing-luck` now run the same configuration across four dates a week apart.
On the benchmarks:

| strategy | CAGR spread across four dates |
|---|---|
| 60/40, rebalanced monthly | 0.11% |
| equal weight, 12 ETFs | 0.08% |

Near zero — which is exactly right, and is the point. **Timing luck is a property of
selection, not of rebalancing.** A fixed-weight strategy wants the same weights
whichever day it looks at, so the date can only matter at the margin. Building a
tranching implementation now would mean building it against strategies that cannot
exercise it. It belongs with the momentum strategy in Phase 3, where the effect exists.

What the run does establish is that the harness measures the strategy rather than
inventing variance of its own, which is the thing worth knowing before trusting a
larger number from it later.

**An unexpected second finding.** A single-asset 100% SPY book shows a *0.62%* annual
spread across the same four dates over 2010–2020 — six times the two-asset figure —
despite never rebalancing at all. With one asset there is nothing to rebalance against,
so the only thing the offset changes is which day the money went in. That is entry-date
luck, a different effect that happened to be sitting in the same measurement, and it is
larger here than the thing being measured. Worth separating deliberately: a backtest's
start date is an arbitrary choice too, and it is one nobody thinks to vary.

Drawdown turns out to be more date-sensitive than return even for the fixed-weight
book: 60/40's maximum drawdown ranges from −29.2% to −31.0% across the four dates,
a 1.8-point spread against a 0.11% spread in return. If a strategy is going to be sold
on its drawdown, the drawdown deserves the same treatment as the headline.

## 2026-09-06 — The strategy exists, and its headline drawdown was a lucky draw

Phase 3 built the dual-momentum strategy from §4 of the roadmap. Full results and the
argument about whether it is worth owning are in [strategy.md](strategy.md); this entry
records the findings that changed how the thing was built.

**Timing luck is real, and it is large.** Phase 2 measured it on fixed-weight benchmarks
and found 0.08–0.11% of annualised spread across four rebalance dates — near zero, which
was the expected answer, since a 60/40 wants the same 60/40 whichever day it looks. The
same measurement on the momentum strategy:

| Rebalance date | Annualised | Sharpe | Max drawdown |
|---|---|---|---|
| Month end | +7.16% | 0.83 | −16.0% |
| 5 sessions earlier | +7.22% | 0.76 | −28.3% |
| 10 sessions earlier | +6.25% | 0.69 | −25.2% |
| 15 sessions earlier | +8.34% | 0.94 | −18.1% |

**A 2.09-point spread in return and a drawdown ranging from −16% to −28%**, from a choice
with no meaning — nineteen times the benchmarks' spread. The prediction made in Phase 2,
that timing luck is a property of selection rather than of rebalancing, held exactly.

The consequence is uncomfortable and worth stating plainly: **had the strategy been
built and reported before the measurement existed, its headline maximum drawdown would
have been −16.0%, and that would have been the luckiest of four arbitrary dates.** The
tranched figure is −21.8%. Nothing was fitted, nothing was p-hacked, and the number
would still have been wrong by six points because of a choice nobody thinks of as a
choice.

Tranching costs nothing measurable: turnover went from 7.08x/yr to 7.13x, because the
four tranches' trades partly cancel before an order is produced. `momentum` is tranched
by default and `momentum-single` exists to reproduce the problem on demand.

**The edge survives its costs, and that was not obvious.** The strategy turns over about
3.5x a year in each direction — high, driven by the volatility target moving the whole
book monthly even when selection is unchanged. At five times the modelled costs it still
returns 6.64% at a Sharpe of 0.76, against 7.43% and 0.85 with free execution. Most
retail backtests die here; this one does not. The honest caveat is that all those rows
scale one model, so a cost model wrong in *shape* rather than level would pass this test
unnoticed — which is what Phase 5b is for.

**No single asset carries it.** All thirteen instruments made money and the largest
contributor is 17% of total profit. The attribution reconciles exactly to NAV growth,
which is also a decent end-to-end check on the accounting.

**A fixture bug worth recording, because it made a test meaningless without failing it.**
The first synthetic price paths for the momentum tests compounded at an exactly constant
rate. A constant compounding rate means a constant daily return, which means *zero*
measured volatility — so the inverse-volatility sizing under test was dividing by
floating-point rounding error, and the weights it produced were noise. Every assertion
still passed, because they were all about which assets got selected rather than how much
of them was held. Test data has to exercise the property under test, and "it passed" is
not evidence that it did.

## 2026-09-06 — What the strategy is actually worth, stated once

The result, after twenty-one years, honest costs and the timing luck removed: **Sharpe
0.83 against 0.79 for a 60/40 and 0.64 for the index, a maximum drawdown of −21.8%
against −31.2% and −55.1%, and the second-lowest return of the four.**

Two things about that are worth carrying forward rather than leaving in a table.

**The risk-adjusted edge over 60/40 does not survive the removal of 2008.** Post-2010 a
plain 60/40 returned 10.03% at a Sharpe of 1.00 against the strategy's 7.04% and 0.80 —
better on every measure including drawdown. The roadmap predicted this in §9 before any
code existed, which is mildly reassuring about the process and says nothing at all about
the strategy.

**Its worst drawdown is not in the crisis it was built for.** −21.8%, and it happened
after 2010. The trend filter did its job in 2008 and then cost money for a decade. A
strategy's worst moment being in the regime it handles *well* is a useful thing to know
about, because it is not what anyone expects when they buy it.
