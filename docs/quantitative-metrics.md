# Quantitative Metrics

The definitions behind every number the analytics module
([`python/udeshi_markets/analytics`](../python/udeshi_markets/analytics)) and the experiment
framework ([`python/udeshi_markets/experiments`](../python/udeshi_markets/experiments)) report.
Microstructure measures (spreads, impact, fill rates) have their own document:
[market-microstructure.md](market-microstructure.md).

## Conventions

* **Observation.** A `MarketObserver` watches a run without taking part in it. It samples the
  market and every portfolio every Δ of simulated time (`interval_ns`, default 1 s) and records
  every order and trade as it happens. There are P = 3600 s / Δ periods per hour.
* **No future information.** Every observation is recorded at the time it describes. The only
  quantities that involve a later time are the post-trade markouts (realized spread, price
  impact). Those are measured when that later time arrives, and nothing feeds them back into the
  simulation, so they cannot influence any decision.
* **Undefined is not zero.** A metric that does not exist for the data returns `None`:
  * fewer than two observations for a standard deviation;
  * zero variance where it would be a divisor;
  * no trades for a spread, no closing trades for a win rate.

  `None` never takes part in an aggregate: a mean over runs uses the runs where the metric is
  defined, and reports how many there were.
* **Zero variance is exact.** A series of identical values has variance exactly 0. Statistics
  are computed on deviations from the first value, so rounding in the mean of identical floats
  cannot produce a spurious tiny variance.
* **Exact accounting, float analytics.** Portfolios are exact (`Fraction`, see
  [trading-agents.md](trading-agents.md)). Final P&L figures come from them. Equity curves are
  sampled in floating point, accurate to about 1e-12 relative, which is enough for ratios.

## Statistics

| Quantity | Definition |
| --- | --- |
| Mean | x̄ = (1/n) Σ xᵢ |
| Sample variance | s² = Σ(xᵢ − x̄)² / (n − 1), the unbiased estimator; needs n ≥ 2 |
| Quantile q | Linear interpolation between order statistics at position q(n − 1) (Hyndman and Fan type 7, the default in R and NumPy) |
| Confidence interval | x̄ ± t₀.₉₇₅,ₙ₋₁ · s / √n for the mean, with t from Student's distribution |
| Welch's t-test | t = (x̄_b − x̄_a) / √(s_a²/n_a + s_b²/n_b) in comparisons, the same sign as the difference of means, with degrees of freedom by Welch and Satterthwaite: ν = (s_a²/n_a + s_b²/n_b)² / [(s_a²/n_a)²/(n_a − 1) + (s_b²/n_b)²/(n_b − 1)]. The p-value is two-sided, 2(1 − F_ν(\|t\|)). |

**Student's t distribution** is implemented in the package, so it needs no SciPy:
* F_ν(t) = 1 − ½ I_x(ν/2, ½) for t > 0, with x = ν / (ν + t²);
* I is the regularized incomplete beta function, evaluated by Lentz's continued fraction;
* quantiles are found by bisection on F.

Tails are computed directly rather than as 1 − F, so small p-values keep their relative
precision: t = 12 with ν = 58 gives p = 2.36 × 10⁻¹⁷, which 1 − F would round to 0.

Accuracy, measured against SciPy on 576 points (t from 10⁻¹² to 10⁴, ν from 0.3 to 10⁷):
* the absolute error of F is at most 3.2 × 10⁻¹⁰ for ν ≤ 10⁵ (1.6 × 10⁻⁹ at ν = 10⁷);
* tails are within 10⁻⁹ relative for ν ≤ 10⁵.

The tests check it against published tables. The large-ν case agrees with the expansion
t ≈ z + (z³ + z)/(4ν).

## Market statistics (per instrument)

| Metric | Definition |
| --- | --- |
| Last price, best bid, best ask, midprice, spread | From the exchange: the midprice is (bid + ask)/2 when both sides are quoted; the spread is ask − bid. |
| Trades, volume | Count and total quantity of executions. |
| Realized volatility | σ̂_hour = s(r) · √P, where r_t = ln(m_t / m_{t−1}) are log returns of the midprice between consecutive samples. Returns span only consecutive samples where both midprices exist; a one-sided book breaks the series. It is 0 for a constant price and None with fewer than two returns. |
| Mean spread | The average of ask − bid over samples where both sides are quoted, in currency and in basis points of the midprice. |
| One-sided fraction | The share of samples with an empty side. |
| Depth | Shares resting within the best `depth_levels` (default 5) price levels, bid plus ask, averaged over samples. |
| Order imbalance | (D_bid − D_ask) / (D_bid + D_ask) of that depth, in [−1, 1], averaged over samples with any depth. Positive means more buying interest. |
| Pricing error (market efficiency) | The mean over samples of \|ln(m_t / V_t)\| in basis points, with V_t the hidden fundamental value at the same instant. It is the observer's measure of how well prices reflect value. Agents never see V. It is None without a market environment. |

## Agent statistics (per participant)

