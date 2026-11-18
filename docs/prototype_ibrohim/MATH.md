# Mathematics, units, and limits

Date: 2026-11-17. Model ID: `single-stage robust MILP with fixed common wholesale price`.

## Scope

An importer/distributor procures **one assumed, registered prescription SKU** through several hypothetically authorized source-and-route options. Source eligibility is pre-approved input and **not inferred by the solver**. A single wholesale selling price is selected across active routes and scenarios, which is a conservative demonstration policy, not a proven legal requirement. No substitution between different registered drugs is assumed.

## Indices and inputs

- `i` indexes eligible source/transport options; `s` indexes scenarios, `sum_s w_s = 1`.
- `D` = annual demand in packs; `L` = indivisible lot size in packs.
- `a_is` = acquisition base per pack in UZS for route i and scenario s.
- `b_i` = below-line distribution/marketing/working-capital/other cost in UZS per pack.
- `f_i` = fixed cost per shipment; `F_i` = one-time route activation cost.
- `m_i`, `q_i` = shipment minimum/maximum pack size; `U_i` = route capacity in packs.
- `B` = worst-case budget for acquisition plus fixed shipping/activation expenses.
- `P^R_s` = **assumed registered wholesale cap** in scenario s, and `P^C` = synthetic maximum contracted wholesale price.
- `mu` = illustrative markup fraction, `0.15`. The legal base/VAT treatment remains UNVERIFIED.

Each acquisition base is modeled as:

`a_is = factory_i * (1 + fx_fraction_i * fx_shock_s) + import_fees_i + freight_i * (1 + freight_shock_s)`

FX and freight shocks are fractions (`12%` is `0.12`). Scenarios are *explicitly hypothetical*. An FX shock does not itself change the registered cap; `wholesale_ceiling_change_pct` is modeled as a separate, hypothetical official revision.

## Decision variables

- `x_i` = nonnegative integer **number of lots**, so pack count is `L * x_i`.
- `n_i` = nonnegative integer shipments through route i.
- `y_i` in `{0,1}` = activate route i.
- `p` = common wholesale sale price in UZS per pack.
- `z` = lowest modeled annual profit in UZS.

## Profit, objective

For each scenario:

`Profit_s = D * p - sum_i [L*x_i*(a_is+b_i) + f_i*n_i + F_i*y_i]`

The solver maximizes:

`(1-lambda) * sum_s [w_s*Profit_s] + lambda*z`

subject to `z <= Profit_s` for all `s`. At `lambda=0`, optimize expected synthetic profit; at `lambda=1`, maximize worst-case synthetic profit. This is **worst-scenario protection, not CVaR**.

## Constraints

1. Demand equality: `sum_i x_i = D/L`. Shortages are not silently allowed.
2. Capacity and activation: `L*x_i <= U_i*y_i`, `n_i <= max_shipments_i*y_i`, and activation requires a shipment.
3. Shipment bounds: `m_i*n_i <= L*x_i <= q_i*n_i`.
4. Wholesale price: `p <= P^C`, `p <= P^R_s` for each s.
5. Active-route price bound: `p <= (1+mu)*a_is + M*(1-y_i)` for every route and scenario. A mathematically sufficient `M` is the upper contracted sale price (since modeled price bounds are nonnegative).
6. Budget: `sum_i [L*x_i*a_is + f_i*n_i + F_i*y_i] <= B` for *every* scenario.
7. Unavailable routes have zero shipments, activation and purchases.

No explicit nonnegative profit constraint is imposed; the optimizer may correctly return a best-feasible loss. There is no "guaranteed profitability" constraint.

For numerical stability the MILP uses `p` in **thousands of UZS** and profit/cash in **millions of UZS**; the public API returns UZS. Its post-solve auditor recomputes legal-model bounds and actual economic totals from the integer allocation independently of solver output.

## Cost-plus paradox (illustrative)

When wholesale price is `p = min(P^C, P^R, 1.15*a)`, per-pack contribution before fixed fees is `p - a - b`. If the 15% model bound is active, contribution is `0.15*a-b`, so reducing a can reduce contribution; if the reference cap is active, reducing a can increase contribution. These signs switch at a price-boundary kink.

This is a property of the **simplified equation**, not evidence that companies can or should increase acquisition costs. The current No. 3735 reference-price rules and the actual legal cost base require a specialist to verify the modeled 15% term.

## Honest limitations

- The 125,000 UZS wholesale cap in the dataset is a **back-of-the-envelope assumption**, not an observed drug registry ceiling. The 150,000 retail ceiling and 150,000 packs come from the hackathon prompt.
- Fixed annual demand and freely interchangeable authorized routes are assumed. No demand model is trained; no real route authorization is verified.
- No dates, lead times, inventory balances, FX forwards, expiry/FEFO or secondary quality checks are modeled.
- No scenario-specific recourse is allowed (single-stage model). Scenario probabilities are arbitrary and do not reflect a statistically estimated distribution.
- Working-capital is modeled as purchase cash plus route fixed costs, not a cash-flow schedule. VAT is not modeled.
- No marketing incremental-effect inference, patient-adherence product or real-options localization calculation exists yet.
- A MILP optimality certificate refers to the **mathematical formulation and tolerances**, not real-world business or regulatory correctness.

## Source traceability

- Challenge #16: user-supplied `toshkent_dolzarb_muammolar (1).pdf`, page 2.
- Current prescription-drug reference price rules (No. 3735, registered 25 Dec 2025): https://www.lex.uz/uz/acts/-7952162
- Prior No. 3242: https://www.lex.uz/uz/acts/-4854519 (ceased 28 March 2026).
- Decree PF-13: https://lex.uz/uz/acts/-7345588
