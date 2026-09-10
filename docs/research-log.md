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

## 2026-09-06 — Validation gave the opposite answer depending on how it was run

The held-out test, run on a single rebalance date:

| | in sample (2005–2018) | held out (2018–2026) |
|---|---|---|
| Sharpe | 0.77 | **0.86** |

Improved out of sample. The same test on the tranched strategy — same split, same
parameters, same data:

| | in sample | held out |
|---|---|---|
| Sharpe | 0.87 | **0.73** |
| Max drawdown | −10.9% | −21.8% |

Degraded out of sample, and the drawdown doubled. **Two opposite conclusions from the
same experiment**, and the only difference is whether the rebalance date was one
arbitrary choice or an average of four.

The mechanism is Phase 2's finding compounding. A single-date run carries about two
percentage points of timing luck; splitting the sample in half roughly doubles the noise
in each piece, because each half has half the sessions to average it out. The result is
a comparison where the noise is larger than the effect being measured.

**Nothing that varies a parameter can be trusted on an untranched strategy.** Every
sweep in this phase — sensitivity, start dates, costs, the grid — would otherwise be
measuring which rebalance dates happened to be lucky for each configuration. The
`validate` command therefore tranches by default and `--single` exists only to reproduce
the problem. It is four times slower and it is not optional.

The honest reading of the tranched result: the strategy got modestly worse on data it
had not seen. It is not a collapse — Sharpe 0.73 still beats buy-and-hold SPY's 0.64
over the same span — but it is a decline, and it should be reported as one. The split
itself is also a free parameter nobody counts: cutting at 2018 puts 2008, the strategy's
single best year, entirely in the training half, and 2020, one of its worst, entirely in
the held-out half. A different boundary would tell a different story, and I have not run
one.

## 2026-09-06 — A sensitivity table that could not show what it was measuring

The no-trade band swept from 0.05 to 0.50 produced this:

```
band     0.05   0.10   0.20   0.30   0.50
Sharpe   0.83   0.83   0.83   0.83   0.83
CAGR    +7.3%  +7.3%  +7.3%  +7.3%  +7.3%
maxDD    -22%   -22%   -22%   -22%   -22%
```

Which reads unambiguously as "this parameter does nothing", and is wrong. Widening the
band from 0.05 to 0.50 removes **1,161 trades** — a fifth of them. What it does not
remove is turnover:

| band | fills | turnover | costs |
|---|---|---|---|
| 0.00 | 6,134 | 7.25x | $7,025 |
| 0.20 | 5,328 | 7.13x | $6,710 |
| 0.90 | 3,966 | 6.69x | $5,925 |

**The band controls the number of trades, not the amount traded.** It removes the small
ones, and the small ones are not where the money goes. Turnover here is driven by
monthly re-selection — five holdings out of twelve, rotating — and by the volatility
scalar moving every weight at once each month. Neither is something a drift band can
prevent, and a 500-dollar cost difference over twenty-one years on a hundred thousand
does not move a Sharpe ratio to two decimal places.

**The failure was mine, not the strategy's.** A sensitivity sweep that reports only
return metrics cannot show a parameter whose only effect is on trading, and I built one
and then read "flat" as "irrelevant". Every sensitivity row now carries turnover
alongside Sharpe, CAGR and drawdown.

The finding underneath survives the correction and is worth keeping: the band is nearly
free to set anywhere, and the strategy's 7x annual turnover is inherent to what it does
rather than a knob that was left in the wrong position. There is no tuning fix for it.

Adding the column immediately changed how three other sweeps read. Holding more assets
cuts turnover almost in half (top 3 → top 10: 7.84x to 4.38x); a longer volatility
window cuts it by a fifth at identical Sharpe (20 → 120 sessions: 8.43x to 6.76x). Both
were invisible in a table of return metrics, and both are the kind of thing that decides
whether a strategy is tradable at size.

## 2026-09-06 — What survived the attacks

Seventy-one configurations, all tranched.

**Plateaus, not spikes.** The trend window reads 0.78 / 0.83 / 0.83 / 0.82 / 0.77 across
100 to 300 days — a broad, gentle hill centred near the conventional 200, which is what a
real effect looks like. The volatility lookback is flatter still: 0.82 to 0.84 across
20 to 120 sessions, essentially indifferent. Neither value was chosen because it peaked,
because neither peaks.

