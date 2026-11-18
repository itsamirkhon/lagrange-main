from pathlib import Path

import pytest

from lagrange_rx.ontology.rules import load_rules


def test_catalog_contains_documented_rules_and_corrected_references():
    catalog = load_rules()
    assert len(catalog.rules) == 17
    by_id = {rule.rule_id: rule for rule in catalog.rules}
    assert by_id['UZ-PKM185-LEGAL-BASE'].regulation == 'UZ-PKM185'
    assert by_id['UZ-PKM185-LEGAL-BASE'].status == 'needs_legal_review'
    assert by_id['UZ-INV-UNIFORM-WAREHOUSE-PRICE'].regulation is None
    assert by_id['UZ-INV-UNIFORM-WAREHOUSE-PRICE'].status == 'modeling_assumption'
    assert by_id['UZ-3735-P20-INDEXATION'].parameters['immediate_threshold'] == 0.05
    assert by_id['UZ-3735-P19-CEILING-FORMULA'].parameters['discrepancy_threshold'] == 0.01


def test_rejects_duplicate_rule_ids(tmp_path: Path):
    source = Path(__file__).parents[2] / 'rules' / 'uz_pharma_rules.yaml'
    text = source.read_text(encoding='utf-8')
    # Duplicate a complete item by duplicating the document's rules list in YAML.
    import yaml
    data = yaml.safe_load(text)
    data['rules'].append(data['rules'][0])
    path = tmp_path / 'duplicate.yaml'
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate'):
        load_rules(path)


def test_rejects_unknown_fields(tmp_path: Path):
    path = tmp_path / 'invalid.yaml'
    path.write_text('version: 1\nrules: []\nextra: true\n', encoding='utf-8')
    with pytest.raises(ValueError):
        load_rules(path)
