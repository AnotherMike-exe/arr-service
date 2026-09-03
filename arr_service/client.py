"""Minimal Sonarr/Radarr v3 API client built on the standard library."""

import json
import urllib.error
import urllib.parse
import urllib.request

PAGE_SIZE = 1000


class ArrError(Exception):
    pass


class ArrClient:
    def __init__(self, name, app, url, api_key, timeout=60):
        self.name = name
        self.app = app
        self.base = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # Sonarr stores shows, Radarr stores movies; the shapes are otherwise alike.
    @property
    def media_path(self):
        return "series" if self.app == "sonarr" else "movie"

    @property
    def media_id_field(self):
        return "seriesIds" if self.app == "sonarr" else "movieIds"

    def request(self, method, path, params=None, body=None):
        url = f"{self.base}/api/v3/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-Api-Key", self.api_key)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:500].decode("utf-8", "replace").strip()
            raise ArrError(
                f"{self.name}: {method} {path} failed with HTTP {exc.code}"
                + (f" — {detail}" if detail else "")
            ) from None
        except urllib.error.URLError as exc:
            raise ArrError(f"{self.name}: cannot reach {self.base} — {exc.reason}") from None

        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ArrError(
                f"{self.name}: {method} {path} returned non-JSON — is {self.base} really "
                f"a {self.app} instance?"
            ) from None

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def put(self, path, body):
        return self.request("PUT", path, body=body)

    def post(self, path, body):
        return self.request("POST", path, body=body)

    def delete(self, path):
        return self.request("DELETE", path)

    def paged(self, path, params=None):
        """Walk a paged endpoint and return every record."""
        records = []
        page = 1
        while True:
            query = dict(params or {})
            query.update({"page": page, "pageSize": PAGE_SIZE})
            payload = self.get(path, params=query)
            batch = payload.get("records", []) if isinstance(payload, dict) else (payload or [])
            records.extend(batch)
            total = payload.get("totalRecords", len(records)) if isinstance(payload, dict) else len(records)
            if len(batch) < PAGE_SIZE or len(records) >= total:
                return records
            page += 1

    def get_repeated(self, path, key, values, chunk=100):
        """GET a path with one query parameter repeated, issued in chunks.

        The file endpoints take `movieId=1&movieId=2&...` rather than a page,
        so this is how bulk file lookups are batched.
        """
        results = []
        for start in range(0, len(values), chunk):
            query = urllib.parse.urlencode([(key, value) for value in values[start : start + chunk]])
            batch = self.get(f"{path}?{query}")
            if batch:
                results.extend(batch)
        return results

    def page_count(self, path, params=None):
        """Cheaply read totalRecords from a paged endpoint."""
        query = dict(params or {})
        query.update({"page": 1, "pageSize": 1})
        payload = self.get(path, params=query)
        return payload.get("totalRecords", 0) if isinstance(payload, dict) else len(payload or [])


def from_config(entry, timeout=60):
    return ArrClient(entry["name"], entry["app"], entry["url"], entry["api_key"], timeout=timeout)
