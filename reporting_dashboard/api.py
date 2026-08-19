"""Inbenta API clients: auth, signing, reporting, editor, and factory functions."""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from reporting_dashboard.config import ConfigLoader

# Disable proxies to avoid connection issues
os.environ["NO_PROXY"] = "*"
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class InbentaAuth:
    """Authenticates with Inbenta Reporting API and caches the token."""

    def __init__(self, auth_url: str, api_key: str, api_secret: str, refresh_buffer: int = 120):
        self.auth_url = auth_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.refresh_buffer = refresh_buffer
        self._access_token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
        self._reporting_base_url: Optional[str] = None

    def get_token(self) -> str:
        if self._needs_refresh():
            self._refresh_token()
        return self._access_token

    def _needs_refresh(self) -> bool:
        if not self._access_token or not self._token_expiry:
            return True
        return (self._token_expiry - datetime.now()).total_seconds() <= self.refresh_buffer

    def _refresh_token(self):
        logger.info("Refreshing Inbenta auth token")
        response = requests.post(
            self.auth_url,
            headers={"x-inbenta-key": self.api_key, "Content-Type": "application/json"},
            json={"secret": self.api_secret},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        self._access_token = data.get("accessToken")
        self._token_expiry = datetime.now() + timedelta(seconds=data.get("expiration", 1200))
        self._reporting_base_url = data.get("apis", {}).get("reporting")

    def get_auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.get_token()}"}

    def get_reporting_base_url(self) -> str:
        if not self._reporting_base_url:
            self.get_token()
        return self._reporting_base_url

    def invalidate(self):
        self._access_token = None
        self._token_expiry = None
        self._reporting_base_url = None


# ---------------------------------------------------------------------------
# Request signing
# ---------------------------------------------------------------------------

class RequestSigner:
    """Signs API requests using Inbenta's HMAC-SHA256 protocol."""

    def __init__(self, api_secret: str):
        self.api_secret = api_secret

    def sign_request(
        self,
        method: str,
        path: str,
        query_params: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
    ) -> Dict[str, str]:
        timestamp = int(datetime.now().timestamp())
        base = self._build_base_string(method, path, query_params, body, timestamp)
        signature = hmac.new(
            key=self.api_secret.encode("utf-8"),
            msg=base.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
        return {
            "x-inbenta-signature": signature,
            "x-inbenta-signature-version": "v1",
            "x-inbenta-timestamp": str(timestamp),
        }

    def _build_base_string(self, method, path, query_params, body, timestamp) -> str:
        enc = lambda t: quote(t, safe="")
        parts = [method.upper()]
        if path:
            clean = path.split("/prod/")[-1].lstrip("/") if "/prod/" in path else path.lstrip("/")
            parts.append(enc(clean))
        if query_params:
            qs = "&".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in sorted(query_params.items()))
            if qs:
                parts.append(quote(qs, safe="", encoding="utf-8"))
        if body:
            parts.append(enc(body))
        parts += [str(timestamp), "v1"]
        return "&".join(parts)


# ---------------------------------------------------------------------------
# Reporting API client
# ---------------------------------------------------------------------------

