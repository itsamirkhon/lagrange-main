"""Simulated human: answers the Ingest confirmation queue from simulation truth.

This is the only place (besides other eval modules) allowed to read `sim/truth`. It never runs in the
product. Every answer is logged with the evidence it was based on; the number of answers is the
human-load metric. The human only sees what a person would see (question prompt, facts, options)
and checks it against "their books" (truth): a group whose summary disagrees with the truth is
rejected, never silently confirmed.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

TRUTH = Path(__file__).resolve().parents[1] / 'sim' / 'truth'
CENT = Decimal('0.01')


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT)


class SimHuman:
    def __init__(self, truth_dir: Path = TRUTH, name: str = 'simulated_human'):
        self.name = name
        self.t = {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in Path(truth_dir).glob('*.json')
                  if p.stem != 'calibration_report'}
        self.log: list[dict] = []

    # --- group confirmation: compare the extracted summary with the books --------------------------------
    def _check_group(self, group: str, f: dict) -> list[str]:
        t = self.t
        problems = []

        def eq(label, got, want):
            if got != want:
                problems.append(f'{label}: extracted {got!r}, books {want!r}')

        if group == 'sales':
            eq('rows', f['rows'], len(t['sales']))
            eq('packs', f['packs'], sum(s['qty_packs'] for s in t['sales']))
            eq('revenue ex VAT', _money(f['revenue_ex_vat_uzs']), _money(sum(Decimal(str(s['revenue_ex_vat_uzs'])) for s in t['sales'])))
            names = {s['sku_id']: s['trade_name'] for s in t['skus']}
            want = {}
            for s in t['sales']:
                want[names[s['sku_id']]] = want.get(names[s['sku_id']], 0) + s['qty_packs']
            eq('packs by product', {k: v for k, v in f['packs_by_product'].items()}, dict(sorted(want.items())))
        elif group == 'master':
            eq('products', f['product_names'], sorted(s['trade_name'] for s in t['skus']))
            eq('suppliers', f['supplier_names'], sorted(s['name'] for s in t['suppliers']))
            eq('carriers', f['carrier_names'], sorted(c['name'] for c in t['carriers']))
        elif group == 'invoices':
            eq('shipments', f['shipments'], len(t['shipments']))
            eq('lots', f['lots'], len(t['lots']))
            eq('packs', f['packs'], sum(s['total_packs'] for s in t['shipments']))
        elif group == 'customs':
            eq('entries', f['entries'], len(t['customs']))
            eq('GTD numbers', f['refs'], sorted(c['gtd_number'] for c in t['customs']))
            want = sum(Decimal(str(c['landed_cost_ex_vat_uzs'])) for c in t['customs'])
            if abs(Decimal(f['landed_cost_ex_vat_uzs']) - want) > Decimal('1'):
                problems.append(f"landed cost: extracted {f['landed_cost_ex_vat_uzs']}, books {want}")
        elif group == 'routes':
            codes = {r['route_id']: r['route_code'] for r in t['routes']}
            supplier_name = {s['supplier_id']: s['name'] for s in t['suppliers']}
            pairs = {f"{codes[s['route_id']]} | {supplier_name[s['supplier_id']]}" for s in t['shipments']}
            pairs |= {f'{code} | -' for code in set(codes.values()) - {p.split(' | ')[0] for p in pairs}}
            eq('corridor / supplier pairs', f['corridor_supplier'], sorted(pairs))
            eq('customs posts', f['customs_posts'], sorted({r['customs_post'] for r in t['routes']}))
        elif group == 'quotes':
            eq('quote numbers', f['quote_ids'], sorted(q['quote_number'] for q in t['quotes']))
        elif group == 'contracts':
            supplier_ids = {s['supplier_id'] for s in t['suppliers']}
            eq('supplier contracts', f['contract_ids'], sorted(c['contract_number'] for c in t['contracts'] if c['party_id'] in supplier_ids))
        elif group == 'registry':
            eq('registry numbers', f['registration_numbers'], sorted(e['registry_record_no'] for e in t['registry_events']))
        elif group == 'costs':
            eq('rows', f['rows'], len(t['costs']))
            eq('total', _money(f['total_uzs']), _money(sum(Decimal(str(c['amount_uzs'])) for c in t['costs'])))
        else:
            problems.append(f'unknown group {group!r}')
        return problems

    # --- cost attribution: which books does this label correspond to ------------------------------------
    def _cost_answer(self, q: dict) -> tuple[dict, str]:
        f, t = q['facts'], self.t
        by_sku = {}
        for c in t['costs']:
            g = by_sku.setdefault(c['sku_id'], [0, Decimal(0)])
            g[0] += 1
            g[1] += Decimal(str(c['amount_uzs']))
        match = [k for k, (n, total) in by_sku.items() if n == f['acts'] and _money(total) == _money(f['total_uzs'])]
        if len(match) != 1:
            return {'allocation': 'unallocated'}, f'could not identify the books group (candidates {match}); left unallocated'
        truth_sku = match[0]
        names = {s['sku_id']: s['trade_name'] for s in t['skus']}
        ours = {name: sid for sid, name in q['options']['sku_names'].items()}
        if truth_sku in names:
            return {'allocation': 'direct', 'product': ours[names[truth_sku]]}, f'books attribute these acts to {names[truth_sku]}'
        background = [ours[s['trade_name']] for s in t['skus'] if not s.get('is_focus')]
        return ({'allocation': 'shared_by_volume', 'scope': sorted(background)},
                f'books class this pool as {truth_sku} (all non-focus SKUs); the books state no split rule, packs sold chosen')

    # --- public ----------------------------------------------------------------------------------------
    def answer(self, question: dict) -> dict:
        kind = question['kind']
        if kind == 'confirm_group':
            problems = self._check_group(question['group'], question['facts'])
            answer = {'decision': 'reject', 'problems': problems} if problems else {'decision': 'confirm'}
            basis = 'summary matches the books' if not problems else '; '.join(problems)
        elif kind == 'cost_attribution':
            answer, basis = self._cost_answer(question)
        elif kind == 'contract_effective_from':
            number = question['facts']['contract']
            contract = next(c for c in self.t['contracts'] if c['contract_number'] == number)
            answer, basis = {'valid_from': contract['effective_from']}, f'books: effective from {contract["effective_from"]}'
        else:
            answer, basis = {'decision': 'unknown'}, f'no rule for question kind {kind!r}'
        result = {'question_id': question['question_id'], 'answer': answer, 'answered_by': self.name}
        self.log.append({**result, 'kind': kind, 'basis': basis})
        return result

    def answer_all(self, questions: list[dict]) -> list[dict]:
        return [self.answer(q) for q in questions]

    @property
    def load(self) -> int:
        return len(self.log)
