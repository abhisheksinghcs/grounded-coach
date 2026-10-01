"""Reusable ingestion: documents -> Azure AI Search keyword index.

Reads PDF/TXT/MD documents (from an Azure Blob container or a local folder),
chunks them into passages, creates the Search index if needed (fields match the
app's configured field mappings), and uploads the chunks. Idempotent: rerun it
after adding documents.

Usage
-----
Install the ingestion extras first:

    uv pip install -e ".[ingest]"      # or: pip install -e ".[ingest]"

Auth:
  * Blob access uses DefaultAzureCredential (run ``az login``).
  * Search writes use an admin key: set SEARCH_ADMIN_KEY, e.g.
      export SEARCH_ADMIN_KEY=$(az search admin-key show \
        --service-name secondchat -g MC-abhi --query primaryKey -o tsv)

Ingest from a blob container (reads AZURE_SEARCH_* + index from .env):

    python scripts/ingest.py --account secondchat --container ai-container

Ingest from a local folder:

    python scripts/ingest.py --local ./my-docs

Options:
  --index NAME       Override AZURE_SEARCH_INDEX for the target index.
  --recreate         Delete and recreate the index before uploading.
  --words-per-chunk  Passage size in words (default 220).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
from pathlib import Path

import httpx

# Make `coach` importable when run as `python scripts/ingest.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from coach.config import get_settings  # noqa: E402

SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}


def _extract_text(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    return data.decode("utf-8", errors="replace")


def _chunks(text: str, words_per_chunk: int):
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split(" ")
    for i in range(0, len(words), words_per_chunk):
        piece = " ".join(words[i : i + words_per_chunk]).strip()
        if piece:
            yield piece


def _iter_blob_docs(account: str, container: str):
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    cred = DefaultAzureCredential()
    svc = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=cred)
    client = svc.get_container_client(container)
    base = f"https://{account}.blob.core.windows.net/{container}"
    for blob in client.list_blobs():
        if Path(blob.name).suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        data = client.download_blob(blob.name).readall()
        url = f"{base}/{urllib.parse.quote(blob.name)}"
        yield blob.name, data, url


def _iter_local_docs(folder: str):
    root = Path(folder)
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        yield path.name, path.read_bytes(), path.as_uri()


def _slug(name: str) -> str:
    stem = Path(name).stem
    return re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-").lower() or "doc"


def _title(name: str) -> str:
    return Path(name).stem.replace("_", " ").replace("-", " ").strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--account", help="Azure Storage account name")
    src.add_argument("--local", help="Local folder of documents")
    ap.add_argument("--container", help="Blob container (with --account)")
    ap.add_argument("--index", help="Target index (default: AZURE_SEARCH_INDEX)")
    ap.add_argument("--recreate", action="store_true")
    ap.add_argument("--words-per-chunk", type=int, default=220)
    args = ap.parse_args()

    if args.account and not args.container:
        ap.error("--container is required with --account")

    settings = get_settings()
    index = args.index or settings.azure_search_index
    if not index:
        ap.error("No index configured (set --index or AZURE_SEARCH_INDEX in .env)")

    admin_key = os.environ.get("SEARCH_ADMIN_KEY")
    if not admin_key:
        ap.error("Set SEARCH_ADMIN_KEY (Search admin key) for index writes")

    endpoint = settings.azure_search_endpoint
    api_version = settings.azure_search_api_version
    headers = {"Content-Type": "application/json", "api-key": admin_key}

    id_f = settings.azure_search_id_field
    title_f = settings.azure_search_title_field
    content_f = settings.azure_search_content_field
    url_f = settings.azure_search_url_field

    if args.recreate:
        httpx.delete(
            f"{endpoint}/indexes/{index}?api-version={api_version}", headers=headers
        )

    index_url = f"{endpoint}/indexes/{index}?api-version={api_version}"
    exists = httpx.get(index_url, headers=headers).status_code == 200
    if exists:
        print(f"index '{index}' already exists; uploading into it")
    else:
        schema = {
            "name": index,
            "fields": [
                {"name": id_f, "type": "Edm.String", "key": True, "filterable": True},
                {"name": title_f, "type": "Edm.String", "searchable": True,
                 "analyzer": "en.lucene"},
                {"name": content_f, "type": "Edm.String", "searchable": True,
                 "analyzer": "en.lucene"},
                {"name": url_f, "type": "Edm.String", "searchable": False},
            ],
        }
        r = httpx.put(index_url, headers=headers, json=schema, timeout=30)
        if r.status_code >= 300:
            print("create index:", r.status_code, r.text[:300])
            r.raise_for_status()
        print(f"index '{index}' created")

    if args.local:
        source = _iter_local_docs(args.local)
    else:
        source = _iter_blob_docs(args.account, args.container)

    docs = []
    for name, data, url in source:
        text = _extract_text(name, data)
        slug = _slug(name)
        title = _title(name)
        count = 0
        for idx, chunk in enumerate(_chunks(text, args.words_per_chunk)):
            docs.append({
                "@search.action": "mergeOrUpload",
                id_f: f"{slug}-{idx}",
                title_f: title,
                content_f: chunk,
                url_f: url,
            })
            count += 1
        print(f"  {name}: {count} chunk(s)")

    if not docs:
        print("No documents found to ingest.")
        return

    upload_url = f"{endpoint}/indexes/{index}/docs/index?api-version={api_version}"
    for i in range(0, len(docs), 100):
        batch = docs[i : i + 100]
        resp = httpx.post(upload_url, headers=headers, json={"value": batch}, timeout=60)
        resp.raise_for_status()
    print(f"uploaded {len(docs)} chunk(s) to '{index}'")


if __name__ == "__main__":
    main()
