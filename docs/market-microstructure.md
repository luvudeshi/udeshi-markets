# Market Microstructure

How trading costs and liquidity are measured in Udeshi Markets, and what the simulated market
shows. The matching rules themselves (price-time priority, order types, self-trade prevention)
are in [matching-engine.md](matching-engine.md). The other metrics are in
[quantitative-metrics.md](quantitative-metrics.md).

## The quote a trade is measured against

Every trade comes from an aggressive order crossing resting liquidity. For each processed
order, the kernel hands observers the best bid and ask **just before the order reached the
exchange**. The prevailing midprice m = (bid + ask)/2 is the benchmark for every trade that
order produced. If either side was empty, there is no midprice: the trade is counted (as
`trades_without_quote`) but excluded from spread measures.

With trade price p, quantity q and s = +1 for a buyer-initiated trade (−1 for
seller-initiated):

| Measure | Definition | Reading |
| --- | --- | --- |
| **Effective spread** | 2s(p − m)/m | The round-trip cost the aggressor paid relative to the midprice. Twice the one-way cost, so it is comparable with the quoted spread. |
| **Realized spread** | 2s(p − m′)/m | What the liquidity provider kept, once the market had moved: m′ is the midprice τ after the trade (`markout_ns`, default 5 s). |
| **Price impact** | 2s(m′ − m)/m | How far the midprice moved in the aggressor's direction. This is the information in the trade: the adverse selection the provider suffered. |

Trade by trade, effective spread = realized spread + price impact. All three are reported in
basis points, volume-weighted, over the same set of trades: those with both a pre-trade and a
post-trade midprice. The identity therefore holds for the reported averages too, which the
tests check on every instrument.

**When m′ is read.**
* **Experiments:** `run_observed` stops the simulated clock exactly at t + τ for every trade, so
  m′ is the midprice after everything that happened up to and including that instant.
* **Live API sessions:** the runner advances in ticks, so m′ is read at the first tick at or
  after t + τ, which is later by less than one tick.
* **End of run:** trades within τ of the end have no markout. They are counted
  (`trades_without_markout`) and excluded.

## Liquidity

| Measure | Definition |
| --- | --- |
| **Quoted spread** | ask − bid, sampled every Δ. Reported in currency and in basis points of the midprice, averaged over samples where both sides are quoted. |
| **Depth** | Shares resting within the best five price levels, bid plus ask, averaged over samples. |
| **Order imbalance** | (D_bid − D_ask)/(D_bid + D_ask) of that depth, in [−1, 1], averaged over samples with any depth. |
| **One-sided fraction** | Share of samples with an empty side. |

## Order execution

| Measure | Definition |
| --- | --- |
| **Fill rate** | Shares filled / shares submitted, over all orders of an instrument. Every trade fills two orders (the aggressor's and the resting one), so this is 2 × volume / shares submitted. Per participant: the participant's filled shares / its submitted shares. |
| **Average execution size** | Volume / number of trades. |
| **Buyer-initiated fraction** | Share of trades whose aggressor was a buyer. |

## What the simulated market shows

One 30-minute run of the default market (26 agents, seed 1), measured:

| | Effective spread | Realized spread | Price impact | Quoted spread |
| --- | --- | --- | --- | --- |
| ACME | 12.4 bps | 0.3 bps | 12.0 bps | 9.4 bps |
| NOVA | 13.9 bps | −2.0 bps | 15.9 bps | 10.5 bps |
| QUANT | 13.4 bps | 0.2 bps | 13.2 bps | 10.2 bps |
| ORBIT | 16.6 bps | 1.7 bps | 14.9 bps | 13.7 bps |
| VECTOR | 12.9 bps | −0.6 bps | 13.5 bps | 10.1 bps |

**Price impact explains almost all of the effective spread.** Liquidity providers keep close to
nothing. This is the adverse-selection result of Glosten–Milgrom and Kyle: the fundamental
traders trade on information about value that the market makers do not have. Every spread the
market makers earn is given back to price moves against their fills. The agent statistics agree:
* market makers lost money on average (Sharpe −4.8 per hour);
* the informed fundamental traders profited (Sharpe +5.5 per hour, 73% of their closing fills
  profitable).

**Effective exceeds quoted.** Effective spreads are larger than quoted spreads because aggressive
orders often walk the book past the best level.

These numbers come from one seed on one machine. The experiment framework gives their
distribution across seeds.

## Assumptions and limitations

* **The benchmark is the midprice.** It is not a microprice weighted by depth.
* **One horizon per run.** The markout horizon τ is a single parameter; a term structure of
  impact (several τ) would be a straightforward extension.
* **Classification is exact.** Trades are classified by the engine's true aggressor side, not by
  a tick or quote rule as empirical studies of real data must do.
* **No cost model.** There are no fees, rebates or latency arbitrage: costs here are spreads and
  impact only.
