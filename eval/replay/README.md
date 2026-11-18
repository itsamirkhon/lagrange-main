# Recorded synthetic replay

The JSON response was recorded from OpenRouter `google/gemini-2.5-flash` using a synthetic supplier quotation and the schema in `lagrange_rx.ingest.config`. No client document or credential is included. The replay validates the original response and its evidence locally; it does not contact OpenRouter.

From the repository root, install the project with test dependencies (`python -m pip install -e '.[test]'`), then run:

```sh
python -m pytest -q tests/test_recorded_replay.py
```

The cache key includes the model, prompt, schema, output-token limit and exact input text. Changes to any of them require a new recording; a cache miss is reported explicitly and does not fall back to the network.