**The lookback blend earns its place.** A single 12-month lookback gives 0.77; blending
3, 6 and 12 gives 0.83. That decision was made in Phase 3 on the argument that no single
lookback is right in every regime, before this was measured, and the measurement agrees.

**Two knobs are preferences, not optimisations.** Holding more assets raises Sharpe and
lowers return monotonically (top 3 → top 10: Sharpe 0.71 to 0.90, CAGR +6.9% to +5.4%),
and the volatility target does the same in reverse (6% → 15%: Sharpe 0.91 to 0.84, CAGR
+5.6% to +8.5%). Neither has an optimum to overfit to; both are dials between risk and
return, and the defaults sit in the middle of each. Worth stating plainly, because a
sensitivity sweep that shows a monotonic line is often misread as "the parameter is set
wrong".

**And here is the trap that reading invites.** Holding the top eight rather than five
gives a better Sharpe (0.90 against 0.83) *and* a third less turnover (5.47x against
7.13x) — better on both axes anyone would care about, worse only on raw return. It is
extremely tempting to move the default.

It stays at five. Choosing a parameter because it looked best across the whole sample is
precisely the thing this phase exists to detect in other people's work, and doing it here
would convert an inherited value into a fitted one — and would invalidate every deflation
figure in this entry, because those count trials on the assumption that the reported
configuration was not selected from among them. If the top-eight result is real it will
survive being tested on data chosen for the purpose, and that is a different experiment
run in a different order. Noted, not acted on.

**Start dates barely matter, once tranched.** Seven starts across a decade give Sharpes
of 0.80 to 0.86 and returns of 7.0% to 7.8%. Phase 2 found a single-asset book swinging
0.62% a year purely on entry date; four staggered tranches largely remove that too.

**The edge survives being deflated.** The bootstrapped Sharpe is 0.83 with a 95% interval
of 0.42 to 1.27 — wide, and clear of zero. Deflating for selection, the best of a
thousand random strategies would be expected to show 0.17; the probability the observed
result is not selection comes to 0.9986. That holds at the honest count of 71
configurations and at a pessimistic 1,000.

**What none of this establishes.** Every one of these tests asks whether the result is an
artefact of *this sample*. None of them can say the sample is representative of the
future, and the strategy's single best year remains one crisis in a window containing one
crisis. Passing this battery means the number is not obviously an illusion. It does not
mean it will happen again.

## 2026-09-09 — A fund that held nothing for twenty-one years and said 0.00%

Asked what the cash sleeve had returned, so that Sharpe ratios could be computed against
a real risk-free rate rather than zero, a buy-and-hold on BIL came back with **0.00% a
year**. Not an error. No rejected orders. Just a fund that finished exactly where it
started.

It had never traded. **BIL did not exist until May 2007**, and the backtest began in
January 2005. On the first session the `Once` schedule fired, the strategy looked for a
price, found none, and correctly returned "no opinion" — and the single firing that
`Once` grants was gone. It was never asked again.

The general fault is not about BIL. **A schedule that fires a fixed number of times can
spend one of them on a warm-up**, and nothing downstream can tell: the engine sees no
orders, the broker sees no orders, the journal records no rejections. The only symptom is
a plausible number.

Fixed by making the invariant explicit. `Schedule.defer()` hands a firing back, and the
engine calls it whenever a strategy declines the opportunity it was given. Stateless
schedules implement it as a no-op; `Once` un-fires. Nothing else changed, because
`Monthly` fires every month regardless — which is why the momentum strategy was never
affected and why this sat undiscovered through four phases.

**What it cost to find:** an unrelated question, asked for an unrelated reason. There was
no test that would have caught it, because every test used assets that existed on day one.

## 2026-09-09 — Why the holdings count stays at five

Phase 4 turned up an uncomfortable result: holding the top eight rather than five gave a
better Sharpe (0.90 against 0.83) *and* a third less turnover. It was left alone on the
grounds that changing a parameter because it looked best across the whole sample is the
thing this project exists to catch elsewhere. That was the right call for the wrong
reason, and the real reason is more interesting.

Realised volatility falls monotonically as the strategy holds more:

| top_n | 3 | 4 | 5 | 6 | 8 | 10 |
|---|---|---|---|---|---|---|
| realised vol | 10.1% | 9.5% | 9.0% | 8.5% | 7.1% | 6.0% |
| ex-ante vol | 12.5% | 11.3% | 10.3% | 9.5% | 8.1% | 7.4% |

