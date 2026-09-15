"""Bounded providers for official-contact research and internal simulations.

Search and page content are untrusted inputs. This module narrows queries to a
server-owned Seattle/category tuple and returns typed contact candidates; it has
no email, telephony, browser-automation, or arbitrary-URL capability.
"""

from __future__ import annotations

import heapq
import http.client
import io
import ipaddress
import json
import math
import queue
import re
import socket
import ssl
import struct
import threading
import time
import wave
from copy import deepcopy
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
CATEGORY_QUERY = {
    "damaged_sidewalk": "damaged sidewalk curb ramp",
    "pothole": "pothole road",
    "walkway_obstruction": "blocked sidewalk walkway",
}
GENERIC_EMAIL_TERMS = {
    "311",
    "access",
    "ada",
    "callcenter",
    "city",
    "communications",
    "contact",
    "customer",
    "department",
    "desk",
    "general",
    "help",
    "info",
    "information",
    "inquiries",
    "inquiry",
    "maintenance",
    "office",
    "operations",
    "public",
    "request",
    "requests",
    "road",
    "roads",
    "sdot",
    "service",
    "sidewalk",
    "street",
    "streets",
    "support",
    "team",
    "traffic",
    "transportation",
}
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?1[ .()-]*)?(?:\(?\d{3}\)?[ .-]*)\d{3}[ .-]*\d{4}(?!\d)"
)
EMAIL_SCAN_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"([a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9.-]{1,253})"
    r"(?![a-z0-9.-])"
)
SHARED_PHONE_WITH_LABEL = re.compile(
    rf"(?i)\b(?:311|customer\s+service|customer\s+care\s+center|"
    rf"contact\s+center|call\s+center|call\s+us\s+at|general\s+information|"
    rf"general\s+inquir(?:y|ies)|service\s+request|city\s+information(?:\s+line)?|"
    rf"public\s+information(?:\s+line)?|main\s+(?:office\s+)?line|hotline|"
    rf"24[- ]hour\s+(?:line|hotline)|find\s+it[, ]+fix\s+it)\b"
    rf"(?:\s+(?:phone|number|line))?[\s:–—-]{{0,16}}(?P<phone>{PHONE_PATTERN.pattern})"
)

_LOCAL_TEMP_LOCK = threading.RLock()
_LOCAL_TEMP_CONDITION = threading.Condition(_LOCAL_TEMP_LOCK)
_LOCAL_TEMP: dict[tuple, dict] = {}
_LOCAL_TEMP_DEADLINES: dict[tuple, float] = {}
_LOCAL_TEMP_HEAP: list[tuple[float, tuple]] = []


def _local_temp_key(store, workspace_id, kind, item_id):
    return (id(store), workspace_id, kind, item_id)


def _local_temp_delete(key):
    with _LOCAL_TEMP_CONDITION:
        _LOCAL_TEMP.pop(key, None)
        _LOCAL_TEMP_DEADLINES.pop(key, None)
        _LOCAL_TEMP_CONDITION.notify()


def _local_temp_put(key, item, expires_epoch):
    with _LOCAL_TEMP_CONDITION:
        _LOCAL_TEMP[key] = deepcopy(item)
        _LOCAL_TEMP_DEADLINES[key] = expires_epoch
        heapq.heappush(_LOCAL_TEMP_HEAP, (expires_epoch, key))
        _LOCAL_TEMP_CONDITION.notify()


def _local_temp_get(key):
    with _LOCAL_TEMP_LOCK:
        item = deepcopy(_LOCAL_TEMP.get(key))
    if item and item.get("expires_epoch", 0) <= time.time():
        _local_temp_delete(key)
        return None
    return item


def _local_temp_sweeper():
    while True:
        with _LOCAL_TEMP_CONDITION:
            while not _LOCAL_TEMP_HEAP:
                _LOCAL_TEMP_CONDITION.wait()
            deadline, key = _LOCAL_TEMP_HEAP[0]
            current = _LOCAL_TEMP_DEADLINES.get(key)
            if current != deadline:
                heapq.heappop(_LOCAL_TEMP_HEAP)
                continue
            remaining = deadline - time.time()
            if remaining > 0:
                _LOCAL_TEMP_CONDITION.wait(remaining)
                continue
            heapq.heappop(_LOCAL_TEMP_HEAP)
            _LOCAL_TEMP.pop(key, None)
            _LOCAL_TEMP_DEADLINES.pop(key, None)


threading.Thread(
    target=_local_temp_sweeper,
    name="nf-local-outreach-temp-sweeper",
    daemon=True,
).start()


