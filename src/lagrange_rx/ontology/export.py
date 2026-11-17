"""Render graph references as Mermaid without exposing document contents."""
import json
from .graph import ClientGraph

def to_mermaid(graph: ClientGraph) -> str:
    graph.validate_references()
    indexes = graph.indexes()
    nodes = {(collection, key): f'n{i}' for i, (collection, key) in enumerate((collection, key) for collection, index in indexes.items() for key in index)}
    lines = ['graph LR']
    for (collection, key), node in nodes.items():
        lines.append(f'    {node}[{json.dumps(collection + ": " + str(key), ensure_ascii=False)}]')
    for collection, refs in graph._refs.items():
        if collection not in graph._keys:
            continue
        for item in getattr(graph, collection):
            source = nodes[(collection, getattr(item, graph._keys[collection]))]
            for field, target in refs.items():
                values = getattr(item, field)
                for value in (values if isinstance(values, list) else [values]):
                    if value is not None:
                        lines.append(f'    {source} -->|{field}| {nodes[(target, value)]}')
    return '\n'.join(lines)
