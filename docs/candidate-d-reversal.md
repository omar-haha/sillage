# Candidate D: stress-conditioned ETF reversal — feasibility reviewed

Status: daily-input/cost feasibility review complete; research-screen specification
frozen below on 2026-10-11. No alpha curve has been inspected. Quote, borrow and
account-level margin validation are still prerequisites for promotion, not completed
evidence. This does not authorize changing paper trading.

## First feasibility review

`scripts/reversal_feasibility.py` and `research/reversal-feasibility.json` reproduce
the review. The frozen 2005-01-03 through 2026-09-16 NYSE-session window has no
missing or nonpositive closes for the nine ETFs, SPY or Cboe VIX. VIX is sourced
directly from Cboe; ETF daily bars retain Candidate C's hashed Yahoo vintage.
Coverage does not certify data accuracy or historical execution prices.

At a 20% allocation, a US$25K portfolio gives the sleeve US$5K. Eight illustrative
$1 tickets consume 16bp per complete four-leg round trip, before spreads and borrow.
Adding a declared 4bp round-trip spread and two calendar days of 1% borrow on half
the gross raises the cost floor to 20.27bp of sleeve capital. At US$100K, the same
allocation gives US$20K and the corresponding floor is 8.27bp. These are budget
examples, not measured ETF spreads or observed expected returns. Stress budgets
use 20bp spread, 4% borrow and four calendar days; impact and other fees are extra.

Conditional decision: permit a research-only daily-bar screen with explicit costs,
not broker deployment. Usable historical quotes and short-fee history were not
found in the repository. No market-data purchase or broker subscription was made.
The outsized paper account's margin cannot certify a live US$25K portfolio.

## Mechanism and evidence

Temporary selling/buying pressure can displace prices; taking the other side may
earn compensation as that pressure dissipates. This differs from the core sleeve's
medium-term trend and directional asset exposure. It is still liquidity-risk taking,
not a guaranteed hedge: price moves can reflect permanent information, and stress
can deepen instead of reversing.