class OutreachProviderError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = True):
        self.code, self.message, self.retryable = code, message, retryable
        super().__init__(message)


def _field(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        nested = value.get("Name") or value.get("Value") or value.get("Code2") or ""
        return nested if isinstance(nested, str) else ""
    return ""


def reverse_geocode(settings, latitude: float, longitude: float) -> dict:
    if settings.mode == "local":
        supported = 47.48 <= latitude <= 47.74 and -122.45 <= longitude <= -122.22
        return {
            "display_name": "Seattle, Washington, United States"
            if supported
            else "Outside the Seattle demonstration area",
            "locality": "Seattle" if supported else "Outside Seattle",
            "municipality": "Seattle" if supported else "Outside Seattle",
            "region": "Washington" if supported else "Unknown",
            "country_code": "US" if supported else "",
            "supported": supported,
            "provider": "Local deterministic jurisdiction fixture",
        }
    if not settings.outreach_enabled:
        raise OutreachProviderError(
            "CONTACT_RESEARCH_DISABLED",
            "Official contact research is not enabled for this deployment.",
            False,
        )
    try:
        import boto3
        from botocore.config import Config

        response = boto3.client(
            "geo-places",
            region_name=settings.region,
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"total_max_attempts": 2, "mode": "standard"},
            ),
        ).reverse_geocode(
            QueryPosition=[longitude, latitude],
            IntendedUse="Storage",
            AddressNamesMode="Administrative",
            Language="en",
            MaxResults=3,
        )
    except Exception as exc:
        raise OutreachProviderError(
            "JURISDICTION_LOOKUP_FAILED",
            "The government area could not be checked. No contact search occurred.",
        ) from exc
    if not isinstance(response, dict):
        raise OutreachProviderError(
            "JURISDICTION_LOOKUP_FAILED",
            "The government area could not be checked. No contact search occurred.",
        )
    items = response.get("ResultItems") or response.get("Results") or []
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise OutreachProviderError(
            "JURISDICTION_LOOKUP_FAILED",
            "The government area could not be checked. No contact search occurred.",
        )
    if not items:
        raise OutreachProviderError(
            "JURISDICTION_NOT_FOUND",
            "No government-area candidate was found. No contact search occurred.",
            False,
        )
    address = items[0].get("Address") or items[0].get("Place") or {}
    if not isinstance(address, dict):
        raise OutreachProviderError(
            "JURISDICTION_LOOKUP_FAILED",
            "The government area could not be checked. No contact search occurred.",
        )
    for key in ("Municipality", "Locality", "SubRegion", "Region"):
        if key in address and not isinstance(address[key], (str, dict)):
            raise OutreachProviderError(
                "JURISDICTION_LOOKUP_FAILED",
                "The government area could not be checked. No contact search occurred.",
            )
        if isinstance(address.get(key), dict) and any(
            nested in address[key] and not isinstance(address[key][nested], str)
            for nested in ("Name", "Value", "Code2")
        ):
            raise OutreachProviderError(
                "JURISDICTION_LOOKUP_FAILED",
                "The government area could not be checked. No contact search occurred.",
            )
    locality = _field(
        address.get("Municipality")
        or address.get("Locality")
        or address.get("SubRegion")
    )
    region = _field(address.get("Region"))
    country = address.get("Country") or {}
    if not isinstance(country, (str, dict)):
        raise OutreachProviderError(
            "JURISDICTION_LOOKUP_FAILED",
            "The government area could not be checked. No contact search occurred.",
        )
    country_code = (
        country.get("Code2") if isinstance(country, dict) else str(country)
    ) or _field(address.get("CountryCode"))
    if not isinstance(country_code, str):
        raise OutreachProviderError(
            "JURISDICTION_LOOKUP_FAILED",
            "The government area could not be checked. No contact search occurred.",
        )
    supported = (
        locality.strip().casefold() == "seattle"
        and region.strip().casefold() in ("washington", "wa")
        and country_code.strip().upper() == "US"
    )
    display = ", ".join(filter(None, (locality, region, country_code.upper())))
    return {
        "display_name": display or "Government area candidate",
        "locality": locality,
        "municipality": locality,
        "region": region,
        "country_code": country_code.upper(),
        "supported": supported,
        "provider": "Amazon Location Service",
    }


