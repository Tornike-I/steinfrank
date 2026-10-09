import asyncio
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .. import config
from .base import Limb, LimbError, TransientLimbError

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "head"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts, self.title, self._skip, self._in_title = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _TextExtractor()
    p.feed(html)
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    return p.title.strip(), "\n".join(line for line in lines if line)


def domain_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in allowed:
        pattern = pattern.lower().lstrip("*.")
        if pattern == "" or host == pattern or host.endswith("." + pattern):
            return True
    return False


async def assert_public(host: str):
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise LimbError(f"refusing non-public address {ip} for {host}")


class HttpFetchLimb(Limb):
    name = "http_fetch"
    description = (
        "HTTP GET a public URL. HTML pages are reduced to plain text. Returns {status, url, title, text, json, error}. "
        "The host must be listed in policy.allowed_domains ('*' allows any public host). By default an HTTP error "
        "or unreachable host fails the run; set allow_errors=true for probes where failure is itself a signal "
        "(then status is the HTTP code, or 0 if unreachable, and error says why)."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "max_chars": {"type": "integer", "description": "Truncate text to this many chars (default 12000)"},
            "allow_errors": {"type": "boolean", "description": "Return failures as data instead of failing"},
        },
        "required": ["url"],
    }
    seconds = 2.0
    outputs = {"status", "url", "title", "text", "json", "error"}

    def validate_args(self, args, spec):
        return [] if spec.policy.allowed_domains else ["http_fetch needs policy.allowed_domains"]

    async def run(self, args, ctx):
        if not args.get("allow_errors"):
            return await self._fetch(args, ctx)
        try:
            return await self._fetch(args, ctx)
        except (LimbError, httpx.HTTPError, OSError) as e:
            if "allowed_domains" in str(e) or "non-public" in str(e):
                raise
            status = int(m.group(1)) if (m := re.search(r"HTTP (\d{3})$", str(e))) else 0
            return {"status": status, "url": str(args["url"]), "title": "", "text": "", "json": None, "error": str(e)}

    async def _fetch(self, args, ctx):
        url = str(args["url"]).strip()
        # Users type "amazon.com"; a missing scheme would otherwise read as an unreachable site.
        if url and "://" not in url:
            url = "https://" + url.lstrip("/")
        max_chars = int(args.get("max_chars") or 12000)
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": config.USER_AGENT}) as client:
            for _ in range(MAX_REDIRECTS + 1):
                u = urlparse(url)
                if u.scheme not in ("http", "https") or not u.hostname:
                    raise LimbError(f"bad url {url!r}")
                if not domain_allowed(u.hostname, ctx.policy.allowed_domains):
                    raise LimbError(f"domain {u.hostname} is not in allowed_domains")
                await assert_public(u.hostname)
                async with client.stream("GET", url) as resp:
                    if resp.is_redirect and "location" in resp.headers:
                        url = urljoin(url, resp.headers["location"])
                        continue
                    body = b""
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            break
                    ctype = resp.headers.get("content-type", "")
                    status = resp.status_code
                    encoding = resp.encoding or "utf-8"
                break
            else:
                raise LimbError("too many redirects")

        if status >= 400:
            raise (TransientLimbError if status == 429 or status >= 500 else LimbError)(f"GET {url} -> HTTP {status}")
        raw = body.decode(encoding, errors="replace")
        out = {"status": status, "url": url, "title": "", "text": "", "json": None, "error": None}
        if "json" in ctype:
            try:
                out["json"] = json.loads(raw)
            except ValueError:
                out["text"] = raw[:max_chars]
        elif "html" in ctype:
            out["title"], text = html_to_text(raw)
            out["text"] = text[:max_chars]
        else:
            out["text"] = raw[:max_chars]
        return out
