"""Load and validate the versioned rule catalog at the data boundary."""
from pathlib import Path
import sys

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Rule


class RuleCatalog(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: str = Field(min_length=1)
    rules: list[Rule]

    @model_validator(mode='after')
    def unique_ids(self):
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate rule_id')
        return self


def load_rules(path: str | Path | None = None) -> RuleCatalog:
    if path is None:
        source = Path(__file__).resolve().parents[3] / 'rules' / 'uz_pharma_rules.yaml'
        path = source if source.is_file() else Path(sys.prefix) / 'rules' / 'uz_pharma_rules.yaml'
    with Path(path).open(encoding='utf-8') as stream:
        data = yaml.safe_load(stream)
    # Catalog is derived from the documented specification, not a direct legal source.
    if isinstance(data, dict) and isinstance(data.get('rules'), list):
        for rule in data['rules']:
            if isinstance(rule, dict):
                rule.setdefault('provenance', {'kind': 'assumption', 'source': 'docs/ONTOLOGY.md §5; original legal texts require review'})
    return RuleCatalog.model_validate(data)
