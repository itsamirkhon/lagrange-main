import pytest
from lagrange_rx.ingest.config import configured_extractor
from lagrange_rx.ai.openrouter import ReplayMiss


def test_replay_is_offline_and_requires_recording(tmp_path):
    extractor = configured_extractor(tmp_path, mode='replay')
    with pytest.raises(ReplayMiss):
        extractor.extract('contract', {1: 'A contract'})


def test_live_requires_explicit_budget(tmp_path):
    with pytest.raises(ValueError):
        configured_extractor(tmp_path, mode='openrouter', budget_usd=0)
