"""The single OpenRouter boundary, with an on-disk replay cache."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ReplayMiss(LookupError):
    pass


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelResponse:
    content: object
    model_id: str
    model_version: str
    latency_ms: float
    cost_usd: float


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _send(payload, timeout, key):
    request = Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(payload).encode(), headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


class OpenRouterClient:
    def __init__(self, cache_dir, *, replay=False, api_key=None, budget_usd=0, max_request_cost_usd=0,
                 timeout=60, retries=2, transport=None):
        if budget_usd < 0 or max_request_cost_usd < 0 or timeout <= 0 or retries < 0:
            raise ValueError('Invalid client limits')
        self.cache_dir = Path(cache_dir)
        self.replay = replay
        self.api_key = api_key if api_key is not None else os.environ.get('OPENROUTER_API_KEY')
        self.budget_usd = budget_usd
        self.max_request_cost_usd = max_request_cost_usd
        self.timeout = timeout
        self.retries = retries
        self.transport = transport or _send
        self.spent_usd = 0.0
        self._lock = threading.Lock()

    def chat(self, model_id, prompt, input_data, schema, *, image_data_url=None, max_tokens=4096):
        """Return a structured response; a cache hit never contacts the provider."""
        if not model_id or not prompt or not isinstance(schema, dict) or max_tokens <= 0:
            raise ValueError('model, prompt, JSON schema and positive max_tokens required')
        key = _digest([model_id, _digest([prompt, schema, max_tokens]), _digest([input_data, image_data_url])])
        path = self.cache_dir / (key + '.json')
        with self._lock:
            if path.exists():
                return ModelResponse(**json.loads(path.read_text(encoding='utf-8')))
            if self.replay:
                raise ReplayMiss('No recorded response for request')
            if not self.api_key:
                raise ValueError('OPENROUTER_API_KEY is required')
            if self.max_request_cost_usd <= 0 or self.spent_usd + self.max_request_cost_usd > self.budget_usd:
                raise BudgetExceeded('Insufficient remaining budget for request')
            contents = [{'type': 'text', 'text': str(input_data)}]
            if image_data_url:
                contents.append({'type': 'image_url', 'image_url': {'url': image_data_url}})
            payload = {'model': model_id, 'temperature': 0, 'max_tokens': max_tokens,
                       'messages': [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': contents}],
                       'response_format': {'type': 'json_schema', 'json_schema': {'name': 'extraction', 'strict': True, 'schema': schema}},
                       'usage': {'include': True}}
            start = time.monotonic()
            for attempt in range(self.retries + 1):
                try:
                    response = self.transport(payload, self.timeout, self.api_key)
                    break
                except HTTPError as error:
                    if error.code != 429 and not 500 <= error.code < 600 or attempt == self.retries:
                        raise
                except (URLError, TimeoutError):
                    if attempt == self.retries:
                        raise
                time.sleep(min(2 ** attempt, 4))
            # Unknown cost is conservatively charged at the configured request ceiling.
            cost = response.get('usage', {}).get('cost')
            cost = self.max_request_cost_usd if cost is None else float(cost)
            if cost < 0:
                raise ValueError('Provider returned invalid cost')
            self.spent_usd += cost
            result = ModelResponse(json.loads(response['choices'][0]['message']['content']), model_id,
                                   response.get('model', model_id), (time.monotonic() - start) * 1000, cost)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.cache_dir, prefix='.response-')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(asdict(result), stream, ensure_ascii=False)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            return result
