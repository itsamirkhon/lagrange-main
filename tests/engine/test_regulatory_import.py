"""Tampering and path escapes cannot silently become a trusted data import."""
from datetime import date
from hashlib import sha256

import pytest

from lagrange_rx.engine.regulatory_gate import Source
from lagrange_rx.engine.regulatory_import import Manifest, ManifestRow, verify_manifest

ON = date(2026, 11, 18)


def make_manifest(raw: bytes):
    source = Source(system='REF_PRICE', record_id='REF-1', kind='reviewed_official',
                    url='https://mpa.uz/en/reference-prices', retrieved_on=ON,
                    reviewed_by='Reviewer', sha256=sha256(raw).hexdigest())
    return Manifest(rows=(ManifestRow(path='reference.csv', source=source),))


def test_verifies_bytes_and_rejects_mutation(tmp_path):
    raw = b'product,price\nfoo,2000\n'
    (tmp_path / 'reference.csv').write_bytes(raw)
    got = verify_manifest(make_manifest(raw), tmp_path, ON)
    assert got[0]['sha256'] == sha256(raw).hexdigest()
    (tmp_path / 'reference.csv').write_bytes(b'product,price\nfoo,1000\n')
    with pytest.raises(ValueError, match='hash mismatch'):
        verify_manifest(make_manifest(raw), tmp_path, ON)


def test_rejects_traversal_duplicate_and_bad_host(tmp_path):
    raw = b'x\n'
    (tmp_path / 'reference.csv').write_bytes(raw)
    m = make_manifest(raw)
    with pytest.raises((FileNotFoundError, ValueError)):
        verify_manifest(Manifest(rows=(m.rows[0].model_copy(update={'path': '../out.csv'}),)), tmp_path, ON)
    with pytest.raises(ValueError, match='duplicate'):
        verify_manifest(Manifest(rows=(m.rows[0], m.rows[0])), tmp_path, ON)
    bad = m.rows[0].source.model_copy(update={'url': 'https://mpa.uz.attacker.example/price'})
    with pytest.raises(ValueError, match='untrusted'):
        verify_manifest(Manifest(rows=(m.rows[0].model_copy(update={'source': bad}),)), tmp_path, ON)
