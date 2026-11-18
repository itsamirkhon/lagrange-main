"""Bounds on untrusted ingest requests and compressed archives."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from lagrange_rx.api.app import create_app
from lagrange_rx.ingest.intake import IntakeError, IntakeStore


def test_archive_rejects_large_member_count_without_expanding(tmp_path):
    contents = io.BytesIO()
    with zipfile.ZipFile(contents, 'w') as archive:
        for i in range(201):
            archive.writestr(f'{i:04d}.txt', 'x')
    with pytest.raises(IntakeError, match='too large'):
        IntakeStore(tmp_path).add_archive(contents.getvalue())


def test_endpoint_limits_file_count(tmp_path):
    with TestClient(create_app(tmp_path / 'data', serve_web=False)) as client:
        response = client.post('/ingest/batches', files=[
            ('files', (f'{i}.txt', b'x', 'text/plain')) for i in range(101)
        ])
    assert response.status_code == 413
