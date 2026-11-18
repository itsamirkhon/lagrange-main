"""The LLM classification path is exercised with a fake transport: no network, no invented recordings."""
import json

import pytest

from lagrange_rx.ai.classify import LlmClassifier, build_llm_classifier
from lagrange_rx.ai.openrouter import OpenRouterClient, ReplayMiss


def fake_transport(answer, calls):
    def send(payload, timeout, key):
        calls.append(payload)
        return {'model': 'fake/model-1', 'usage': {'cost': 0.001},
                'choices': [{'message': {'content': json.dumps({'doc_class': answer, 'confidence': .8})}}]}
    return send


def live_client(tmp_path, answer, calls):
    return OpenRouterClient(tmp_path, api_key='test-key', budget_usd=1, max_request_cost_usd=.1, transport=fake_transport(answer, calls))


def test_live_call_is_recorded_then_replayed_offline(tmp_path):
    calls = []
    live = LlmClassifier(live_client(tmp_path, 'memo', calls), 'fake/model')
    first = live.classify('scan.jpg', 'some text', 'image/jpeg')
    assert (first.doc_class, first.method) == ('memo', 'llm') and len(calls) == 1
    replay = LlmClassifier(OpenRouterClient(tmp_path, replay=True), 'fake/model')
    again = replay.classify('scan.jpg', 'some text', 'image/jpeg')
    assert again.doc_class == 'memo' and len(calls) == 1


def test_replay_without_recording_raises(tmp_path):
    replay = LlmClassifier(OpenRouterClient(tmp_path, replay=True), 'fake/model')
    with pytest.raises(ReplayMiss):
        replay.classify('x.pdf', 'text', '')


def test_out_of_set_answer_is_unknown_and_confidence_is_capped(tmp_path):
    result = LlmClassifier(live_client(tmp_path, 'spaceship', []), 'fake/model').classify('x.pdf', 't', '')
    assert result.doc_class == 'unknown'
    ok = LlmClassifier(live_client(tmp_path / 'b', 'invoice', []), 'fake/model').classify('x.pdf', 't', '')
    assert ok.confidence <= .85  # uncalibrated model self-report never reaches auto-accept territory


def test_request_carries_only_bounded_text_and_enumerated_schema(tmp_path):
    calls = []
    LlmClassifier(live_client(tmp_path, 'invoice', calls), 'fake/model', max_chars=50).classify('x.pdf', 'A' * 1000, '')
    payload = calls[0]
    assert len(payload['messages'][1]['content'][0]['text']) < 400
    enum = payload['response_format']['json_schema']['schema']['properties']['doc_class']['enum']
    assert 'invoice' in enum and 'unknown' in enum


def test_builder_modes(tmp_path, monkeypatch):
    assert build_llm_classifier('off', tmp_path) is None
    assert build_llm_classifier('replay', tmp_path).client.replay is True
    with pytest.raises(ValueError):
        build_llm_classifier('openrouter', tmp_path, budget_usd=0)
    live = build_llm_classifier('openrouter', tmp_path, budget_usd=.5)
    assert live.client.replay is False and live.model_id
    with pytest.raises(ValueError):
        build_llm_classifier('bogus', tmp_path)
