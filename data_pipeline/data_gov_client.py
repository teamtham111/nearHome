"""Small, retrying client for official data.gov.sg dataset downloads.

The dataset-download API avoids hundreds of paginated requests for the HDB
transaction snapshot and is also the supported path for the very large SLA
National Map Line GeoJSON.  Downloads are staged and atomically published so
a failed refresh cannot replace the last known-good raw snapshot.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin

import httpx

API_ROOT = "https://api-open.data.gov.sg/v1/public/api/datasets"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class DataGovDownloadError(RuntimeError):
    """The official dataset could not be downloaded or validated."""


class DataGovClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("DATA_GOV_SG_API_KEY", "")
        self._client = client
        self._sleeper = sleeper

    @property
    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key} if self.api_key else {}

    def _get(self, url: str, *, provider_request: bool = True, timeout: float = 60.0) -> httpx.Response:
        headers = self.headers if provider_request else {}
        client = self._client or httpx.Client()
        owns_client = self._client is None
        try:
            for attempt in range(5):
                response = client.get(url, headers=headers, timeout=timeout, follow_redirects=True)
                if response.status_code not in RETRYABLE_STATUS:
                    response.raise_for_status()
                    return response
                if attempt == 4:
                    response.raise_for_status()
                retry_after = response.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(30.0, 2.0**attempt)
                self._sleeper(delay)
        finally:
            if owns_client:
                client.close()
        raise AssertionError("unreachable")

    @staticmethod
    def _download_url(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        for key in ("url", "downloadUrl", "download_url"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                return value
        return None

    def prepare_download(self, dataset_id: str, *, initiate: bool, max_polls: int = 20) -> str:
        if not dataset_id.startswith("d_"):
            raise ValueError("data.gov.sg dataset IDs must start with 'd_'")
        base = f"{API_ROOT}/{dataset_id}/"
        if initiate:
            response = self._get(urljoin(base, "initiate-download"))
            try:
                payload = response.json()
            except ValueError as exc:
                raise DataGovDownloadError("data.gov.sg initiate-download returned invalid JSON") from exc
            code = payload.get("code") if isinstance(payload, dict) else None
            if code not in (None, 0):
                raise DataGovDownloadError("data.gov.sg rejected the dataset download request")

        for poll_number in range(max_polls):
            response = self._get(urljoin(base, "poll-download"))
            try:
                payload = response.json()
            except ValueError as exc:
                raise DataGovDownloadError("data.gov.sg poll-download returned invalid JSON") from exc
            url = self._download_url(payload)
            if url:
                return url
            code = payload.get("code") if isinstance(payload, dict) else None
            message = payload.get("errMsg") if isinstance(payload, dict) else None
            if code not in (None, 0) and not _looks_pending(message):
                raise DataGovDownloadError("data.gov.sg could not prepare the requested dataset")
            if poll_number + 1 < max_polls:
                self._sleeper(min(30.0, 2.0 + poll_number))
        raise DataGovDownloadError("data.gov.sg dataset download did not become ready before the poll limit")

    def download(
        self,
        dataset_id: str,
        destination: Path,
        *,
        initiate: bool,
        expected_suffix: str | None = None,
    ) -> dict[str, object]:
        """Download and atomically publish one complete official snapshot."""

        url = self.prepare_download(dataset_id, initiate=initiate)
        response = self._get(url, provider_request=False, timeout=180.0)
        content = response.content
        if not content:
            raise DataGovDownloadError("data.gov.sg returned an empty dataset file")

        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = _extract_zip_member(content, expected_suffix) if content.startswith(b"PK\x03\x04") else content
        if not payload:
            raise DataGovDownloadError("the downloaded dataset archive contained no usable data")

        fd, staged_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        staged = Path(staged_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
        return {
            "dataset_id": dataset_id,
            "output": str(destination),
            "bytes": len(payload),
            "content_type": response.headers.get("content-type"),
        }


def _looks_pending(message: object) -> bool:
    text = str(message or "").casefold()
    return not text or any(word in text for word in ("pending", "prepar", "process", "not ready"))


def _extract_zip_member(content: bytes, expected_suffix: str | None) -> bytes:
    with tempfile.TemporaryFile() as buffer:
        buffer.write(content)
        buffer.seek(0)
        with zipfile.ZipFile(buffer) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            if expected_suffix:
                preferred = [item for item in files if item.filename.casefold().endswith(expected_suffix.casefold())]
                files = preferred or files
            if not files:
                return b""
            selected = min(files, key=lambda item: (item.filename.count("/"), item.filename))
            if selected.file_size > 1_500_000_000:
                raise DataGovDownloadError("the downloaded archive member exceeds the 1.5 GB safety limit")
            return archive.read(selected)


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    fd, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
