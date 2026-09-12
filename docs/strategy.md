# The strategy, and what it is actually worth

> Read §4 of [ROADMAP.md](ROADMAP.md) for the specification. This document is the
> result of running it, and the argument about whether it was worth running.

## What it does

Once a month, for each of twelve liquid ETFs:

1. **Score** it on total return over 3, 6 and 12 months, each skipping the most recent
   month, and average the three **ranks** (not the returns — a 12-month return dwarfs a
   3-month one, and averaging the raw numbers would quietly be a 12-month strategy).
2. **Take the top five.**
3. **Check each against its 200-day average.** Anything below it is dropped and its
   money goes to Treasury bills instead.
4. **Size the survivors** inversely to their own volatility, then scale the whole book
   by one number so its forecast volatility — computed from the covariance matrix, not
   from a sum of individual volatilities — comes to 10% a year, capped at no leverage.
5. **Trade** only if some position has drifted more than 20% away from its target.

And it does all five of those on **four staggered dates a week apart**, holding a
quarter of the capital on each, for reasons in §"Timing luck" below.

## The results

Twenty-one years, real prices, with commission, spread and volume-scaled market impact
charged on every fill.

| 2005-01 → 2026-09 | Momentum | 60/40 | SPY | Equal weight |
|---|---|---|---|---|
| Annualised | 7.16% | 8.30% | **10.88%** | 6.47% |
| Volatility | **8.9%** | 10.9% | 18.9% | 11.6% |
| Sharpe | **0.82** | 0.78 | 0.64 | 0.60 |
| Sortino | **1.12** | 1.11 | 0.91 | 0.85 |
| Max drawdown | **−21.8%** | −31.2% | −55.1% | −36.8% |
| Longest drawdown | **698d** | 1,092d | 1,773d | 967d |
| Calmar | **0.33** | 0.27 | 0.20 | 0.18 |

On the full sample it does what it was built to do. It has the best risk-adjusted
return, the smallest drawdown by a wide margin, and it recovers in under two years
where the S&P took nearly five. It also made the second-least money.

**That is the trade, and it is the whole trade.** Anyone who wants the top line should
buy the index and accept that a 55% drawdown is part of the deal.

## Where it comes from

The down years, and one of them above all.

| Every year the index fell | Momentum | SPY | Gap |
|---|---|---|---|
| **2008** | **+10.6%** | **−36.2%** | **+46.8** |
| 2022 | −5.9% | −18.6% | +12.7 |
| 2018 | −0.4% | −5.2% | +4.9 |

Three times in twenty-one years the S&P finished down, and the strategy beat it all
three. That is the pattern working, not a single lucky year — but the *magnitude* is
almost entirely 2008, where the 200-day filter moved the portfolio into Treasury bills
through the crisis and it made money while equities halved.

Strip 2008 out and the argument gets much harder:

| 2010-01 → 2026-09 | Momentum | 60/40 | SPY |
|---|---|---|---|
| Annualised | 7.04% | **10.03%** | 14.17% |
| Sharpe | 0.80 | **1.00** | 0.86 |
| Max drawdown | **−21.8%** | −21.7% | −33.7% |
| Calmar | 0.32 | **0.46** | 0.42 |

**Post-crisis, a plain 60/40 beat it on every measure that matters** — more return, a
better Sharpe, the same drawdown. The strategy's edge over the simplest sensible
alternative is not a general property; it is concentrated in one crisis.

This is not a surprise and it is not a bug. It is what the family does: it pays a
premium in calm markets for insurance that only pays out in a crash. The honest way to
state the result is that **you are buying crash insurance, and the last fifteen years
are what the premium looks like.**

## Where it loses

| Year | Momentum | SPY | Gap |
|---|---|---|---|
| 2019 | +11.1% | +31.1% | **−20.0%** |
| 2020 | −2.4% | +17.2% | **−19.7%** |
| 2023 | +7.6% | +26.7% | **−19.1%** |
| 2009 | +6.2% | +22.6% | **−16.4%** |

Two distinct failure modes.

**Bull markets (2019, 2023).** It holds five assets, some of them bonds and gold, in a
year when owning everything in equities was the answer. Nothing is going wrong here;
this is diversification costing what diversification costs.