class InbentaReportingClient:
    """Fetches data from the Inbenta Reporting API (day-by-day)."""

    def __init__(self, base_url: str, auth: InbentaAuth, signer: RequestSigner, rate_limit: int = 800):
        self.auth = auth
        self.signer = signer
        try:
            self.base_url = auth.get_reporting_base_url().rstrip("/")
        except Exception:
            self.base_url = base_url.rstrip("/")
        self.min_interval = 60.0 / rate_limit
        self.last_request_time = 0

    def _rate_limit_sleep(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    @retry(
        retry=retry_if_exception_type((requests.HTTPError, requests.Timeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _make_request(self, method: str, endpoint: str, params=None, body=None) -> Dict[str, Any]:
        self._rate_limit_sleep()
        url = f"{self.base_url}{endpoint}"
        headers = self.auth.get_auth_headers()
        headers.update({"Content-Type": "application/json", "x-inbenta-key": self.auth.api_key})
        body_str = json.dumps(body) if body else None
        headers.update(self.signer.sign_request(method, endpoint, params, body_str))
        response = requests.request(method=method, url=url, headers=headers, params=params, data=body_str, timeout=60)
        if response.status_code == 429:
            raise requests.HTTPError("Rate limit exceeded", response=response)
        if response.status_code in [500, 502, 503, 504]:
            raise requests.HTTPError(f"Server error {response.status_code}", response=response)
        response.raise_for_status()
        return response.json()

    def _fetch_daily(self, endpoint: str, date_from: datetime, date_to: datetime, sources=None) -> List[Dict]:
        """Fetch endpoint for a date range in one request (fast path).

        The API caps each response at a fixed page size (observed: 1000
        results) and does not support offset-based pagination for this
        client (an explicit `offset` param is rejected with 403). So if the
        range's total_count exceeds what was actually returned, the single
        request silently truncated the result set -- in that case (and on
        any request failure) we fall back to day-by-day fetching, since a
        single day's volume is reliably under the page size.
        Always includes env= from MERZ_INBENTA_ENV (development|preproduction|production).
        """
        from reporting_dashboard.config import INBENTA_ENV

        params = {
            "date_from": date_from.strftime("%Y-%m-%d"),
            "date_to": date_to.strftime("%Y-%m-%d"),
            "env": INBENTA_ENV,
        }
        if sources:
            params["source"] = json.dumps(sources)
        try:
            logger.info(
                "Fetching %s for %s → %s (env=%s, single request)",
                endpoint, params["date_from"], params["date_to"], INBENTA_ENV,
            )
            response = self._make_request("GET", endpoint, params=params)
            results = response.get("results", [])
            total_count = response.get("total_count")
            if total_count is None or len(results) >= total_count:
                return results
            logger.warning(
                "Range fetch for %s truncated (%s of %s results); falling back to day-by-day",
                endpoint, len(results), total_count,
            )
        except Exception as exc:
            logger.warning("Range fetch failed for %s (%s); falling back to day-by-day", endpoint, exc)

        from reporting_dashboard.config import split_into_single_days
        days = split_into_single_days(date_from, date_to)
        all_records = []
        for i, (day_start, day_end) in enumerate(days, 1):
            logger.info(f"  [{i}/{len(days)}] {day_start.date()} env={INBENTA_ENV}")
            day_params = {
                "date_from": day_start.strftime("%Y-%m-%d"),
                "date_to": day_end.strftime("%Y-%m-%d"),
                "env": INBENTA_ENV,
            }
            if sources:
                day_params["source"] = json.dumps(sources)
            day_response = self._make_request("GET", endpoint, params=day_params)
            day_results = day_response.get("results", [])
            day_total = day_response.get("total_count")
            if day_total is not None and len(day_results) < day_total:
                logger.error(
                    "Day fetch for %s on %s STILL truncated (%s of %s results) -- "
                    "single-day volume exceeds page size, data will be incomplete",
                    endpoint, day_start.date(), len(day_results), day_total,
                )
            all_records.extend(day_results)
        return all_records

    def get_user_questions(self, date_from, date_to, sources=None):
        return self._fetch_daily("/v1/events/user_questions", date_from, date_to, sources)

    def get_session_details(self, date_from, date_to, sources=None):
        return self._fetch_daily("/v1/aggregates/session_details", date_from, date_to, sources)

    def get_sessions(self, date_from, date_to, sources=None):
        return self._fetch_daily("/v1/events/sessions", date_from, date_to, sources)

    def get_clicks(self, date_from, date_to, sources=None):
        return self._fetch_daily("/v1/events/clicks", date_from, date_to, sources)

    def get_ratings(self, date_from, date_to, sources=None):
        return self._fetch_daily("/v1/events/ratings", date_from, date_to, sources)

    def get_survey_answer(self, answer_id: str) -> Dict[str, Any]:
        from reporting_dashboard.config import INBENTA_ENV
        return self._make_request(
            "GET",
            f"/v1/events/surveys_answer/{answer_id}",
            params={"env": INBENTA_ENV},
        ).get("results", {})


# ---------------------------------------------------------------------------
# Editor API client
# ---------------------------------------------------------------------------

class EditorAuth:
    def __init__(self, auth_url: str, api_key: str, api_secret: str, user_personal_secret: str):
        self.auth_url = auth_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.user_personal_secret = user_personal_secret
        self._access_token: Optional[str] = None

    def get_token(self) -> str:
        if not self._access_token:
            self._authenticate()
        return self._access_token

    def _authenticate(self):
        url = f"{self.auth_url}?secret={self.api_secret}&user_personal_secret={self.user_personal_secret}"
        response = requests.post(
            url,
            headers={"x-inbenta-key": self.api_key, "Content-Type": "application/json"},
            json={"key": self.api_key, "secret": self.api_secret, "user_personal_secret": self.user_personal_secret},
            timeout=30,
        )
        response.raise_for_status()
        self._access_token = response.json().get("accessToken")
        if not self._access_token:
            raise ValueError("No accessToken in Editor auth response")

    def invalidate(self):
        self._access_token = None


class InbentaEditorClient:
    def __init__(self, base_url: str, auth: EditorAuth, rate_limit: int = 100):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.min_interval = 60.0 / rate_limit
        self.last_request_time = 0

    def _rate_limit_sleep(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    @retry(
        retry=retry_if_exception_type((requests.HTTPError, requests.Timeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _make_request(self, method: str, endpoint: str) -> Dict[str, Any]:
        self._rate_limit_sleep()
        headers = {
            "Authorization": f"Bearer {self.auth.get_token()}",
            "x-inbenta-key": self.auth.api_key,
            "Content-Type": "application/json",
        }
        response = requests.request(method, f"{self.base_url}{endpoint}", headers=headers, timeout=30)
        if response.status_code == 401:
            self.auth.invalidate()
            raise requests.HTTPError("Auth failed", response=response)
        if response.status_code == 429:
            raise requests.HTTPError("Rate limit exceeded", response=response)
        if response.status_code in [500, 502, 503, 504]:
            raise requests.HTTPError(f"Server error {response.status_code}", response=response)
        response.raise_for_status()
        return response.json()

    def get_content(self, content_id: int) -> Optional[Dict[str, Any]]:
        try:
            return self._make_request("GET", f"/contents/{content_id}").get("data", {})
        except requests.HTTPError as e:
            if e.response and e.response.status_code == 404:
                return None
            raise

    def get_contents_batch(self, content_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        results = {}
        for i, cid in enumerate(content_ids, 1):
            if i % 10 == 0:
                logger.info(f"  Editor API progress: {i}/{len(content_ids)}")
            try:
                data = self.get_content(cid)
                if data:
                    results[cid] = data
            except Exception as e:
                logger.error(f"Error fetching content {cid}: {e}")
        return results


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def get_reporting_client() -> InbentaReportingClient:
    config = ConfigLoader()
    api = config.get_api_config()
    auth = InbentaAuth(
        auth_url=api["auth_url"],
        api_key=api["key"],
        api_secret=api["secret"],
        refresh_buffer=api.get("token_refresh_buffer", 120),
    )
    signer = RequestSigner(api["signature_key"])
    return InbentaReportingClient(
        base_url=api["base_url"],
        auth=auth,
        signer=signer,
        rate_limit=api.get("rate_limit", 800),
    )


def create_editor_client() -> Optional[InbentaEditorClient]:
    key = os.getenv("MERZ_INBENTA_EDITOR_API_KEY")
    secret = os.getenv("MERZ_INBENTA_EDITOR_API_SECRET")
    personal = os.getenv("MERZ_INBENTA_EDITOR_PERSONAL_SECRET_KEY")
    if not all([key, secret, personal]):
        logger.warning("Editor API credentials not set - content IDs will show as placeholders")
        return None
    try:
        auth = EditorAuth(
            auth_url="https://api.inbenta.io/v1/auth",
            api_key=key,
            api_secret=secret,
            user_personal_secret=personal,
        )
        return InbentaEditorClient(
            base_url=os.getenv("MERZ_INBENTA_EDITOR_BASE_URL", "https://chatbot-api-us.inbenta.io/editor/v1"),
            auth=auth,
            rate_limit=100,
        )
    except Exception as e:
        logger.error(f"Failed to create Editor client: {e}")
        return None