def _hostname(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Invalid official-source hostname") from exc
    if host.startswith("xn--") or ".xn--" in host:
        raise ValueError("Internationalized lookalike hostnames are not accepted")
    return host


def _public_addresses(host: str) -> list[str]:
    try:
        addresses = {
            row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise ValueError("Official-source hostname could not be resolved") from exc
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise ValueError("Official-source hostname resolves outside public IP space")
    return sorted(addresses)


def _public_addresses_before(host: str, deadline: float) -> list[str]:
    result: queue.Queue = queue.Queue(maxsize=1)

    def resolve():
        try:
            result.put((True, _public_addresses(host)))
        except Exception as exc:  # noqa: BLE001 - forward DNS failure to caller.
            result.put((False, exc))

    threading.Thread(target=resolve, daemon=True).start()
    try:
        ok, value = result.get(timeout=max(0.001, deadline - time.monotonic()))
    except queue.Empty as exc:
        raise TimeoutError("Official-source DNS deadline exceeded") from exc
    if not ok:
        raise value
    return value


def validate_official_url(
    url: str, exceptions: tuple[str, ...] = (), *, resolve: bool = True
) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("Invalid official-source URL")
    parsed = urlsplit(url)
    host = _hostname(url)
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or "\\" in url
    ):
        raise ValueError("Official sources must use ordinary HTTPS URLs")
    try:
        ipaddress.ip_address(host)
        raise ValueError("IP-literal sources are not accepted")
    except ValueError as exc:
        if str(exc) == "IP-literal sources are not accepted":
            raise
    allowed = host.endswith(".gov") or host in set(exceptions)
    if not allowed:
        raise ValueError("Source hostname is outside reviewed official domains")
    if resolve:
        _public_addresses(host)
    path = parsed.path or "/"
    return urlunsplit(("https", host, path, parsed.query, ""))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float):
        super().__init__(
            host,
            port=443,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_address = address

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class _PinnedPageResponse:
    encoding = "utf-8"

    def __init__(self, connection, response, deadline, watchdog):
        self.connection = connection
        self.response = response
        self.deadline = deadline
        self.watchdog = watchdog
        self.status_code = response.status
        self.headers = {key.casefold(): value for key, value in response.getheaders()}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.watchdog.cancel()
        self.connection.close()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError("Official source returned an HTTP error")

    def iter_bytes(self):
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Official-source wall-clock deadline exceeded")
            if self.connection.sock:
                self.connection.sock.settimeout(remaining)
            chunk = self.response.read1(16 * 1024)
            if not chunk:
                return
            yield chunk


class _PinnedPageClient:
    """HTTPS page client pinned to the DNS answers that passed IP policy."""

    def __init__(
        self,
        exceptions: tuple[str, ...] = (),
        overall_deadline: float | None = None,
    ):
        self.exceptions = exceptions
        self.overall_deadline = overall_deadline

    def stream(self, method, url, *, headers):
        deadline = min(
            time.monotonic() + 10,
            self.overall_deadline or float("inf"),
        )
        if deadline <= time.monotonic():
            raise TimeoutError("Provider overall deadline exceeded")
        normalized = validate_official_url(url, self.exceptions, resolve=False)
        parsed = urlsplit(normalized)
        address = _public_addresses_before(parsed.hostname or "", deadline)[0]
        connection = _PinnedHTTPSConnection(
            parsed.hostname or "", address, max(0.1, deadline - time.monotonic())
        )
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        watchdog = threading.Timer(
            max(0.001, deadline - time.monotonic()), connection.close
        )
        watchdog.daemon = True
        watchdog.start()
        try:
            connection.request(
                method, path, headers={**headers, "Host": parsed.hostname}
            )
            response = connection.getresponse()
            return _PinnedPageResponse(connection, response, deadline, watchdog)
        except BaseException:
            watchdog.cancel()
            connection.close()
            raise


def _bounded_body(response, limit=256 * 1024, seconds=10) -> bytes:
    deadline = time.monotonic() + seconds
    data = bytearray()
    iterator = iter(response.iter_bytes())
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("Provider wall-clock deadline exceeded")
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("Provider response exceeds the size limit")
    return bytes(data)


class _ContactHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.text: list[str] = []
        self.title: list[str] = []
        self._in_title = False
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        normalized = tag.casefold()
        if normalized in ("script", "style", "noscript", "template"):
            self._hidden_depth += 1
            return
        if self._hidden_depth:
            return
        if normalized == "title":
            self._in_title = True
        for key, value in attrs:
            if normalized == "a" and key.casefold() == "href" and value:
                self.hrefs.append(value)

    def handle_endtag(self, tag):
        normalized = tag.casefold()
        if normalized in ("script", "style", "noscript", "template"):
            self._hidden_depth = max(0, self._hidden_depth - 1)
            return
        if self._hidden_depth:
            return
        if normalized == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._hidden_depth:
            return
        self.text.append(data)
        if self._in_title:
            self.title.append(data)


def _read_html(
    client: httpx.Client, url: str, exceptions: tuple[str, ...]
) -> tuple[str, str]:
    current = validate_official_url(url, exceptions, resolve=False)
    original_host = _hostname(current)
    for _ in range(3):
        with client.stream(
            "GET",
            current,
            headers={
                "User-Agent": "NeighborhoodFixerContactResearch/1.0",
                "Accept-Encoding": "identity",
                "Range": "bytes=-262144",
            },
        ) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                target = validate_official_url(
                    urljoin(current, response.headers.get("location", "")),
                    exceptions,
                    resolve=False,
                )
                if _hostname(target) != original_host:
                    raise ValueError("Cross-host redirects are not accepted")
                current = target
                continue
            response.raise_for_status()
            media = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if media not in ("text/html", "application/xhtml+xml"):
                raise ValueError("Official source is not an HTML page")
            encoding = response.headers.get("content-encoding", "identity").casefold()
            if encoding not in ("", "identity"):
                raise ValueError("Official source returned an encoded range")
            if response.status_code == 206:
                content_range = response.headers.get("content-range", "")
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                if not match:
                    raise ValueError("Official source returned an invalid byte range")
                start, end, total = (int(value) for value in match.groups())
                range_length = end - start + 1
                if (
                    start > end
                    or end != total - 1
                    or range_length > 256 * 1024
                ):
                    raise ValueError("Official source returned an unsafe byte range")
            data = _bounded_body(response)
            if response.status_code == 206 and len(data) != range_length:
                raise ValueError("Official source returned an incomplete byte range")
            return data.decode(response.encoding or "utf-8", "replace"), current
    raise ValueError("Official source redirected too many times")


def normalize_shared_email(value: str) -> str | None:
    value = unquote(value).strip().strip(".,;:()[]{}<>").lower()
    if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9.-]{1,253}", value):
        return None
    local, host = value.rsplit("@", 1)
    if not host.endswith(".gov"):
        return None
    tokens = list(filter(None, re.split(r"[^a-z0-9]+", local)))
    alphabetic = [token for token in tokens if token.isalpha()]
    if not alphabetic or any(token not in GENERIC_EMAIL_TERMS for token in alphabetic):
        return None
    return value


