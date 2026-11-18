import json
import unittest
from tempfile import TemporaryDirectory

from lagrange_rx.ai.openrouter import OpenRouterClient, ReplayMiss, BudgetExceeded
from lagrange_rx.ingest.extract.extractor import validate_evidence


class ExtractTests(unittest.TestCase):
    def test_rejects_hallucinated_quote_and_invalid_page(self):
        value = {'price': {'raw': '7,13', 'evidence': {'page': 1, 'quote': 'Цена 7,13'}},
                 'currency': {'raw': 'USD', 'evidence': {'page': 1, 'quote': 'Currency USD'}},
                 'date': {'raw': '2026', 'evidence': {'page': 8, 'quote': 'Цена'}}}
        accepted, issues = validate_evidence(value, {1: 'Цена 7,13 за упаковку'})
        self.assertEqual(accepted, {'price': value['price']})
        self.assertEqual(len(issues), 2)

    def test_value_must_also_appear_in_quote(self):
        accepted, issues = validate_evidence({'price': {'raw': '99', 'evidence': {'page': 1, 'quote': 'Цена 7,13'}}}, {1: 'Цена 7,13'})
        self.assertEqual(accepted, {})
        self.assertEqual(len(issues), 1)

    def test_replay_and_budget(self):
        with TemporaryDirectory() as path:
            calls = []
            def transport(payload, timeout, key):
                calls.append(payload)
                return {'choices': [{'message': {'content': '{"ok": true}'}}], 'usage': {'cost': .01}, 'model': 'versioned-model'}
            client = OpenRouterClient(path, api_key='test', budget_usd=.1, max_request_cost_usd=.02, transport=transport)
            result = client.chat('model', 'prompt', 'input', {'type': 'object'})
            self.assertEqual(result.content, {'ok': True})
            self.assertEqual(result.model_version, 'versioned-model')
            self.assertEqual(len(calls), 1)
            replay = OpenRouterClient(path, replay=True)
            self.assertEqual(replay.chat('model', 'prompt', 'input', {'type': 'object'}).content, result.content)
            with self.assertRaises(ReplayMiss):
                replay.chat('model', 'different', 'input', {'type': 'object'})
            limited = OpenRouterClient(path, api_key='test', budget_usd=.001, max_request_cost_usd=.02, transport=transport)
            with self.assertRaises(BudgetExceeded):
                limited.chat('model', 'new', 'input', {'type': 'object'})


if __name__ == '__main__':
    unittest.main()


def test_absent_optional_field_does_not_create_hallucination_issue():
    from lagrange_rx.ingest.extract.extractor import validate_evidence
    result, issues = validate_evidence({'price': None}, {1: 'nothing stated'})
    assert result == {}
    assert issues == []
