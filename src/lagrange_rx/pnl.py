"""Conservative P&L for documented, single-SKU, lot-traced sales.

Required expense categories are supplied by the caller; the absence of an expense
record never means zero. Expenses are attributed per item through
`CostItem.allocation`: `direct` (own SKU only), `shared_by_volume` /
`shared_by_revenue` (split across all SKUs sold in the period) or `unallocated`
(blocks the result unless the caller explicitly attests attribution). Amounts are UZS excluding VAT. This narrow calculation
rejects cost allocations it cannot substantiate from the graph.
"""
from dataclasses import dataclass
from decimal import Decimal

from .ontology import ClientGraph


@dataclass(frozen=True)
class PnL:
    revenue: Decimal | None
    cost: Decimal | None
    margin: Decimal | None
    missing: tuple[str, ...]


def calculate_pnl(graph: ClientGraph, product: str, period: str, required_expenses: set[str], *, expenses_attributed_to_product: bool = False) -> PnL:
    graph.for_calculation()
    missing = []
    sales = [s for s in graph.sales_records if s.product == product and s.period == period]
    if not sales:
        missing.append(f'Нет данных продаж {product} за {period}')
    period_sales = [s for s in graph.sales_records if s.period == period]
    # Costs directly attributed to another SKU are irrelevant to this SKU.
    # A shared cost whose scope excludes this SKU is irrelevant to it as well.
    costs = [c for c in graph.cost_items if c.period == period
             and not (c.allocation == 'direct' and c.product != product)
             and not (c.scope is not None and product not in c.scope)]
    unallocated = [c for c in costs if c.allocation == 'unallocated']
    if unallocated and not expenses_attributed_to_product:
        missing.append('Не подтверждено распределение затрат на SKU')
        if any(s.product != product for s in period_sales):
            missing.append('Неизвестно распределение затрат между SKU')
    for category in sorted(required_expenses - {c.category for c in costs if c.layer == 'below_line'}):
        missing.append(f'Нет данных затрат {category} за {period}')
    def totals(scope):
        rows = [s for s in period_sales if scope is None or s.product in scope]
        return (sum((Decimal(s.qty_packs) for s in rows), Decimal(0)),
                sum((s.qty_packs * s.price_ex_vat for s in rows), Decimal(0)))
    total_volume, total_revenue = totals(None)
    sku_volume = sum((Decimal(s.qty_packs) for s in sales), Decimal(0))
    sku_revenue = sum((s.qty_packs * s.price_ex_vat for s in sales), Decimal(0))
    if any(c.allocation == 'shared_by_volume' and not totals(c.scope)[0] for c in costs):
        missing.append(f'Нет объёмов продаж за {period} для распределения общих затрат')
    if any(c.allocation == 'shared_by_revenue' and not totals(c.scope)[1] for c in costs):
        missing.append(f'Нет выручки за {period} для распределения общих затрат')
    if any(c.driver != 'fixed' or c.shipment is not None for c in costs):
        missing.append('Не определено распределение затрат с driver или shipment')
    if any(c.layer == 'acquisition' for c in costs):
        missing.append('Не определено, включены ли acquisition затраты в CIP')
    if graph.fx_hedges:
        missing.append('Не определено влияние хеджирования на себестоимость')

    lots = {lot.lot_number: lot for lot in graph.lots}
    shipments = {shipment.shipment_id: shipment for shipment in graph.shipments}
    customs = {entry.entry_ref: entry for entry in graph.customs_entries}
    basis = Decimal(0)
    for sale in sales:
        lot = lots.get(sale.lot)
        if lot is None or lot.product != product:
            missing.append('Нет привязки продажи к партии товара')
            continue
        shipment = shipments[lot.shipment]
        entry = customs[shipment.customs_entry]
        shipment_lots = [item for item in graph.lots if item.shipment == shipment.shipment_id]
        if (set(shipment.lots) != {item.lot_number for item in shipment_lots}
                or any(item.product != product for item in shipment_lots)
                or sum(item.qty_packs for item in shipment_lots) != shipment.packs
                or shipment.packs != entry.packs
                or sum(s.customs_entry == entry.entry_ref for s in graph.shipments) != 1):
            missing.append(f'Нет однозначного распределения таможенной стоимости поставки {shipment.shipment_id}')
            continue
        if sum(s.qty_packs for s in graph.sales_records if s.lot == lot.lot_number) > lot.qty_packs:
            missing.append(f'Продажи превышают объём партии {lot.lot_number}')
            continue
        basis += sale.qty_packs * entry.legal_base_c_per_pack
    if missing:
        return PnL(None, None, None, tuple(dict.fromkeys(missing)))
    revenue = sum((s.qty_packs * s.price_ex_vat for s in sales), Decimal(0))
    if not revenue:
        return PnL(revenue, None, None, ('Выручка равна нулю',))
    def attributed(cost):
        if cost.allocation == 'shared_by_volume':
            return cost.amount_uzs * sku_volume / totals(cost.scope)[0]
        if cost.allocation == 'shared_by_revenue':
            return cost.amount_uzs * sku_revenue / totals(cost.scope)[1]
        return cost.amount_uzs  # direct for this SKU, or unallocated attested by caller

    total_cost = basis + sum((attributed(c) for c in costs if c.layer == 'below_line'), Decimal(0))
    return PnL(revenue, total_cost, (revenue - total_cost) / revenue, ())