def normalize_us_phone(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def _shared_phones(plain: str, shared_emails: list[str] | None = None) -> list[str]:
    """Return phones that are locally identified as a shared public channel."""

    phones: list[str] = []
    for match in SHARED_PHONE_WITH_LABEL.finditer(plain):
        phone = normalize_us_phone(match.group("phone"))
        if phone and phone not in phones:
            phones.append(phone)
    # Official contact cards sometimes put a collective heading, shared mailbox,
    # and a separately labelled phone on consecutive lines. Accept only a Phone
    # label immediately after an already-validated shared mailbox; arbitrary page
    # context and staff-directory prose cannot bridge this association.
    folded = plain.casefold()
    for email in shared_emails or []:
        cursor = 0
        while (position := folded.find(email.casefold(), cursor)) >= 0:
            tail = plain[position + len(email) : position + len(email) + 96]
            match = re.match(
                rf"(?is)^[\s:;|,/–—-]{{0,48}}(?:phone|telephone|voice)"
                rf"\s*[:–—-]\s*(?P<phone>{PHONE_PATTERN.pattern})",
                tail,
            )
            if match:
                phone = normalize_us_phone(match.group("phone"))
                if phone and phone not in phones:
                    phones.append(phone)
            cursor = position + len(email)
    return phones


def extract_contact(html: str, source_url: str, category: str) -> dict | None:
    parser = _ContactHTMLParser()
    parser.feed(html[: 256 * 1024])
    plain = unescape(" ".join(parser.text))
    email_values = EMAIL_SCAN_PATTERN.findall(plain)
    email_values += [
        href[7:].split("?", 1)[0]
        for href in parser.hrefs
        if href.casefold().startswith("mailto:")
    ]
    emails = [
        email for value in email_values if (email := normalize_shared_email(value))
    ]
    phones = _shared_phones(plain, emails)
    if not emails and not phones:
        return None
    host = _hostname(source_url)
    page_title = re.sub(r"\s+", " ", " ".join(parser.title)).strip()[:160]
    return {
        "agency": "City of Seattle" if host.endswith("seattle.gov") else host,
        "role": {
            "damaged_sidewalk": "Transportation public contact",
            "pothole": "Road maintenance public contact",
            "walkway_obstruction": "Public walkway contact",
        }[category],
        "email": emails[0] if emails else None,
        "phone": phones[0] if phones else None,
        "source_title": page_title or "Official government contact page",
        "source_url": source_url,
        "source_hostname": host,
        "validated_channels": [
            channel
            for channel, present in (("email", emails), ("phone", phones))
            if present
        ],
        "match_reason": (
            "Official government page with a verified shared department email "
            "and public-service phone."
            if emails and phones
            else (
                "Official government page with a verified shared department email."
                if emails
                else (
                    "Official government page with a public-service phone "
                    "identified by department context."
                )
            )
        ),
    }


@lru_cache(maxsize=8)
def _brave_token(secret_arn: str, region: str) -> str:
    if not secret_arn:
        raise OutreachProviderError(
            "BRAVE_CONFIGURATION_REQUIRED",
            "Official contact research is unavailable because Brave Search "
            "is not configured.",
            False,
        )
    try:
        import boto3
        from botocore.config import Config

        raw = boto3.client(
            "secretsmanager",
            region_name=region,
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"total_max_attempts": 2, "mode": "standard"},
            ),
        ).get_secret_value(SecretId=secret_arn)["SecretString"]
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            decoded = raw
        if isinstance(decoded, dict):
            token = next(
                (
                    decoded.get(key)
                    for key in ("api_key", "token", "BRAVE_SEARCH_API_KEY")
                    if decoded.get(key)
                ),
                "",
            )
        else:
            token = decoded
        if not isinstance(token, str) or not token.strip():
            raise ValueError("empty token")
        return token.strip()
    except OutreachProviderError:
        raise
    except Exception as exc:
        raise OutreachProviderError(
            "BRAVE_CONFIGURATION_REQUIRED",
            "Official contact research is unavailable because Brave Search "
            "is not configured.",
            False,
        ) from exc