[Nagel, Evaporating Liquidity](https://www.nber.org/papers/w17653) reports stronger
conditional reversal returns in high-VIX regimes, including industry portfolios.
This supports a mechanism and regime hypothesis, not a specific SPDR ETF signal,
threshold, retail execution rule, or attainable Sharpe. ETF transferability is the
experiment. Do not call a next-open market-order strategy literal passive market
making: it crosses the spread rather than earning it.

## Feasibility gate before implementation

1. Check coverage and gaps for the nine original sector ETFs, SPY and historical
   VIX closes. Declare a common start/end and warm-up before running any strategy.
   Retain the existing control cutoff first; newer observations are not automatically
   untouched if already inspected elsewhere.
2. Identify usable spread/quote evidence. Adjusted daily Yahoo bars cannot establish
   historical bid/ask spreads, opening-auction capacity or intraday fill quality.
   A daily-bar run may be a pessimistic cost screen, not venue validation. Do not
   silently substitute guessed spreads for measured historical quotes.
3. Estimate turnover and break-even cost budget from the proposed schedule, without
   tuning it to a price curve. Include both legs, opening gaps, conservative commissions,
   borrow, distributions, zero short-proceeds interest and calendar-day financing.
4. Check current contract permissions, shortable shares/fees and account-level what-if
   margin before any future promotion. Availability is time-varying; historical borrow
   proxies do not certify today's ability to short. Paper approvals do not establish
   live fills. At $25K, reserve buying-power and drawdown buffers; no leverage increase.
5. Decide the combined runner's reconciliation, recall/buy-in and failed-leg handling
   before deployment. Missing one short must cancel admission of the whole basket;
   stress does not justify bypassing safety checks.

IBKR references: [borrow cost and short-proceeds interest](https://brokerage.ibkr.com/en/pricing/short-sale-cost.php),
[short availability](https://investors.interactivebrokers.com/en/trading/short-securities-availability.php?menu=A).
The listed first USD 100K short-proceeds tier earns zero interest. Borrow fees can
change, and availability must be checked rather than inferred from ETF liquidity.

## Frozen first daily-bar screen

These rules are committed before the first Candidate D alpha run. They operationalize
the draft below; this is not an empirically chosen optimum. Any subsequent rule
change is a separate ledger entry, never an overwriting of this specification.

- Research period ends 2026-09-16; 252-session warm-up, score only afterward.
  Chronological halves and 2008/2020 stress episodes are reused diagnostic data,
  not pristine holdouts. Forward paper observation is distinct evidence.
- Beta: covariance of trailing 252 daily log returns with SPY divided by SPY
  variance. Require positive finite betas and positive residual volatility.
- Rank the sum of trailing five daily log residual returns divided by the standard
  deviation of trailing 20 daily residual returns. Residuals use the decision's
  trailing beta. Tie-break alphabetically; lowest two long, highest two short.
- VIX decision close >=25 activates entries. The decision runs after the VIX closing
  observation is available, not at the ETF closing bell using a future VIX print.
- Equal weights within sides, side gross chosen to neutralize estimated SPY beta,
  total gross <=100%, individual absolute weight <=35%; proportional reduction
  preserves beta neutrality if capped. Round target shares toward zero.
- One basket at a time. Enter next observed session open, hold two complete
  open-to-open intervals, exit the third open; no intraday entries/exits or stop
  prices synthesized from daily OHLC. An entry is not permitted on an exit day.
  Stress ending does not accelerate the fixed exit. Invalid history means no entry;
  do not abandon an existing basket's scheduled exit. The final open basket is marked
  and identified separately, not given a fabricated terminal fill.
- Use the existing cost model with $0.005/share, $1 minimum commission, 2bp
  half-spread, and trailing 20-session ADV impact. These fixed-pricing inputs are
  conservative assumptions consistent with observed minimum paper tickets; extra
  venue fees and actual stress spread changes remain limitations.
- Base borrow 1% over actual calendar days; zero interest on idle cash and short
  proceeds. Compute real cash/debit balances and financing if debit cash arises.
- Declared variants only: VIX 20 and 30, one extra session entry delay, 2x execution
  costs, 4% borrow, and a 10bp half-spread plus 4% borrow crisis-cost scenario.
  Delayed entry retains the original decision and cannot peek at the next day's rank.
- Primary combination allocates 20% of capital to D, with 10% and 30% as robustness
  checks, not competing weights from which to choose a winner. Combined gross <=100%
  with capital substitution and netted orders; financing and commissions must be
  recalculated on the combined book before any executable conclusion.

The first standalone sanity gate is evaluated at $5K (20% of the modeled $25K
portfolio), not $25K secretly devoted to the sleeve. An unchanged-rule $25K standalone
run is a size diagnostic only; it cannot replace a failed $5K primary case.

First accounting/sanity gate: positive cumulative net trading P&L (excluding cash
interest) in both chronological halves, plus no reliance on free execution for the
sign. This is not a minimum standalone excess-Sharpe requirement. If viable, the
primary combined screen must improve excess Sharpe by >=0.05, not deepen maximum
drawdown, and not lengthen the longest drawdown versus the same-period balanced
control. Report a paired block-bootstrap interval for the improvement; require
its 95% lower bound above zero before treating improvement as established. Report
all alternate-weight/cost/delay cases rather than silently replacing the primary
case. If minimum gates fail, log rejection; if uncertainty straddles zero, mark it
inconclusive, not approved. A >=1.1 final portfolio Sharpe remains a later target.

No paper promotion unless live permissions, quotes/borrow, virtual-budget margin
and failed-leg/recall handling are verified. Low sample size in high-VIX episodes,
tail losses or reused history can prevent promotion even when point estimates pass.

### Original proposal, retained for provenance

The draft wording below is superseded by the frozen controls above. Its references
to future freezing describe the earlier planning stage, not the current status.

- Universe: `XLB XLE XLF XLI XLK XLP XLU XLV XLY`; SPY supplies a market reference,
  not another overlapping ranked asset. Do not add sectors after seeing winners.
- At a close, rank five-session sector returns net of trailing estimated SPY beta,
  normalized by trailing 20-session residual volatility. Long two recent losers and
  short two winners. All estimates use only observations available at that close.
- Proposed activation: prior decision-close VIX at least 25. This is an explicit,
  judgmental stress definition, not Nagel's estimated optimum. Declare 20/30 solely
  as robustness checks, not thresholds selected afterward.
- Proposed holding period: two sessions, enter and exit at next-session opens;
  one basket at a time, no pyramiding. Freeze tie-breaking, zero-volatility behavior,
  eligible entry/exit days, and whether stress ending permits an early exit before
  implementation. Test an extra one-session entry delay.
- Candidate-alone maximum gross 100%, capped single-name weights, whole shares;
  combined portfolios use capital substitution, not additive leverage. Freeze exact
  beta-neutral side sizing and gross/position limits before any curve is generated.
- Borrow proxy base 1%, stress 4%; 2x execution-cost and stress-spread scenarios.
  These are assumptions, not an archive of actual crisis borrow/quotes. Use zero
  cash interest in the conservative $25K screen, consistent with Candidate C.

These choices are deliberately drafts. Do not backtest several draft versions and
then describe the winner as preregistered. Add the final dated rules to the trials
ledger and commit the frozen specification first.

## Portfolio-first acceptance

Do not demand that a complementary sleeve individually reach the portfolio's
1.1 excess-Sharpe target or beat SPY. Conversely, low correlation alone is not a
reason to finance a losing, tail-fragile strategy.

Before running results, freeze numerical pass/fail thresholds for:

- A credible positive net-of-cost incremental return source across chronological
  splits and stress episodes; reject accounting-dependent or free-execution-only edge.
- Substituting 10%, 20% or 30% of core capital into the candidate, rather than adding
  exposure. Rerun the combined order book, net opposing ETF orders, accrue actual
  portfolio financing assumptions and account for nonlinear minimum commissions.
  A weighted sum of standalone returns is only a preliminary diagnostic.
- Material improvement in combined excess Sharpe, drawdown depth/duration and worst
  month against frozen balanced and SPY controls. Bootstrap the *paired improvement*,
  preserving serial dependence; stress windows may be too few for strong conclusions.
- Tail behavior when both sleeves lose, sustained selloffs, widening spreads,
  borrow withdrawal and delayed/unpaired execution. Do not cherry-pick VIX events.
- Robustness across the predeclared thresholds/holding delays and concentration;
  include all variants in selection-aware uncertainty and the trials ledger.

Final portfolio target remains excess Sharpe >= 1.1 and a favorable SPY return/
drawdown comparison; neither is a promised attainable outcome. If D fails, retain
the rejection rather than inventing rescue variants. Cross-asset value stays a
secondary research idea, not another simultaneous experiment or a ready-made ETF signal.
