# Candidate D: stress-conditioned ETF reversal — draft

Status: evidence-backed research proposal, not a tested edge, frozen signal, or
authorization to change paper trading. No new performance curve has been inspected.

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

## Proposed first screen — freeze only after the feasibility review

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
