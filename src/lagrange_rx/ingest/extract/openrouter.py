"""Generative extraction with code-verified citations."""
from .extractor import Extractor, validate_evidence

PROMPT = ('Extract only values explicitly present in the document, preserving the original spelling. '
          'For every value return {"raw": "...", "evidence": {"page": 1, "quote": "exact quote"}}. '
          'Omit absent or unreadable values. Never infer values. Pages are numbered from 1.')


class OpenRouterExtractor(Extractor):
    def __init__(self, client, model_id, schemas):
        self.client, self.model_id, self.schemas = client, model_id, schemas

    def extract(self, doc_class, pages, *, image_data_url=None):
        if doc_class not in self.schemas:
            raise ValueError('Unknown document class')
        numbered = '\n\n'.join(f'Page {number}:\n' + '\n'.join(f'{i}: {line}' for i, line in enumerate(text.splitlines(), 1)) for number, text in sorted(pages.items()))
        response = self.client.chat(self.model_id, PROMPT, numbered, self.schemas[doc_class], image_data_url=image_data_url)
        accepted, issues = validate_evidence(response.content, pages)
        return accepted, issues, response
