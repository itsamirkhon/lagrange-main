# Three-minute live demo and judge Q&A

Date: 2026-11-17. Use the local web UI, not slides with precomputed gains.

## Timing

**0:00-0:30 | Stakes.** "A medicine distributor sells 150,000 packs a year. The retail price has a regulated ceiling of 150,000 UZS, and the challenge says profitability fell to around 2-3%. The distributor cannot solve that simply by raising prices."

**0:30-1:00 | Explain the real variable.** Show the case strip. Emphasize that the 125,000 UZS *wholesale* cap and every procurement option in the demo are **clearly marked synthetic**. "The system must optimize supplier options, shipment sizes and routes together under a price constraint."

**1:00-1:40 | Run a real solver.** Click Run optimization. Show the allocated packs sum to exactly 150,000; switch attention to expected profit versus worst scenario. Explain that the system solves integer shipments and checks each scenario's cash constraint.

**1:40-2:05 | Challenge it.** Move UZS depreciation from 12% to 18%, click Run. Disable "Regional pooling" and re-run. Show that the new solution uses other routes and the results change.

**2:05-2:25 | Break it intentionally.** Click **Test infeasibility**. The tool must return an explicit `INFEASIBLE`, not a fabricated plan. Reset.

**2:25-2:50 | Counterintuitive math.** At the bottom, set factory discount to 5%. Explain why, *under the assumed cost-plus markup equation*, a cheaper acquisition base can also lower the permitted wholesale price. Label this a thought experiment, not legal advice.

**2:50-3:00 | Proof.** Click Export JSON. Show the solution trace, 21+ automated tests and small-instance brute force benchmark. Close: "We don't ask a model to guess the optimal decision. We solve it and independently audit it."

## Q&A to expect

**"Is this real supplier data?"** No. The procurement and FX sensitivity inputs are invented. Public registration and actual supplier quotes are needed to establish real-world effects. This prototype demonstrates that the optimization method and constraints function.

**"Is this AI or simply operations research?"** The mathematical core is an optimization solver. It is not a trained ML model. Future AI components are document extraction and demand prediction; both need real evaluation. The current demo is an algorithmic decision engine, not a pretense of existing ML training.

**"Are these regulatory restrictions legally correct?"** The current reference-pricing framework is No. 3735; our price caps and markup equation are simplified and must be checked with a domain expert, using actual registered medicine data. No legal certification is claimed.

**"Can you guarantee the +765M UZS uplift?"** No. That is an optimized result within the synthetic example. Formal feasibility and a low solver gap do not establish commercial forecast accuracy.

**"Why not build the six MARJA modules?"** They are a broader vision. We built one integrated, testable optimizer instead of a front-end that pretends unimplemented marketing, adherence and localization models exist.

**"Why is the margin under FX stress negative?"** It is a valid modeled scenario. We do not impose a fake nonnegative-profit constraint; the solver can minimize losses but cannot conjure guaranteed gains.

## Fallback

If the site fails, use: `python -m lagrange_rx.cli --output evidence/fallback.json`, open the exported JSON, and explain the allocation and sensitivity outcomes from actual runs. Screenshots are secondary evidence, not proof of server health.