At eight holdings the book's forecast volatility is 8.1%, *below* the 10% target — so the
volatility scaler wants to lever up, is capped at 1.0, and the fund runs at 7.1% instead.
**"Top eight is better" is mostly "top eight takes less risk"**, which is the one-sided
cap documented back in Phase 3 showing up somewhere new.

The decisive comparison is at matched risk:

| | realised vol | CAGR | Sharpe |
|---|---|---|---|
| top 8, 10% target | 7.1% | 6.32% | 0.80 |
| top 5, 7% target | 7.1% | 6.12% | 0.78 |

Two basis points of Sharpe, against a bootstrap interval of ±0.4. There is a real
diversification benefit in there and it is far too small to see. So: **bumping to eight
is defensible, but it is a risk reduction wearing a selection improvement's clothes**,
and the honest way to take it is to turn the volatility target down — a preference, not a
fitted parameter. Top five at a 6% target gives the best Sharpe of any configuration
tested (0.81 against a real cash rate) and costs a point and a half of return.

**A measurement correction that came out of the same investigation.** Every Sharpe in
this project is computed against a zero risk-free rate, which is the convention and is
also flattering to a strategy that parks a fifth of its capital in Treasury bills.
Against the cash sleeve's actual return the baseline moves from 0.83 to 0.76. That the
gap is small is luck: BIL's life is dominated by the zero-rate years, and cash averaged
0.66% a year. In a period like 2023-24 it would matter a great deal.

## 2026-09-09 — Where a Sharpe of 1.1 would have to come from

Combining strategies is the only reliable way to raise a Sharpe ratio, because the
arithmetic depends on correlation rather than on the quality of either part. Two sleeves
at 0.83 apiece combine to 1.17 if they are uncorrelated and 0.87 if they correlate at
0.8. Individual quality barely moves it.

Measured, on daily returns over the sample:

| | momentum | 60/40 | SPY | equal weight |
|---|---|---|---|---|
| momentum | 1.00 | 0.58 | 0.50 | 0.57 |
| 60/40 | 0.58 | 1.00 | 0.97 | 0.91 |

And the blend that correlation implies, built and run:

| | vol | CAGR | Sharpe |
|---|---|---|---|
| momentum | 9.0% | 7.27% | 0.83 |
| 60/40 | 10.9% | 8.35% | 0.79 |
| **half of each** | **8.7%** | **7.82%** | **0.91** |

Better than either sleeve on Sharpe, Sortino, Calmar, maximum drawdown and worst month —
*and* it returns more than the momentum sleeve alone, at half the turnover, because the
60/40 half barely trades. Their bad years are different ones: trend bleeds through long
calm bull markets, which is exactly when a static allocation compounds quietly.

**The ceiling is about 0.95, and it is set by correlation.** Everything long-only on
these thirteen ETFs correlates with everything else at 0.5 or more, because they are the
same assets. Adding a third or fourth long-only sleeve buys almost nothing. Getting to
1.1 needs a sleeve that is *structurally* different:

- **Market-neutral cross-sectional momentum** — long the strongest, short the weakest.
  Near-zero correlation to a long-only book by construction, and the engine already
  supports shorting (`SimulatedBroker(allow_short=True)`, and `Position` handles flips
  through zero). The cheapest real option.
- **A genuinely different asset class** — the crypto sleeve already planned for Phase 7.
- **A different holding period** — intraday or weekly, which needs data this project
  does not have.

A faster trend sleeve and a short-term reversal sleeve were considered and are not worth
building first: both still hold the same twelve ETFs long-only, so both land in the same
0.5-correlation trap. `Blend` is built and general, so any of these drops in when it
exists.

## 2026-09-09 — Three ways a live fund fails quietly

Phase 5a is meant to test the operational machinery rather than the strategy. It did,
immediately, and all three failures share a shape: the fund kept running and reported
numbers that looked fine.

**A risk limit that fought the strategy.** The default position cap was 35% of the fund.
A 60/40 wants 60% in equities. Every order it placed was refused, and the fund sat in
cash reporting a healthy NAV of exactly its starting capital. The cap was wrong in kind,
not degree: a position limit should be a *fault* threshold — in a long-only fund any
weight above 100% is a bug — not a portfolio constraint competing with the allocation.
Default raised to 1.0, with tightening left to whoever runs a particular fund.

**Nothing shouted.** The step reported "2 orders, 0 fills" and moved on. Finding out why
meant opening the database. Placing orders and filling none is the signature of nearly
every quiet live failure, so it now has its own flag and the runner says so in bold.