def _local_contacts(category: str) -> list[dict]:
    shared = {
        "agency": "City of Seattle",
        "role": "Seattle transportation public contact",
        "email": "684-road@seattle.gov",
        "phone": "206-684-7623",
        "source_title": "SDOT contact information (deterministic fixture)",
        "source_url": "https://www.seattle.gov/transportation/about-us/contact-us",
        "source_hostname": "www.seattle.gov",
        "match_reason": (
            "Local deterministic fixture based on an official public contact page."
        ),
        "validated_channels": ["email", "phone"],
        "retrieved_at": None,
    }
    second = {
        "agency": "City of Seattle",
        "role": "Find It, Fix It reporting information",
        "email": None,
        "phone": "206-684-2489",
        "source_title": "Report a problem (deterministic fixture)",
        "source_url": "https://www.seattle.gov/customer-service-bureau/find-it-fix-it-mobile-app",
        "source_hostname": "www.seattle.gov",
        "match_reason": (
            f"Local deterministic fixture for the {CATEGORY_QUERY[category]} category."
        ),
        "validated_channels": ["phone"],
        "retrieved_at": None,
    }
    return [shared, second]


def dispatch_outreach_worker(settings, payload: dict) -> None:
    function_name = (
        settings.contact_research_function
        if payload.get("action") == "research_contacts"
        else settings.outreach_provider_function
    )
    if not function_name:
        raise OutreachProviderError(
            "OUTREACH_PROVIDER_REQUIRED",
            "The private outreach provider worker is not configured.",
            False,
        )
    try:
        import boto3
        from botocore.config import Config

        response = boto3.client(
            "lambda",
            region_name=settings.region,
            config=Config(
                connect_timeout=5,
                read_timeout=150,
                retries={"total_max_attempts": 1},
            ),
        ).invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps({"phase": "outreach_provider", **payload}).encode(),
        )
        if response.get("StatusCode") != 202:
            raise ValueError("private worker failed")
    except OutreachProviderError:
        raise
    except Exception as exc:
        raise OutreachProviderError(
            "OUTREACH_PROVIDER_FAILED",
            "The private outreach provider failed. No outreach occurred.",
        ) from exc


def research_contacts(
    settings,
    category: str,
    *,
    workspace_id: str | None = None,
    research_id: str | None = None,
) -> tuple[list[dict], str]:
    if category not in CATEGORY_QUERY:
        raise OutreachProviderError(
            "UNSUPPORTED_CATEGORY", "This issue category cannot be researched.", False
        )
    if settings.mode == "local":
        return _local_contacts(category), "Local deterministic contact fixture"
    if not settings.outreach_enabled:
        raise OutreachProviderError(
            "CONTACT_RESEARCH_DISABLED",
            "Official contact research is not enabled for this deployment.",
            False,
        )
    raise OutreachProviderError(
        "PRIVATE_WORKER_REQUIRED",
        "AWS contact research must run in the private outreach worker.",
        False,
    )


