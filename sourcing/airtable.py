"""Thin Airtable REST client for the "Polsky Sourcing Project" base.

The code refers to tables and fields by their Airtable names. If someone renames a
field in Airtable, search the repo for the old name and update it.
"""
from __future__ import annotations

import base64
import logging
import os
import time

import requests

log = logging.getLogger(__name__)
API = "https://api.airtable.com/v0"
CONTENT_API = "https://content.airtable.com/v0"
BATCH = 10  # Airtable's max records per write request


class Airtable:
    def __init__(self, dry_run: bool = False):
        self.base = os.environ["AIRTABLE_BASE_ID"]
        self.dry_run = dry_run
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {os.environ['AIRTABLE_TOKEN']}"
        self._fake_ids = 0

    # -- plumbing -----------------------------------------------------------
    def _call(self, method: str, url: str, **kw):
        for attempt in range(6):
            time.sleep(0.25)  # Airtable allows 5 requests/sec per base
            r = self.http.request(method, url, timeout=60, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(30, 2 ** attempt))
                continue
            if not r.ok:
                raise RuntimeError(f"Airtable {method} {url} failed ({r.status_code}): {r.text[:400]}")
            return r.json()
        raise RuntimeError(f"Airtable {method} {url} kept failing (rate limit or outage)")

    def _url(self, table: str) -> str:
        return f"{API}/{self.base}/{requests.utils.quote(table, safe='')}"

    # -- reads --------------------------------------------------------------
    def all(self, table: str, fields: list[str] | None = None) -> list[dict]:
        params: dict = {"pageSize": 100}
        if fields:
            params["fields[]"] = fields
        out: list[dict] = []
        while True:
            data = self._call("GET", self._url(table), params=params)
            out.extend(data["records"])
            if "offset" not in data:
                return out
            params["offset"] = data["offset"]

    def select_options(self) -> dict[str, dict[str, set[str]]]:
        """{table: {field: {option names}}}, so the code never invents a select option."""
        data = self._call("GET", f"{API}/meta/bases/{self.base}/tables")
        opts: dict[str, dict[str, set[str]]] = {}
        for t in data["tables"]:
            for f in t["fields"]:
                choices = (f.get("options") or {}).get("choices")
                if choices is not None:
                    opts.setdefault(t["name"], {})[f["name"]] = {c["name"] for c in choices}
        return opts

    # -- writes -------------------------------------------------------------
    def create(self, table: str, rows: list[dict]) -> list[dict]:
        created: list[dict] = []
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            if self.dry_run:
                for fields in chunk:
                    self._fake_ids += 1
                    log.info("[dry-run] create %s: %s", table, _brief(fields))
                    created.append({"id": f"dry{self._fake_ids:014d}", "fields": dict(fields)})
                continue
            data = self._call("POST", self._url(table), json={"records": [{"fields": f} for f in chunk]})
            created.extend(data["records"])
        return created

    def update(self, table: str, rows: list[dict]) -> None:
        """rows = [{"id": "rec...", "fields": {...}}]. Only the given fields change."""
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            if self.dry_run:
                for r in chunk:
                    log.info("[dry-run] update %s %s: %s", table, r["id"], _brief(r["fields"]))
                continue
            self._call("PATCH", self._url(table), json={"records": chunk})

    def delete(self, table: str, ids: list[str]) -> None:
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            if self.dry_run:
                log.info("[dry-run] delete %s: %s", table, chunk)
                continue
            self._call("DELETE", self._url(table), params={"records[]": chunk})

    def replace_attachment(self, table: str, record: dict, field: str, data: bytes,
                           filename: str, content_type: str = "image/png") -> None:
        """Upload a file, then keep only that file in the field (old files are dropped)."""
        if self.dry_run:
            log.info("[dry-run] upload %s -> %s.%s (%d bytes)", filename, record["id"], field, len(data))
            return
        old_ids = {a["id"] for a in record["fields"].get(field) or []}
        resp = self._call(
            "POST",
            f"{CONTENT_API}/{self.base}/{record['id']}/{requests.utils.quote(field, safe='')}/uploadAttachment",
            json={"contentType": content_type, "filename": filename, "file": base64.b64encode(data).decode()},
        )
        attachments = next(iter((resp.get("fields") or {}).values()), [])
        new = [a for a in attachments if a.get("id") not in old_ids]
        if new:  # drop the old logo only once the new one is safely stored
            self.update(table, [{"id": record["id"], "fields": {field: [{"id": new[-1]["id"]}]}}])


def _brief(fields: dict) -> str:
    return ", ".join(f"{k}={str(v)[:60]}" for k, v in fields.items())
