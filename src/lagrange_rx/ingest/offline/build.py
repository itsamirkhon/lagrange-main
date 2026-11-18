"""Headless Ingest: a folder of client documents -> a Draft of unconfirmed ontology entities.

Deterministic; no network, no LLM, no simulation internals. Links are made only through
document numbers (GTD <-> invoice <-> lot <-> sales), names and customs posts. Anything a
document does not state is left unknown (None) or becomes a question - never a guess.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from ...ontology import (
    Carrier, Channel, Contract, CostItem, Currency, CustomsEntry, FxRate, LeadTime, Lot, PaymentTerms,
    Product, ProductionSite, Provenance, Quote, RegistryRecord, Region, Route, SalesRecord, Shipment,
    SourceDocument, Supplier,
)
from ..normalize import normalize_name
from . import finance_docs as fd
from . import sales as sl
from . import supply_docs as sd
from .draft import Draft, assumed, question, slug, stamp, unconfirmed
from .readers import read_folder

CATEGORY_LAYER = 'below_line'
_MODES = (('auto', 'road'), ('rail', 'rail'), ('air', 'air'))


class Names:
    """Product names as written in documents; exact match, else a unique truncated-prefix match."""
    def __init__(self, names):
        self.by_norm = {normalize_name(n): n for n in names}

    def resolve(self, raw: str) -> str | None:
        key = normalize_name(raw)
        if key in self.by_norm:
            return self.by_norm[key]
        hits = [v for k, v in self.by_norm.items() if len(key) >= 8 and k.startswith(key)]
        return hits[0] if len(hits) == 1 else None


def _parse(docs, d: Draft) -> dict:
    facts = defaultdict(list)
    for doc in docs:
        try:
            _parse_one(doc, facts)
        except (ValueError, KeyError, IndexError, StopIteration, AttributeError) as exc:
            d.issue('unreadable', 'warning', f'{doc.name}: not parsed ({type(exc).__name__}: {exc})', [doc.document_id])
        if doc.kind == 'image':
            d.issue('unreadable', 'warning', f'{doc.name}: image, OCR unavailable offline', [doc.document_id])
    return facts


def _parse_one(doc, facts) -> None:
    c = doc.doc_class
    if c == 'sales_export':
        facts['sales'] += [dict(r, doc=doc) for r in sl.parse_1c(doc.tables[0])]
    elif c == 'regional_report':
        facts['regional'] += [dict(r, doc=doc) for r in sl.parse_regional(doc.tables[0], doc.sheets[0])]
    elif c == 'invoice':
        facts['invoices'] += [dict(sd.parse_invoice_page(p), doc=doc) for p in doc.pages]
    elif c == 'customs':
        rows = [r for t in doc.tables for r in sd.parse_customs_table(t)] if doc.kind == 'xlsx' else \
               [r for p in doc.pages for r in sd.parse_customs_pdf(p)]
        facts['customs_' + doc.kind] += [dict(r, doc=doc) for r in rows]
    elif c == 'stock':
        for name, rows in zip(doc.sheets, doc.tables):
            if name == 'Партии':
                facts['stock'] += [dict(r, doc=doc) for r in sd.parse_stock_lots(rows)]
    elif c == 'carrier_quote':
        if doc.kind == 'xlsx':
            year = re.search(r'\d{4}', doc.sheets[0])
            facts['rates'] += [dict(r, doc=doc, year=int(year.group()) if year else 0) for r in sd.parse_rates(doc.tables[0])]
        else:
            facts['carrier_quotes'] += [dict(r, doc=doc) for p in doc.pages for r in sd.parse_carrier_quotes(p)]
    elif c == 'supplier_quote':
        facts['supplier_quotes'].append(dict(sd.parse_supplier_quote(doc.pages[0]), doc=doc))
    elif c == 'contract':
        facts['contracts'] += [dict(sd.parse_contract_page(p), doc=doc) for p in doc.pages]
    elif c == 'payments':
        for name, rows in zip(doc.sheets, doc.tables):
            if name == 'Расходы':
                facts['expenses'] += [dict(r, doc=doc) for r in fd.parse_expenses(rows)]
            else:
                facts['credit_sheets'].append(doc.name)
    elif c == 'registry_extract' and doc.kind == 'csv':
        facts['registry'] += [dict(r, doc=doc) for r in fd.parse_registry(doc.tables[0])]


def _master(d: Draft, facts):
    """Products, regions, channels, currencies, suppliers, carriers."""
    first_doc = {}
    for key in ('sales', 'stock', 'registry'):
        for r in facts[key]:
            first_doc.setdefault(r['product'], r['doc'])
    for inv in facts['invoices']:
        for lot in inv['lots']:
            first_doc.setdefault(lot['product'], inv['doc'])
    skus = {}
    for name in sorted(first_doc):
        doc = first_doc[name]
        strength = re.search(r'(\d+(?:[.,]\d+)?)\s*(мг|mg)', name, re.I)
        pack = re.search(r'№\s*(\d+)', name)
        if not pack:
            d.issue('missing', 'warning', f'pack size not readable from name {name!r}', [doc.document_id])
            continue
        p = Product(sku_id=slug(name), trade_name=name, strength=f'{strength.group(1)} мг' if strength else None,
                    pack_size=int(pack.group(1)), rx=True, provenance=unconfirmed(doc, 'product name'),
                    field_provenance={'rx': assumed('the client is an importer of prescription drugs (task scope)', doc)})
        d.add('products', p, 'master')
        skus[name] = p.sku_id
    return skus


def _regions_channels(d, facts):
    regions, channels = {}, {}
    for r in facts['sales']:
        if r['region'] not in regions:
            regions[r['region']] = slug(r['region'])
            d.add('regions', Region(region_id=regions[r['region']], name=r['region'], provenance=unconfirmed(r['doc'], 'region name')), 'master')
        ch = sl.CHANNELS.get(r['channel'].casefold())
        if ch is None:
            d.issue('missing', 'warning', f"unknown channel {r['channel']!r}", [r['doc'].document_id])
        elif ch not in channels:
            channels[ch] = ch
            d.add('channels', Channel(name=ch, provenance=unconfirmed(r['doc'], 'channel name')), 'master')
    return regions, channels


def _suppliers_carriers(d, facts):
    """Supplier / carrier / production-site entities; returns lookup maps."""
    rates = sorted(facts['rates'], key=lambda r: r['year'])
    latest = {r['code']: r for r in rates}  # later year wins
    carriers = {}
    for r in latest.values():
        if r['carrier'] not in carriers:
            mode = next((m for prefix, m in _MODES if r['mode'].casefold().startswith(prefix)), None)
            if mode is None:
                d.issue('missing', 'warning', f"transport mode {r['mode']!r} not recognised", [r['doc'].document_id])
                continue
            cid = slug(r['carrier'])
            carriers[r['carrier']] = cid
            d.add('carriers', Carrier(carrier_id=cid, name=r['carrier'], modes=[mode], provenance=unconfirmed(r['doc'], 'rate table')), 'master')
    codes = {q['supplier']: q['supplier_code'] for q in facts['supplier_quotes']}
    names = {}
    for inv in facts['invoices']:
        if inv['supplier']:
            names.setdefault(inv['supplier'], inv['doc'])
    for q in facts['supplier_quotes']:
        names.setdefault(q['supplier'], q['doc'])
    for c in facts['contracts']:
        if c['party'] not in carriers:
            names.setdefault(c['party'], c['doc'])
    suppliers = {}
    for name in sorted(names):
        terms = _terms_for(name, facts)
        if terms is None:
            d.issue('missing', 'blocking', f'payment terms of supplier {name!r} not found or not understood', [names[name].document_id])
            continue
        country = re.search(r'\(([^,)]+),', name)
        sid = codes.get(name) or slug(name)
        suppliers[name] = sid
        d.add('suppliers', Supplier(
            supplier_id=sid, name=name, country=country.group(1) if country else 'unknown',
            payment_terms=PaymentTerms(supplier_days=terms[0], prepayment_share=terms[1], provenance=unconfirmed(names[name], 'contract terms')),
            provenance=unconfirmed(names[name], 'supplier name')), 'master')
    return carriers, suppliers, latest


def _terms_for(name, facts):
    contracts = sorted((c for c in facts['contracts'] if c['party'] == name), key=lambda c: c['signed'])
    for text in [c['payment_terms'] for c in reversed(contracts)] + [q['payment_terms'] for q in facts['supplier_quotes'] if q['supplier'] == name and q['payment_terms']]:
        parsed = sd.parse_payment_terms(text)
        if parsed:
            return parsed
    return None


def _customs(d, facts, invoices):
    table, pdf = {r['gtd']: r for r in facts['customs_xlsx']}, {r['gtd']: r for r in facts['customs_pdf']}
    entries = {}
    for gtd, row in table.items():
        inv = invoices.get(row['invoice'])
        if inv is None:
            d.issue('missing', 'blocking', f'GTD {gtd}: invoice {row["invoice"]} not found', [row['doc'].document_id])
            continue
        p = pdf.get(gtd)
        if p and (p['invoice'], p['rate'], p['landed_uzs']) != (row['invoice'], row['rate'], row['landed_uzs']):
            d.issue('contradiction', 'blocking', f'GTD {gtd}: PDF and spreadsheet disagree', [row['doc'].document_id, p['doc'].document_id])
        cip_fx = p['cip_fx'] if p else (row['cip_uzs'] / row['rate']).quantize(Decimal('0.01'))
        if abs(cip_fx * row['rate'] - row['cip_uzs']) > 1:
            d.issue('arithmetic', 'warning', f'GTD {gtd}: CIP in currency x rate differs from CIP UZS by more than 1 UZS', [row['doc'].document_id])
        fees = row['landed_uzs'] - cip_fx * row['rate'] - row['duty_uzs']
        if fees < 0:
            d.issue('arithmetic', 'blocking', f'GTD {gtd}: landed cost is below CIP + duty', [row['doc'].document_id])
            continue
        entry = CustomsEntry(
            entry_ref=gtd, entry_date=row['date'], cip_value_fx=cip_fx, currency=p['currency'] if p else 'USD',
            fx_rate_on_entry=row['rate'], duty_uzs=row['duty_uzs'], fees_uzs=fees, packs=inv['total_packs'],
            provenance=unconfirmed(row['doc'], f'GTD {gtd}'),
            field_provenance={'fees_uzs': assumed('landed cost ex VAT (D7) minus CIP and duty: includes clearance fees AND inbound freight, which D7 does not separate', row['doc'])})
        d.add('customs_entries', entry, 'customs')
        d.add('fx_rates', FxRate(currency=entry.currency, date=row['date'], rate_uzs=row['rate'], source='customs summary, CBU rate on GTD date',
                                 provenance=unconfirmed(row['doc'], f'GTD {gtd}')), 'customs')
        entries[row['invoice']] = (entry, row)
    return entries


def _shipments_lots(d, facts, skus, suppliers, entries, latest):
    stock = {r['lot']: r for r in facts['stock']}
    post_code = {r['post']: code for code, r in latest.items()}
    routes = {}
    shipments = {}
    for inv in facts['invoices']:
        number, doc = inv['invoice'], inv['doc']
        pair = entries.get(number)
        supplier = suppliers.get(inv['supplier'])
        if pair is None or supplier is None:
            d.issue('missing', 'blocking', f'invoice {number}: no customs entry or unknown supplier; shipment not built', [doc.document_id])
            continue
        entry, row = pair
        code = post_code.get(row['post'])
        if code is None:
            d.issue('missing', 'blocking', f"invoice {number}: customs post {row['post']!r} matches no carrier rate", [doc.document_id])
            continue
        route_id = f'{code}/{supplier}'
        routes.setdefault(route_id, (code, supplier))
        lots = []
        for lot in inv['lots']:
            sku = skus.get(lot['product'])
            st = stock.get(lot['lot'])
            if sku is None or st is None:
                d.issue('missing', 'blocking', f"lot {lot['lot']}: product or stock record missing", [doc.document_id])
                continue
            if (st['expiry'], st['arrival'], st['initial']) != (lot['expiry'], inv['arrival'], lot['qty']):
                d.issue('contradiction', 'warning', f"lot {lot['lot']}: invoice and stock register disagree", [doc.document_id, st['doc'].document_id])
            d.add('lots', Lot(lot_number=lot['lot'], product=sku, production_date=lot['produced'], expiry_date=lot['expiry'],
                              qty_packs=lot['qty'], shipment=number, remaining_packs=st['remaining'],
                              provenance=unconfirmed(doc, f'packing list of {number}'),
                              field_provenance={'remaining_packs': unconfirmed(st['doc'], f"stock register, waybill {st['waybill']}")}), 'invoices',
                  waybill=st['waybill'])
            lots.append(lot['lot'])
        d.add('shipments', Shipment(shipment_id=number, route=route_id, dispatch_date=inv['dispatch'], arrival_date=inv['arrival'],
                                    packs=inv['total_packs'], lots=lots, customs_entry=entry.entry_ref,
                                    provenance=unconfirmed(doc, f'invoice {number}')), 'invoices')
        shipments[number] = route_id
    return routes


def _routes(d, routes, latest, carriers, suppliers_by_id, facts):
    used = {code for code, _ in routes.values()}
    sites = {}
    approved = defaultdict(set)
    for lot in d.of('lots'):
        ship = next(s for s in d.of('shipments') if s.shipment_id == lot.shipment)
        approved[ship.route.split('/', 1)[1]].add(lot.product)
    for route_id, (code, supplier) in sorted(routes.items()):
        site = f'{supplier}-site'
        if site not in sites:
            s = suppliers_by_id[supplier]
            sites[site] = d.add('production_sites', ProductionSite(
                site_id=site, supplier=supplier, country=s.country, approved_for=sorted(approved[supplier]),
                provenance=unconfirmed(facts['rates'][0]['doc'], 'derived from invoices of this supplier')), 'routes')
        _add_route(d, route_id, latest[code], carriers, site)
    for code in sorted(set(latest) - used):
        _add_route(d, code, latest[code], carriers, None)


def _add_route(d, route_id, r, carriers, site):
    cid = carriers[r['carrier']]
    mode = next(m for prefix, m in _MODES if r['mode'].casefold().startswith(prefix))
    d.add('routes', Route(route_id=route_id, production_site=site, carrier=cid, transport_mode=mode, customs_post=r['post'],
                          lead_time_days=LeadTime(p50=r['days']), freight_per_shipment_usd=r['usd_per_shipment'],
                          freight_per_pack_usd=r['usd_per_pack'], provenance=unconfirmed(r['doc'], f"rate table {r['code']}")), 'routes')


def _sales(d, facts, skus, regions, channels, lots):
    count = 0
    for r in facts['sales']:
        sku, ch, lot = skus.get(r['product']), sl.CHANNELS.get(r['channel'].casefold()), lots.get(r['lot'])
        if sku is None or ch is None or lot is None or lot != sku:
            d.issue('missing', 'warning', f"sale {r['doc'].name} row {r['row']}: product/channel/lot not resolvable or lot of another product", [r['doc'].document_id])
            continue
        d.add('sales_records', SalesRecord(period=r['period'], product=sku, region=regions[r['region']], channel=ch, qty_packs=r['qty'],
                                           price_ex_vat=r['price'], lot=r['lot'], provenance=unconfirmed(r['doc'], f"row {r['row']}")), 'sales')
        count += 1
    return count


def _reconcile_regional(d, facts):
    """Regional summaries re-state the 1C export by region; compare totals per (period, lot)."""
    one_c, regional = defaultdict(lambda: [0, Decimal(0)]), defaultdict(lambda: [0, Decimal(0)])
    for r in facts['sales']:
        one_c[(r['period'], r['lot'])][0] += r['qty']
        one_c[(r['period'], r['lot'])][1] += r['amount']
    for r in facts['regional']:
        regional[(r['period'], r['lot'])][0] += r['qty']
        regional[(r['period'], r['lot'])][1] += r['amount']
    bad = [k for k in set(one_c) | set(regional) if one_c[k] != regional[k]] if regional else []
    for key in sorted(bad)[:20]:
        d.issue('contradiction', 'warning', f'1C export and regional summaries differ for {key}: {one_c[key]} vs {regional[key]}', [])
    d.links['regional_reconciliation'] = {'keys': len(set(one_c) | set(regional)), 'mismatches': len(bad)}


def _contracts(d, facts, suppliers, carriers):
    contracts = {c['contract']: c for c in facts['contracts'] if c['party'] in suppliers}
    carrier_contracts = [c['contract'] for c in facts['contracts'] if c['party'] in carriers]
    for number, c in sorted(contracts.items()):
        parent = re.sub(r'-A\d+$', '', number)
        addendum = parent != number
        parsed = sd.parse_payment_terms(c['payment_terms'])
        if parsed is None:
            d.issue('missing', 'warning', f'contract {number}: payment terms not understood', [c['doc'].document_id])
            continue
        d.add('contracts', Contract(
            contract_id=number, supplier=suppliers[c['party']], version='A' + number.rsplit('-A', 1)[1] if addendum else 'base',
            signed_on=c['signed'], valid_from=c['signed'], valid_to=c['valid_to'], unit_price=c['price'], currency=c['currency'],
            moq_packs=c['moq'], payment_terms=PaymentTerms(supplier_days=parsed[0], prepayment_share=parsed[1], provenance=unconfirmed(c['doc'], number)),
            supersedes=parent if addendum and parent in contracts else None, provenance=unconfirmed(c['doc'], number),
            field_provenance={'valid_from': assumed('the contract states only its signing date; start of validity taken equal to it (an addendum may start later)', c['doc'])}),
            'contracts')
        if addendum:
            d.questions.append(question('contract_effective_from', number,
                                        f'Addendum {number} (price {c["price"]} {c["currency"]}) states only the signing date {c["signed"]}. From what date does it apply?',
                                        {'contract': number, 'signed_on': str(c['signed']), 'note': c['note']}, {'valid_from': 'ISO date'}))
    d.links['carrier_contracts_not_modelled'] = carrier_contracts


def _quotes(d, facts, skus_names: Names, skus, suppliers, carriers, latest):
    code_carrier = {code: carriers[r['carrier']] for code, r in latest.items()}
    for q in facts['supplier_quotes']:
        supplier = suppliers.get(q['supplier'])
        for item in q['items']:
            name = skus_names.resolve(item['product'])
            if supplier is None or name is None:
                d.issue('missing', 'warning', f"quote {item['quote']}: supplier or product not resolvable ({item['product']!r})", [q['doc'].document_id])
                continue
            d.add('quotes', Quote(quote_id=item['quote'], supplier=supplier, product=skus[name],
                                  issued_on=q['date'] if item['quote'] == q['quote'] else None, valid_until=item['valid_until'],
                                  unit_price=item['price'], currency=item['currency'], incoterms=q['incoterms'], moq_packs=item['moq'],
                                  lead_time_days=item['lead_days'], vat_status='unknown', provenance=unconfirmed(q['doc'], item['quote'])), 'quotes')
    for q in facts['carrier_quotes']:
        carrier = code_carrier.get(q['route_code'])
        if carrier is None:
            d.issue('missing', 'warning', f"carrier quote {q['quote']}: unknown route code {q['route_code']}", [q['doc'].document_id])
            continue
        d.add('quotes', Quote(quote_id=q['quote'], carrier=carrier, route_code=q['route_code'], unit_price=q['rate_per_pack'],
                              currency=q['currency'], lead_time_days=q['lead_days'], vat_status=q['vat'],
                              provenance=unconfirmed(q['doc'], q['quote'])), 'quotes')


def _registry(d, facts, skus_names, skus):
    seen = {}
    for r in facts['registry']:
        if r['number'] in seen:
            if {k: v for k, v in seen[r['number']].items() if k != 'doc'} != {k: v for k, v in r.items() if k != 'doc'}:
                d.issue('contradiction', 'blocking', f"registry record {r['number']} differs between extracts", [r['doc'].document_id])
            continue
        seen[r['number']] = r
        name = skus_names.resolve(r['product'])
        if name is None:
            d.issue('missing', 'warning', f"registry {r['number']}: product {r['product']!r} unknown", [r['doc'].document_id])
            continue
        d.add('registry_records', RegistryRecord(
            product=skus[name], registration_number=r['number'], registry_date=r['effective_from'], rn_fx=r['rn'], rn_currency='UZS',
            filing_fx_rate=Decimal(1), customs_b_uzs=r['b'], chun_incl_vat=r['chun'], chchn_incl_vat=r['chchn'],
            provenance=unconfirmed(r['doc'], f"{r['event']}"),
            field_provenance={'registry_date': assumed('column "Действует с" (effective date) used as registry_date', r['doc']),
                              'filing_fx_rate': assumed('extract gives Rn in UZS, so the filing rate is 1', r['doc'])}), 'registry')
        if abs(r['rn'] + r['bx'] + r['b'] - r['base']) > Decimal('0.02'):
            d.issue('arithmetic', 'warning', f"registry {r['number']}: Rn + B + Bx differs from the base", [r['doc'].document_id])


def _costs(d, facts):
    labels = defaultdict(list)
    for r in facts['expenses']:
        if r['category'] is None:
            d.issue('missing', 'warning', f"expense {r['doc_no']}: article {r['article']!r} is not a known cost category", [r['doc'].document_id])
            continue
        item = d.add('cost_items', CostItem(category=r['category'], layer=CATEGORY_LAYER, amount_uzs=r['amount'], period=r['period'],
                                            driver='fixed', provenance=unconfirmed(r['doc'], f"row {r['row']}, act {r['doc_no']}"),
                                            field_provenance={'driver': assumed('monthly act without per-pack or per-shipment formula, treated as fixed per month', r['doc'])}),
                     'costs', label=r['label'], act=r['doc_no'])
        labels[r['label']].append(item)
    return labels


def _questions(d: Draft, labels, skus_names: Names, skus):
    summaries = {
        'sales': lambda es: {'rows': len(es), 'packs': sum(e.qty_packs for e in es), 'revenue_ex_vat_uzs': str(sum(e.qty_packs * e.price_ex_vat for e in es)),
                              'packs_by_product': _by(es, 'product', 'qty_packs')},
        'master': lambda es: {'collections': _count(d, 'master'), 'product_names': sorted(e.trade_name for e in d.of('products')),
                               'supplier_names': sorted(e.name for e in d.of('suppliers')), 'carrier_names': sorted(e.name for e in d.of('carriers'))},
        'invoices': lambda es: {'shipments': len(d.of('shipments')), 'lots': len(d.of('lots')), 'packs': sum(s.packs for s in d.of('shipments'))},
        'customs': lambda es: {'entries': len(d.of('customs_entries')), 'refs': sorted(e.entry_ref for e in d.of('customs_entries')),
                                'landed_cost_ex_vat_uzs': str(sum(e.legal_base_c_per_pack * e.packs for e in d.of('customs_entries')))},
        'routes': lambda es: {'route_ids': sorted(r.route_id for r in d.of('routes')), 'customs_posts': sorted({r.customs_post for r in d.of('routes')})},
        'quotes': lambda es: {'quote_ids': sorted(q.quote_id for q in d.of('quotes'))},
        'contracts': lambda es: {'contract_ids': sorted(c.contract_id for c in d.of('contracts'))},
        'registry': lambda es: {'registration_numbers': sorted(r.registration_number for r in d.of('registry_records'))},
        'costs': lambda es: {'rows': len(es), 'total_uzs': str(sum(e.amount_uzs for e in es)), 'labels': sorted(labels)},
    }
    groups = defaultdict(list)
    for e in d.entries:
        groups[e.group].append(e.entity)
    for group, entities in groups.items():
        facts = summaries[group](entities)
        d.questions.append(question('confirm_group', group, f'Confirm the {len(entities)} extracted items of group "{group}" '
                                    f'(source documents are cited on each item).', facts, ['confirm', 'reject'], group))
    for label, items in sorted(labels.items()):
        candidates = [sku for name, sku in skus.items() if normalize_name(name) == normalize_name(label)]
        d.questions.append(question('cost_attribution', label, f'Expense rows labelled {label!r} ({len(items)} acts): to which SKU and by which allocation rule do they belong?',
                                    {'label': label, 'acts': len(items), 'periods': [min(i.period for i in items), max(i.period for i in items)],
                                     'total_uzs': str(sum(i.amount_uzs for i in items)), 'categories': sorted({i.category for i in items}),
                                     'name_matches': candidates},
                                    {'allocation': ['direct', 'shared_by_volume', 'shared_by_revenue', 'unallocated'],
                                     'skus': sorted(skus.values()), 'sku_names': {v: k for k, v in sorted(skus.items())}}))


def _by(entities, key, qty):
    out = defaultdict(int)
    for e in entities:
        out[getattr(e, key)] += getattr(e, qty)
    return dict(sorted(out.items()))


def _count(d, group):
    out = defaultdict(int)
    for e in d.entries:
        if e.group == group:
            out[e.collection] += 1
    return dict(sorted(out.items()))


def _not_extracted(d, docs, facts):
    images = [x.name for x in docs if x.kind == 'image']
    d.missing('content of image documents (memos, carrier chats, waybill photos)', f'{len(images)} files {images}: OCR unavailable offline; an LLM-vision step is optional and was not run')
    d.missing('Product: INN, dosage form, manufacturer, registration certificate', 'no document states them next to the product name')
    d.missing('CreditLine: limit, annual rate, available_from', 'D10 shows only monthly balances, drawdowns and interest')
    d.missing('CustomsEntry: split of fees_uzs into clearance fees and inbound freight; vat_uzs', 'D7 states only CIP, duty and landed cost ex VAT')
    d.missing('Shipment.actual_freight_uzs', 'no document states the freight actually paid')
    d.missing('Route: incoterms, min/max shipment packs, shipments per year, capacity, activation cost, eligibility, p90 lead time', 'rate tables state only freight, transit days and customs post')
    d.missing('Route production_site for corridors without observed shipments', 'no invoice ties such a corridor to a producer')
    d.missing('Supplier.reliability; PaymentTerms.customer_days', 'not stated in any document')
    d.missing('PriceIndexationEvent, Warehouse capacity, DemandForecast, FxHedge', 'not stated, or mapping of registry event types to rules is a legal interpretation')
    d.missing('Carrier contracts (CNT-CAR-*)', 'Contract.supplier references suppliers only; carrier rates are taken from the rate tables and carrier quotes')
    d.missing('Quote.vat_status for supplier quotes; Quote.issued_on except the quote named in the document title', 'not stated')


def build_draft(folder, now: datetime | None = None) -> Draft:
    docs = read_folder(folder)
    d = Draft()
    stamped = stamp(now)
    for doc in docs:
        d.source_documents.append(SourceDocument(
            document_id=doc.document_id, file_name=doc.name, mime=doc.mime, pages=max(len(doc.pages), len(doc.tables), 1 if doc.kind in ('image', 'eml') else 0),
            uploaded_at=stamped, doc_class=None if doc.doc_class == 'unknown' else doc.doc_class,
            provenance=Provenance(kind='user_provided', source='document upload')))
    facts = _parse(docs, d)
    skus = _master(d, facts)
    names = Names(skus)
    regions, channels = _regions_channels(d, facts)
    carriers, suppliers, latest = _suppliers_carriers(d, facts)
    invoices = {i['invoice']: i for i in facts['invoices']}
    entries = _customs(d, facts, invoices)
    routes = _shipments_lots(d, facts, skus, suppliers, entries, latest)
    by_id = {s.supplier_id: s for s in d.of('suppliers')}
    if routes or latest:
        _routes(d, routes, latest, carriers, by_id, facts)
    lot_product = {l.lot_number: l.product for l in d.of('lots')}
    n_sales = _sales(d, facts, skus, regions, channels, lot_product)
    _reconcile_regional(d, facts)
    _contracts(d, facts, suppliers, carriers)
    _quotes(d, facts, names, skus, suppliers, carriers, latest)
    _registry(d, facts, names, skus)
    labels = _costs(d, facts)
    seen = {}
    for e in d.entries:
        if e.collection in ('quotes', 'customs_entries') and e.entity.currency not in seen:
            seen[e.entity.currency] = e.entity.provenance
    for code, prov in sorted(seen.items()):
        d.add('currencies', Currency(code=code, provenance=prov), 'master')
    d.links.update({'invoices': len(invoices), 'invoices_with_customs': len(entries), 'customs_without_invoice': len(facts['customs_xlsx']) - len(entries),
                    'lots': len(lot_product), 'sales_rows': len(facts['sales']), 'sales_linked_to_lot': n_sales})
    _questions(d, labels, names, skus)
    _not_extracted(d, docs, facts)
    return d
