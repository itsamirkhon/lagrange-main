"""Replay uses the identical extraction path and a replay-configured client."""
from .openrouter import OpenRouterExtractor


class ReplayExtractor(OpenRouterExtractor):
    def __init__(self, client, model_id, schemas):
        if not client.replay:
            raise ValueError('ReplayExtractor requires a replay client')
        super().__init__(client, model_id, schemas)