| Metric | Definition |
| --- | --- |
| Realized P&L | Average-cost method: reducing a position realizes (price − average cost) × quantity × sign(position). |
| Unrealized P&L | Σ(qᵢ · markᵢ − cost basisᵢ). The mark is the midprice, else the last trade, else the reference price. |
| Total P&L | Equity − initial cash, where equity = cash + Σ qᵢ · markᵢ. It always equals realized + unrealized. Over all participants it sums to zero exactly. |
| Return | Total P&L / initial cash × 100. |
| Equity curve | E_t sampled every Δ, from the start of the run to the final instant. |
| Sharpe ratio (per hour) | SR = mean(r) / s(r) · √P, with r_t = (E_t − E_{t−1}) / E_{t−1} and a risk-free rate of zero. It is None with fewer than two returns or zero variance: a participant that never trades has an undefined Sharpe ratio, not zero. |
| Maximum drawdown | max_t (peak_t − E_t), where peak_t is the running maximum of E. Reported in currency, and as a percentage of the peak it fell from (None if that peak was not positive). |
| Exposure | Gross exposure Σ\|qᵢ\| · markᵢ: mean and maximum over samples. |
| Win rate | Closing fills that realized a profit / closing fills. A closing fill reduces a position; a fill at exactly the average cost counts as no win. None with no closing fills. |
| Trades | Fills booked to the participant (a self-trade counts two). |
| Average execution price | Per instrument, the volume-weighted average price of the participant's own fills. |
| Fill rate | Shares filled / shares the participant submitted. |

## Experiment metrics

`run_once(spec, seed)` reduces one run to scalar metrics, named as below. Market-wide values are
means over instruments, each instrument's value being itself a mean over its samples or trades.
Counts are sums over instruments.

| Name | Meaning |
| --- | --- |
| `trades`, `volume` | Totals over instruments. |
| `volatility_per_hour`, `spread_bps`, `depth`, `one_sided_fraction` | Market statistics, as above. |
| `abs_imbalance` | The mean over instruments of \|mean imbalance\| (so opposite imbalances do not cancel). |
| `pricing_error_bps` | Market efficiency, as above. |
| `effective_spread_bps`, `realized_spread_bps`, `price_impact_bps`, `fill_rate`, `average_trade_size` | Microstructure; see [market-microstructure.md](market-microstructure.md). |
| `<strategy>.pnl`, `.return_pct`, `.sharpe`, `.max_drawdown_pct`, `.win_rate`, `.trades` | The mean over that strategy's agents for whom the metric is defined. The strategy is one of random, market_maker, momentum, fundamental or pairs. |

## Experiments

* **Independent runs.** Run k of an experiment uses seed `base_seed + k`. A run is a pure function
  of (spec, seed), so any run can be reproduced on its own, and its digest identifies it.
* **Parallelism.** Runs execute in spawned worker processes. Results are collected in seed
  order, so a parallel experiment produces exactly the same results as a sequential one (a
  test checks this).
* **Monte Carlo.** `monte_carlo(spec, runs)` estimates each metric's distribution over the
  market's randomness. For each metric, the summary gives:
  * count of runs where it is defined, mean, standard deviation and standard error;
  * the 95% t confidence interval for the mean;
  * minimum, 5th percentile, median, 95th percentile and maximum.
* **Parameter sweeps.** `sweep(spec, grid)` builds one experiment per point of the Cartesian
  product of settings. A setting is a field of the spec (`duration_ns`), of the population
  (`population.market_makers`) or of the environment (`environment.volatility`). Points come
  in a fixed order, and all points' runs share one pool of workers.
* **Comparison.** `compare(a, b)` gives, per metric, both means, their difference and Welch's
  test of it. With few runs, read p-values with care: runs are independent, but the
  distributions of market metrics need not be normal.
* **CSV export.** There are three tables:
  * one row per run: experiment, seed, swept parameters, metrics, digest, wall time;
  * one row per experiment and metric: the summary;
  * one row per compared metric.

  Undefined values are empty cells. Wall time is measured on the machine that ran the run. It is
  informational, never a metric.

## Measured example

The brief's two example experiments were run for 5 simulated minutes with 4 seeds each, and
compared:
* **Experiment 1:** 5 market makers, 20 random, 5 momentum, 5 fundamental and 1 pairs trader.
* **Experiment 2:** 10 market makers, 50 random, 10 momentum, 5 fundamental and 1 pairs trader.

| Metric | Experiment 1 | Experiment 2 | Welch p |
| --- | --- | --- | --- |
| Spread (bps) | 9.12 | 6.71 | 0.0005 |
| Depth (shares, top 5 levels) | 70.9 | 131.9 | 0.0003 |
| Volume (shares) | 20,804 | 40,026 | 0.034 |
| Realized volatility (per hour) | 0.038 | 0.029 | 0.080 |

More market makers and more order flow give a tighter, deeper and busier market. The volatility
difference is not significant at this sample size. These are single measurements, from one
development machine.
