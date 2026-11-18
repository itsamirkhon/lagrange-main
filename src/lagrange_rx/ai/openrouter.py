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


def _post(url, payload, timeout, key):
    request = Request(url, data=json.dumps(payload).encode(), headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


DECISIONS_URL = 'https://openrouter.ai/api/alpha/decisions'
EMBEDDINGS_URL = 'https://openrouter.ai/api/v1/embeddings'
EMBED_BATCH = 32


class OpenRouterClient:
    def __init__(self, cache_dir, *, replay=False, api_key=None, budget_usd=0, max_request_cost_usd=0,
                 timeout=60, retries=2, transport=None, post=None):
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
        self.post = post or _post
        self.spent_usd = 0.0
        self._lock = threading.Lock()

    def _cache_read(self, path):
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def _cache_write(self, path, value):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.cache_dir, prefix='.response-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _guard_live(self):
        """Caller holds the lock. Raise unless a live request may be made."""
        if self.replay:
            raise ReplayMiss('No recorded response for request')
        if not self.api_key:
            raise ValueError('OPENROUTER_API_KEY is required')
        if self.max_request_cost_usd <= 0 or self.spent_usd + self.max_request_cost_usd > self.budget_usd:
            raise BudgetExceeded('Insufficient remaining budget for request')

    def _post_retry(self, url, payload):
        for attempt in range(self.retries + 1):
            try:
                return self.post(url, payload, self.timeout, self.api_key)
            except HTTPError as error:
                if error.code != 429 and not 500 <= error.code < 600 or attempt == self.retries:
                    raise
            except (URLError, TimeoutError):
                if attempt == self.retries:
                    raise
            time.sleep(min(2 ** attempt, 4))

    def _charge(self, response):
        cost = (response.get('usage') or {}).get('cost')
        cost = self.max_request_cost_usd if cost is None else float(cost)
        if cost < 0:
            raise ValueError('Provider returned invalid cost')
        self.spent_usd += cost
        return cost

    def decide(self, model_id, state, questions):
        """Closed-choice scoring through the Decisions API: {qid: {type, instructions, criteria}} -> raw answers.

        The result is the provider's ``answers`` mapping plus the served model version and cost.
        """
        if not model_id or not isinstance(state, str) or not state.strip() or not questions:
            raise ValueError('model, non-empty state and questions required')
        key = _digest(['decide', model_id, state, questions])
        path = self.cache_dir / ('decide-' + key + '.json')
        with self._lock:
            cached = self._cache_read(path)
            if cached is not None:
                return cached
            self._guard_live()
            start = time.monotonic()
            response = self._post_retry(DECISIONS_URL, {'model': model_id, 'state': state, 'questions': questions})
            result = {'answers': response['answers'], 'model_id': model_id, 'model_version': response.get('model', model_id),
                      'latency_ms': (time.monotonic() - start) * 1000, 'cost_usd': self._charge(response)}
            self._cache_write(path, result)
            return result

    def embed(self, model_id, texts):
        """One vector per text, cached per (model_id, text hash); only uncached texts are requested."""
        if not model_id or not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError('model and non-empty texts required')
        with self._lock:
            paths = [self.cache_dir / ('embed-' + _digest([model_id, t]) + '.json') for t in texts]
            vectors = [self._cache_read(p) for p in paths]
            missing = sorted({i for i, v in enumerate(vectors) if v is None}, key=lambda i: i)
            if missing:
                self._guard_live()
                unique = list(dict.fromkeys(texts[i] for i in missing))
                by_text = {}
                for start in range(0, len(unique), EMBED_BATCH):  # providers cap the inputs per request
                    chunk = unique[start:start + EMBED_BATCH]
                    if start:
                        self._guard_live()
                    response = self._post_retry(EMBEDDINGS_URL, {'model': model_id, 'input': chunk})
                    self._charge(response)
                    data = sorted(response['data'], key=lambda item: item.get('index', 0))
                    if len(data) != len(chunk):
                        raise ValueError('Provider returned a wrong number of embeddings')
                    by_text.update({text: item['embedding'] for text, item in zip(chunk, data)})
                for i in missing:
                    vectors[i] = by_text[texts[i]]
                    self._cache_write(paths[i], vectors[i])
            return vectors

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