**Sharp reversals (2009, 2020).** These are the real weakness. A monthly signal cannot
react to a crash and recovery that both happen inside eight weeks. In 2020 the strategy
looked at the market at the end of February — after the fall had begun — moved to cash,
and was still in cash through the March bottom and much of the rebound. It **lost 2.4%
in a year the index gained 17.2%**, having sold near the low and bought back higher.
2009 is the same failure a year later: the crisis protection that made 2008 was still on
when the recovery started, and the strategy captured 6.2% of a 22.6% year.

Its worst drawdown of the whole sample, −21.8%, did not happen in 2008. It happened
after 2010. The trend filter earned its keep in the crisis and then cost money for a
decade.

## Timing luck, and why the headline number moved

The strategy rebalances monthly, and nothing makes the last session of the month a
better day to trade than the third-to-last. Running the identical strategy on four
dates a week apart:

| Rebalance date | Annualised | Sharpe | Max drawdown |
|---|---|---|---|
| Month end | +7.00% | 0.81 | **−16.0%** |
| 5 sessions earlier | +7.09% | 0.75 | −28.3% |
| 10 sessions earlier | +6.19% | 0.68 | −25.2% |
| 15 sessions earlier | +8.25% | 0.93 | −18.1% |

**A 2.09 percentage point spread in annualised return, and a drawdown ranging from −16%
to −28%, from a choice with no meaning.** For comparison, the same measurement on a
60/40 gives a spread of 0.11 points — timing luck is a property of *selection*, and a
fixed-weight portfolio does not select.

The first version of this document would have reported −16.0% as the maximum drawdown.
That was the luckiest of four arbitrary dates. Running four staggered tranches and
averaging them gives **−21.8%**, which is the number in the table above and the number
to believe. Turnover barely moved, because the tranches' trades
partly cancel before an order is ever produced.

## Does it survive its own costs?

It turns over about 3.5 times a year in each direction — high for a monthly strategy,
driven by the volatility target moving the whole book every month even when the
selection is unchanged.

| Assumed costs | Annualised | Sharpe |
|---|---|---|
| Free execution | 7.31% | 0.84 |
| **Modelled (1x)** | **7.16%** | **0.82** |
| 3x | 6.85% | 0.79 |
| 5x | 6.54% | 0.76 |

At five times the assumed costs it still returns 6.6% at a Sharpe of 0.76. The edge does
not live inside the cost assumptions, which is the failure mode most retail backtests
die of. This is the strongest single result in the document.

The caveat is that all four rows use the *same* model, scaled. A cost model that is
wrong in shape rather than in level would not be caught by this, which is what Phase 5b
exists to find out.

## Is it one asset in a costume?

No, and this was worth checking. Every one of the thirteen instruments made money over
the sample, and the best single contributor was 17% of total profit:

| | QQQ | GLD | SPY | DBC | EEM | … | BIL |
|---|---|---|---|---|---|---|---|
| Net P&L | 61.8k | 56.3k | 51.6k | 26.8k | 26.3k | … | 15.0k |
| Avg weight | 8.9% | 6.5% | 10.0% | 3.4% | 4.7% | … | 18.0% |
| Time held | 70% | 53% | 65% | 32% | 48% | … | 69% |

A strategy whose twenty-year result came four-fifths from one asset would be a bet on
that asset with eleven decorative positions attached, paid for in commission. This one
is not that. The cash sleeve is the largest single average holding at 18% — the strategy
spends roughly a fifth of its life partly out of the market, which is the mechanism, not
a side effect.

## Does it survive being attacked?

Phase 4 ran seventy-one configurations against it. The full battery is
`sillage validate --report`; this is what came back.

**Held-out data — it got modestly worse.** Fitting nothing and simply splitting at
2018-01-02:

| | in sample (2005–2018) | held out (2018–2026) |
|---|---|---|
| Sharpe | 0.86 | 0.73 |
| Max drawdown | −10.9% | −21.8% |

A decline, and the drawdown doubled. Sharpe 0.73 still beats buy-and-hold SPY's 0.64 over
the same span, so this is a degradation rather than a collapse — but it is a degradation
and it is reported as one. Note also that the split date is itself an unexamined choice:
2018 puts 2008, the strategy's best year, wholly in the training half.

**Parameters sit on plateaus, not spikes.**

| Trend window | 100 | 150 | **200** | 250 | 300 |
|---|---|---|---|---|---|
| Sharpe | 0.77 | 0.83 | **0.82** | 0.81 | 0.77 |

| Vol lookback | 20 | 40 | **60** | 90 | 120 |
|---|---|---|---|---|---|
| Sharpe | 0.83 | 0.81 | **0.82** | 0.81 | 0.82 |