**Stale data was not detected.** The store's newest bar was days old and every read
succeeded, because stale market data is not an error — the files are there and every
price is a real price, just the wrong week's. A fund on a cron job would trade on them
every evening and report nothing unusual. `run_once` now refuses to act when the newest
bar is more than a few sessions behind.

None of the three were found by tests. All three were found by running the thing once and
reading what it said, which is roughly the argument for Phase 5 existing.

## 2026-09-09 — Building a broker adapter with no broker to test it against

Phase 5b was written before the IBKR account existed, which forces a question worth
answering deliberately: what can honestly be built and tested against nothing?

The answer turned out to be most of it, because the interesting part of a broker adapter
is not the protocol translation. It is the reasoning — when to resubmit, what to do with
an order that has not resolved, how to tell a refusal from a delay — and that reasoning
can be tested against a fake if the boundary is drawn in the right place.

So the boundary is a narrow `IBClient` protocol of eight methods, **normalised at the
edge**: `ib_async`'s types never reach the rest of the system. `IBGatewayClient` is the
only thing that touches the library, it contains no decisions, and it is consequently the
only part that cannot be tested. Everything with judgement in it sits in `IBKRBroker` and
runs against a fake venue that can do the things a simulator cannot — accept an order and
leave it working, fill one order in three pieces at three prices, cancel something after
accepting it.

**The design error that found.** Idempotency was originally checked two ways: open orders,
and today's executions. An order that IBKR accepted and then cancelled — a halt, a margin
failure — appears in *neither*. It is not working and it never executed. Only its status
remembers it happened, so the adapter would have placed it again on the next attempt,
every evening, forever. Found by writing the test for "an order the venue cancelled" and
watching it come back outstanding instead of rejected. There is now a third check.

**A structural change that came out of it.** `ExecutionReport` grew a third bucket.
Everything written before this point assumed an order resolves during the call that
submits it, because that is the only thing a simulator can do. A real venue accepts an
order and it works — for a second, or until the opening auction. Collapsing that into
"filled" loses the fill; collapsing it into "refused" sends the order twice. So
`outstanding` is now a first-class outcome, the engine puts those orders back into
pending rather than dropping them, and the simulated broker simply never produces any.

**And the one place live genuinely cannot mirror the backtest.** The engine's rule is
decide at the close, fill at the next open, and in a replay it satisfies that by
travelling forward and acting retroactively. A real market-on-open order has to be at the
exchange *before* the auction — it must be sent hours before the event that will execute
it. There is no way to make those identical, so a live run submits tonight's decisions
immediately against tomorrow's open, they come back outstanding because the market is
shut, and the next run recognises its own order references at the venue and collects
instead of resubmitting. It works precisely because the idempotency check exists, which
is a pleasant result: the property added for crash safety turned out to be what made the
order lifecycle possible at all.

**What is not established.** That any of this speaks the protocol correctly. A fake I
wrote agreeing with an adapter I wrote is a check on internal consistency, nothing more.
`sillage broker-check` against a live gateway is the first real evidence, and until it
passes the honest description of this phase is "written, not working".

## 2026-09-09 — What the divergence report will and will not be able to say

The Phase 5b milestone is a number comparing simulated fills against real ones, and the
machinery for it now runs end to end on seeded journals — 80 paired fills, a median
divergence, a suggested cost correction. The number itself does not exist and will not
for some time, which is worth writing down before it arrives so that the standard is set
in advance rather than after seeing it.

**The threshold that matters is 5x.** Phase 4 established that the strategy survives five
times its modelled costs at a Sharpe of 0.76. So the divergence report is not really
asking "were the estimates right" — they will not be. It is asking whether they were
wrong by less than a factor of five. Comfortably under and the twenty-one-year backtest
stands with a corrected cost model. Near or above it and the result needs rewriting
rather than adjusting.

**It will take months to mean anything.** A monthly-rebalanced fund holding five of
twelve assets produces a handful of trades per rebalance. Thirty paired fills is most of
a year, and below that the report says so and declines to suggest a correction — a
confident-looking figure built from eleven trades is worse than no figure.

**And it measures the gap between two models, not the gap to reality.** IBKR's paper
account simulates its own book. It is a far better simulator than this one, built by
people with the order flow to calibrate it, and it is still a simulator. The last step —
whether a real order in a real book behaves like either of them — costs money to find
out, and nothing here should be traded with real money until it has run on paper long
enough to surprise you at least twice.
