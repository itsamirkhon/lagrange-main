"""Read access to graphs committed by /ingest (state under the data root: graphs/<id>.json)."""
from __future__ import annotations

import json
from pathlib import Path

from lagrange_rx.ontology import ClientGraph


class GraphStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / 'graphs'

    def ids(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob('*.json')) if self.dir.is_dir() else []

    def get(self, graph_id: str) -> ClientGraph | None:
        if not graph_id.isalnum():  # batch ids are hex; blocks path traversal
            return None
        path = self.dir / (graph_id + '.json')
        if not path.is_file():
            return None
        return ClientGraph.model_validate(json.loads(path.read_text(encoding='utf-8')))