A broad gentle hill and a flat line. Neither default was chosen because it peaked,
because neither peaks. The lookback blend is also vindicated: a single 12-month signal
gives 0.76 against the blend's 0.82.

**Two "parameters" are really preferences.** Holding more assets raises Sharpe and lowers
return monotonically (top 3 → top 10: 0.70 → 0.90 Sharpe, +6.6% → +5.3% CAGR); the
volatility target does the same in reverse. Neither has an optimum to overfit to. The
defaults sit mid-dial.

**The tempting result, chased down.** Holding the top eight instead of five gives a
better Sharpe *and* a third less turnover. It turns out to be mostly an illusion of the
leverage cap: eight holdings diversify better, so the book's forecast volatility drops to
8.1% against a 10% target, the scaler cannot lever up to reach it, and the fund runs at
7.1% realised instead of 9.0%. Less risk taken, better Sharpe. Compared at *matched*
risk — top eight at a 10% target against top five at 7% — the gap is 0.80 against 0.78,
two basis points inside a bootstrap interval of ±0.4. (Those two figures predate the
position cap; the cap moves both by about a hundredth and the argument not at all.)

So the honest reading is that bumping to eight is a risk reduction wearing a selection
improvement's clothes. If less risk is what you want, turn the volatility target down and
say so: top five at a 6% target gives the best risk-adjusted result of anything tested,
and costs about a point and a half of annual return.

**Start date barely matters.** Seven starts across a decade: Sharpe 0.80 to 0.86.

**The edge survives deflation.** Bootstrapping the return series in month-long blocks
gives a Sharpe of 0.82 with a 95% interval of **0.41 to 1.23** — wide, and clear of zero.
Correcting for selection, the best of a thousand random strategies would be expected to
show 0.17, and the probability this result is not selection comes to **0.9984**.

**What that does not establish.** Every test above asks whether the result is an artefact
of this sample. None can say the sample resembles the future, and the strategy's case
still rests on one crisis in a window containing one crisis.

## Combining it with something else

Adding a second strategy is the only reliable way to raise a Sharpe ratio, because the
arithmetic turns on correlation rather than on how good either part is. Two sleeves at
0.83 each combine to 1.17 if uncorrelated and 0.87 if they correlate at 0.8.

Dual momentum and a 60/40 correlate at **0.58**, and half of each gives:

| | Volatility | Annualised | Sharpe | Max drawdown | Turnover |
|---|---|---|---|---|---|
| Momentum | 8.9% | 7.16% | 0.82 | −21.8% | 6.89x |
| 60/40 | 10.9% | 8.30% | 0.78 | −31.2% | 0.07x |
| **Half of each** | **8.6%** | **7.79%** | **0.91** | **−19.9%** | **3.55x** |

Better than both sleeves on Sharpe, Sortino, Calmar, drawdown and worst month — and it
returns *more* than momentum alone, at half the turnover. Their bad years are different
ones: trend bleeds through long calm bull markets, which is exactly when a static
allocation compounds quietly. `sillage backtest -s balanced`.

**The ceiling is about 0.95** and correlation sets it. Everything long-only on these
thirteen ETFs correlates with everything else at 0.5 or more, because it is the same
thirteen assets. A third and fourth long-only sleeve buy almost nothing. Reaching 1.1
needs something structurally different — a market-neutral long/short sleeve (the engine
already supports shorting), or a different asset class such as the planned crypto sleeve.

## A note on the risk-free rate

Every Sharpe ratio here is computed against zero, which is the convention and is also
flattering to a strategy that keeps a fifth of its capital in Treasury bills. Against the
cash sleeve's actual return the headline moves from 0.83 to **0.76**, and the 60/40 from
0.79 to 0.66. The gap is small only because this sample is dominated by the zero-rate
years: cash averaged 0.66% a year over it. In a period like 2023-24 the correction would
be several times larger.

## The crypto sleeve, and why it does almost nothing

The universe extends to bitcoin and ether, which never close while the portfolio's
heartbeat is the NYSE. That resolves conservatively — a crypto bar closes at 23:59 UTC,
after the New York close, so the strategy sees crypto a session behind and never ahead.

Adding it took the Sharpe from 0.86 to 1.03 over 2018–2026. Almost all of that is
hindsight, and the measurement is the point:

| Crypto admitted to the universe | Sharpe | Annualised | Mean weight |
|---|---|---|---|
| Never | 0.86 | 7.51% | — |
| **January 2018** | **1.03** | 10.05% | 5.5% |
| January 2021 | 0.87 | 8.06% | 4.2% |
| January 2023 | 0.90 | 8.12% | 3.3% |

The 2018 row is a fund allocating to an asset with two months of price history, because
whoever wrote the backtest in 2026 knows what happened next. On a date a real investment
committee might have reached — after regulated futures, mainstream custody and corporate
treasuries — crypto adds **0.01 of Sharpe.**

Nothing in the validation battery above can detect this. The bootstrap resamples a given
strategy's returns; the deflation corrects for configurations tried; the held-out split
tests unseen data. All three take the universe as given, and this bias happens before any
of them runs. `Instrument.available_from` now declares the assumption so it can at least
be swept.

## One holding, one third, at most

Positions are capped at a third of the fund. The roadmap expected to need that for crypto
and was wrong twice: inverse-volatility sizing gives a four-times-more-volatile asset a
four-times-smaller weight, so crypto never exceeded 14.9%.

What it needed capping for was the opposite end. Uncapped, the largest position the
strategy ever took was **58.7% in high-yield credit** — inverse-vol hands the biggest
weight to whatever looks quietest, and credit is quiet until it is not: it sells off in
jumps and stops being liquid exactly when someone wants out.

| Cap | Annualised | Sharpe | Largest position |
|---|---|---|---|
| none | 7.25% | 0.83 | 58.7% |
| **a third** | **7.16%** | **0.82** | **35.0%** |
| a quarter | 6.87% | 0.80 | 25.0% |

Nine basis points a year, and maximum drawdown is unchanged at every level — this is
insurance against a concentration event that did not occur in twenty-one years, which is
the honest description of insurance rather than an argument against it.

## Known weaknesses

- **Its case rests on one crisis.** It beat the index in all three of the sample's down
  years, but 2008 supplies almost all of the margin, and it is the only genuine crash in
  the window. One observation of a crash is one observation. The 2000–02 bear market is
  the natural second test and our data begins in 2005 — extending it is the cheapest
  meaningful improvement available to this project.
- **Monthly signals cannot handle fast reversals.** 2020 cost 19.7 points against the
  index. A faster signal would fix 2020 and break something else; this has not been
  tested and should not be tuned until it has been, on data held out for the purpose.
- **The volatility target is one-sided.** With no leverage permitted, the strategy runs
  *under* 10% in calm regimes and only at target in violent ones. Realised volatility
  came to 9.0% against a 10% target. "10% vol strategy" overstates it; "at most 10%,
  usually less" is accurate.
- **Momentum and the trend filter are not independent.** An asset with strong 12-month
  momentum is usually already above its 200-day average, so the filter binds mainly at
  turning points. That is where it is wanted, but it is one layer of protection, not two.
- **Six parameters**, all inherited from the literature rather than fitted here. That is
  a real defence and a weaker one than it sounds: the literature fitted them, largely on
  this same US data. The deflation above uses a pessimistic thousand trials partly to
  stand in for the ones nobody here ran, but that is a gesture at the problem, not a
  measurement of it.
- **Turnover of about 7x a year is inherent, not a setting.** The no-trade band removes a
  fifth of the trades and almost none of the notional: turnover comes from monthly
  re-selection and from the volatility scalar moving every weight at once. Costs are
  currently immaterial because these are liquid ETFs at small size. At scale, or in
  anything wider than an ETF spread, this would be the first thing to break.
- **Adjusted prices restate the past.** Every historical bar reflects dividends paid
  after it. Immaterial for monthly rebalancing on ETFs; not immaterial in principle.

## The verdict

On twenty-one years of data, honestly costed and with the timing luck taken out, this
strategy produces **index-like risk-adjusted returns with a third of the index's
drawdown, and meaningfully less money.** Against a 60/40 it wins on the full sample and
loses on the last fifteen years.

Whether that is worth owning depends entirely on a question the backtest cannot answer:
whether you would actually have held it through 2019, 2020 and 2023, watching a
neighbour's index fund beat you by twenty points a year, on the strength of what it did
in a crisis before that and might do in the next one.

Treat this as a hypothesis the code exists to test, not a recommendation. Nothing here
has been traded with real money, and Phase 4 — held-out data, walk-forward, parameter
sensitivity — is what would move it from "the backtest looks good" to "the process that
produced this number does not lie."