def research_contacts_aws(settings, category: str) -> tuple[list[dict], str]:
    """Run only inside the private provider worker that holds the Brave secret."""

    token = _brave_token(settings.brave_search_secret_arn, settings.region)
    query = f"Seattle WA {CATEGORY_QUERY[category]} government contact site:seattle.gov"
    try:
        overall_deadline = time.monotonic() + 80
        brave_params = {
            "q": query,
            "count": 5,
            "country": "us",
            "search_lang": "en",
            "safesearch": "strict",
            "spellcheck": "false",
        }
        brave_client = _PinnedPageClient(("api.search.brave.com",), overall_deadline)
        page_client = _PinnedPageClient(
            settings.official_domain_exceptions, overall_deadline
        )
        with brave_client.stream(
            "GET",
            f"{BRAVE_ENDPOINT}?{urlencode(brave_params)}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": token,
            },
        ) as response:
            response.raise_for_status()
            media = response.headers.get("content-type", "").split(";", 1)[0]
            if media.casefold() != "application/json":
                raise ValueError("Brave response is not JSON")
            payload = json.loads(_bounded_body(response))
        if not isinstance(payload, dict):
            raise ValueError("Brave response has an invalid top-level shape")
        web = payload.get("web") or {}
        if not isinstance(web, dict):
            raise ValueError("Brave response has an invalid web-result shape")
        candidates = web.get("results") or []
        if not isinstance(candidates, list):
            raise ValueError("Brave response results are not a list")
        contacts = []
        seen = set()
        for candidate in candidates[:5]:
            if time.monotonic() >= overall_deadline:
                break
            if not isinstance(candidate, dict):
                continue
            try:
                source_url = validate_official_url(
                    candidate.get("url", ""),
                    settings.official_domain_exceptions,
                    resolve=False,
                )
                html, final_url = _read_html(
                    page_client,
                    source_url,
                    settings.official_domain_exceptions,
                )
                item = extract_contact(html, final_url, category)
                if not item:
                    continue
                signature = (item.get("email"), item.get("phone"))
                if signature in seen:
                    continue
                seen.add(signature)
                contacts.append(item)
                if len(contacts) == 3:
                    break
            except (OSError, TimeoutError, ValueError, httpx.HTTPError):
                continue
    except (
        httpx.HTTPError,
        OSError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise OutreachProviderError(
            "CONTACT_RESEARCH_FAILED",
            "Official contact research failed. No outreach occurred.",
        ) from exc
    return contacts, "Brave Search and validated official government pages"


def generate_voice_script(settings, envelope: dict, principal: dict) -> dict:
    if settings.mode == "local":
        from . import fixtures

        return fixtures.simulate_voice(envelope, principal)
    raise OutreachProviderError(
        "PRIVATE_WORKER_REQUIRED",
        "AWS voice generation must run in the private outreach worker.",
        False,
    )


def synthesize_audio_stream(settings, caption: str, speaker: str):
    if settings.mode == "local":
        # A short deterministic tone keeps the fixture audible without pretending
        # that local development invoked a speech provider.
        rate, duration = 8000, 0.16
        frequency = 523 if speaker == "reporting_agent" else 659
        frames = bytearray()
        for index in range(int(rate * duration)):
            sample = int(6500 * math.sin(2 * math.pi * frequency * index / rate))
            frames.extend(struct.pack("<h", sample))
        output = io.BytesIO()
        with wave.open(output, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(bytes(frames))
        data = output.getvalue()
        return (
            data[index : index + 16 * 1024] for index in range(0, len(data), 16 * 1024)
        ), "audio/wav"
    try:
        import boto3
        from botocore.config import Config

        response = boto3.client(
            "polly",
            region_name=settings.region,
            config=Config(
                connect_timeout=5,
                read_timeout=20,
                retries={"total_max_attempts": 2, "mode": "standard"},
            ),
        ).synthesize_speech(
            Text=caption,
            TextType="text",
            OutputFormat="mp3",
            VoiceId="Joanna" if speaker == "reporting_agent" else "Matthew",
            Engine="standard",
        )
        stream = response["AudioStream"]

        def chunks():
            total = 0
            emitted = False
            try:
                while True:
                    data = stream.read(16 * 1024)
                    if isinstance(data, bytearray):
                        data = bytes(data)
                    if not isinstance(data, bytes):
                        raise OutreachProviderError(
                            "VOICE_AUDIO_FAILED",
                            "The approved demo turn returned invalid audio.",
                        )
                    if not data:
                        if not emitted:
                            raise OutreachProviderError(
                                "VOICE_AUDIO_FAILED",
                                "The approved demo turn returned no audio.",
                            )
                        return
                    emitted = True
                    total += len(data)
                    if total > 1_000_000:
                        raise OutreachProviderError(
                            "VOICE_AUDIO_FAILED",
                            "The approved demo turn exceeded its audio limit.",
                        )
                    yield data
            finally:
                stream.close()

        return chunks(), "audio/mpeg"
    except Exception as exc:
        raise OutreachProviderError(
            "VOICE_AUDIO_FAILED",
            "The approved demo turn could not be synthesized. No number was dialed.",
        ) from exc


def put_contact_candidates(
    settings,
    store,
    workspace_id: str,
    research_id: str,
    contacts: list[dict],
    expires_epoch: int,
):
    item = {
        "research_id": research_id,
        "contacts": contacts[:3],
        "expires_epoch": int(expires_epoch),
    }
    if settings.mode == "local":
        _local_temp_put(
            _local_temp_key(store, workspace_id, "contact", research_id),
            item,
            expires_epoch,
        )
        return
    _voice_table(settings).put_item(
        TableName=settings.voice_transcripts_table,
        Item={
            "pk": {"S": f"W#{workspace_id}"},
            "sk": {"S": f"CONTACT#{research_id}"},
            "data": {"S": json.dumps(item, separators=(",", ":"))},
            "expires_epoch": {"N": str(int(expires_epoch))},
        },
    )


def get_contact_candidates(
    settings, store, workspace_id: str, research_id: str
) -> list[dict]:
    if settings.mode == "local":
        item = _local_temp_get(
            _local_temp_key(store, workspace_id, "contact", research_id)
        )
    else:
        raw = (
            _voice_table(settings)
            .get_item(
                TableName=settings.voice_transcripts_table,
                Key={
                    "pk": {"S": f"W#{workspace_id}"},
                    "sk": {"S": f"CONTACT#{research_id}"},
                },
                ConsistentRead=True,
            )
            .get("Item")
        )
        item = json.loads(raw["data"]["S"]) if raw else None
    if item and item.get("expires_epoch", 0) <= time.time():
        delete_contact_candidates(settings, store, workspace_id, research_id)
        return []
    contacts = (item or {}).get("contacts", [])
    return contacts if isinstance(contacts, list) else []


def delete_contact_candidates(settings, store, workspace_id: str, research_id: str):
    if settings.mode == "local":
        _local_temp_delete(_local_temp_key(store, workspace_id, "contact", research_id))
        return
    _voice_table(settings).delete_item(
        TableName=settings.voice_transcripts_table,
        Key={
            "pk": {"S": f"W#{workspace_id}"},
            "sk": {"S": f"CONTACT#{research_id}"},
        },
    )


def _voice_table(settings):
    if not settings.voice_transcripts_table:
        raise OutreachProviderError(
            "VOICE_STORAGE_REQUIRED",
            "Temporary voice-session storage is not configured.",
            False,
        )
    import boto3
    from botocore.config import Config

    return boto3.client(
        "dynamodb",
        region_name=settings.region,
        config=Config(
            connect_timeout=5,
            read_timeout=10,
            retries={"total_max_attempts": 2, "mode": "standard"},
        ),
    )


def put_voice_session(settings, store, workspace_id: str, run_id: str, session: dict):
    if settings.mode == "local":
        _local_temp_put(
            _local_temp_key(store, workspace_id, "voice", run_id),
            session,
            session["expires_epoch"],
        )
        return
    _voice_table(settings).put_item(
        TableName=settings.voice_transcripts_table,
        Item={
            "pk": {"S": f"W#{workspace_id}"},
            "sk": {"S": f"RUN#{run_id}"},
            "data": {"S": json.dumps(session, separators=(",", ":"))},
            "expires_epoch": {
                "N": str(int(session.get("ttl_epoch", session["expires_epoch"])))
            },
        },
    )


def get_voice_session(settings, store, workspace_id: str, run_id: str) -> dict | None:
    if settings.mode == "local":
        item = _local_temp_get(_local_temp_key(store, workspace_id, "voice", run_id))
    else:
        raw = (
            _voice_table(settings)
            .get_item(
                TableName=settings.voice_transcripts_table,
                Key={"pk": {"S": f"W#{workspace_id}"}, "sk": {"S": f"RUN#{run_id}"}},
                ConsistentRead=True,
            )
            .get("Item")
        )
        item = json.loads(raw["data"]["S"]) if raw else None
    if item and item.get("expires_epoch", 0) <= time.time():
        delete_voice_session(settings, store, workspace_id, run_id)
        return None
    return item


def delete_voice_session(settings, store, workspace_id: str, run_id: str):
    if settings.mode == "local":
        session_key = _local_temp_key(store, workspace_id, "voice", run_id)
        session = _local_temp_get(session_key) or {}
        for turn in session.get("turns", []):
            _local_temp_delete(
                _local_temp_key(store, workspace_id, "audio", f"{run_id}:{turn['id']}")
            )
        _local_temp_delete(session_key)
        return
    client = _voice_table(settings)
    partition = {"S": f"W#{workspace_id}"}
    raw = client.get_item(
        TableName=settings.voice_transcripts_table,
        Key={"pk": partition, "sk": {"S": f"RUN#{run_id}"}},
        ConsistentRead=True,
    ).get("Item")
    session = json.loads(raw["data"]["S"]) if raw else {}
    turn_ids = [
        turn.get("id")
        for turn in session.get("turns", [])
        if isinstance(turn, dict) and isinstance(turn.get("id"), str)
    ][:6]
    client.transact_write_items(
        TransactItems=[
            {
                "Delete": {
                    "TableName": settings.voice_transcripts_table,
                    "Key": {"pk": partition, "sk": {"S": sort_key}},
                }
            }
            for sort_key in (
                f"RUN#{run_id}",
                *(f"AUDIO#{run_id}#{turn_id}" for turn_id in turn_ids),
            )
        ]
    )


def claim_voice_audio(
    settings,
    store,
    workspace_id: str,
    run_id: str,
    turn_id: str,
    expires_epoch: int,
    max_requests: int = 2,
):
    """Bound Polly cost without storing synthesized audio."""

    claim_id = f"{run_id}:{turn_id}"
    if settings.mode == "local":
        claim_key = _local_temp_key(store, workspace_id, "audio", claim_id)
        with _LOCAL_TEMP_LOCK:
            claim = _LOCAL_TEMP.get(claim_key) or {
                "run_id": run_id,
                "turn_id": turn_id,
                "request_count": 0,
                "expires_epoch": expires_epoch,
            }
            if claim["request_count"] >= max_requests:
                raise OutreachProviderError(
                    "VOICE_AUDIO_LIMIT_REACHED",
                    "This temporary demo turn reached its playback retry limit.",
                    False,
                )
            claim = {**claim, "request_count": claim["request_count"] + 1}
            _local_temp_put(claim_key, claim, expires_epoch)
        return
    try:
        _voice_table(settings).transact_write_items(
            TransactItems=[
                {
                    "ConditionCheck": {
                        "TableName": settings.voice_transcripts_table,
                        "Key": {
                            "pk": {"S": f"W#{workspace_id}"},
                            "sk": {"S": f"RUN#{run_id}"},
                        },
                        "ConditionExpression": (
                            "attribute_exists(pk) AND expires_epoch > :now"
                        ),
                        "ExpressionAttributeValues": {
                            ":now": {"N": str(int(time.time()))}
                        },
                    }
                },
                {
                    "Update": {
                        "TableName": settings.voice_transcripts_table,
                        "Key": {
                            "pk": {"S": f"W#{workspace_id}"},
                            "sk": {"S": f"AUDIO#{run_id}#{turn_id}"},
                        },
                        "UpdateExpression": (
                            "SET expires_epoch = :expires ADD request_count :one"
                        ),
                        "ConditionExpression": (
                            "attribute_not_exists(request_count) OR request_count < :limit"
                        ),
                        "ExpressionAttributeValues": {
                            ":expires": {"N": str(expires_epoch)},
                            ":one": {"N": "1"},
                            ":limit": {"N": str(max_requests)},
                        },
                    }
                },
            ]
        )
    except Exception as exc:
        response = getattr(exc, "response", {})
        reasons = response.get("CancellationReasons") or []
        if len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed":
            raise OutreachProviderError(
                "VOICE_AUDIO_LIMIT_REACHED",
                "This temporary demo turn reached its playback retry limit.",
                False,
            ) from exc
        if response.get("Error", {}).get("Code") == "TransactionCanceledException":
            raise OutreachProviderError(
                "VOICE_SESSION_UNAVAILABLE",
                "This temporary voice session is no longer available.",
                False,
            ) from exc
        raise OutreachProviderError(
            "VOICE_AUDIO_FAILED",
            "The temporary demo turn could not be reserved for playback.",
        ) from exc
