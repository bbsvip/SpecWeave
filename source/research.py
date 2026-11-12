"""SearXNG and bounded web research. External page contents are untrusted data."""
import asyncio
import ipaddress
import json
import os
import socket
from urllib.parse import urlparse, urljoin, unquote
import httpx
from bs4 import BeautifulSoup
from fastapi import HTTPException

MAX_FETCH = 700_000

def validate_public_url(url):
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            raise ValueError("Invalid URL")
        if p.port and p.port not in (80, 443, 8080, 8443):
            raise ValueError("Port not permitted")
        if p.hostname.lower() in ("localhost", "localhost.localdomain") or p.hostname.lower().endswith((".local", ".internal")):
            raise ValueError("Private host")
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not infos: raise ValueError("No address")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                raise ValueError("Private or reserved address")
    except Exception as exc:
        raise HTTPException(400, f"URL không an toàn hoặc không thể phân giải: {type(exc).__name__}") from exc
    return url

def extract_html(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "form"]): tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    main = soup.select_one("main, article, [role=main]") or soup.body or soup
    text = "\n".join(x.strip() for x in main.get_text("\n", strip=True).splitlines() if x.strip())
    return {"title": title[:400], "text": text[:MAX_FETCH]}

async def search_searxng(settings, query, limit=8):
    base = (settings.get("url") or os.environ.get("SEARXNG_URL") or "").rstrip("/")
    if not base:
        raise HTTPException(503, "Chưa cấu hình SearXNG")
    params = {"q":query[:350], "format":"json", "language":settings.get("language", "all"),
              "safesearch":int(settings.get("safesearch", 1))}
    if settings.get("engines"): params["engines"] = settings["engines"]
    if settings.get("categories"): params["categories"] = settings["categories"]
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        try:
            res = await client.get(base + "/search", params=params)
            res.raise_for_status()
            data = res.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(502, "SearXNG trả lỗi, kiểm tra format=json được bật") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, "Không kết nối được SearXNG") from exc
    results = []
    for item in data.get("results", [])[:max(1, min(limit, 15))]:
        url = item.get("url", "")
        if url.startswith(("https://", "http://")):
            results.append({"title":item.get("title", "")[:300], "url":url, "snippet":item.get("content", "")[:900]})
    return results

async def fetch_web(url, *, browser=False, proxy=None):
    validate_public_url(url)
    if browser:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            launch_args = {"headless": True}
            if proxy:
                parsed=urlparse(proxy)
                # Chromium requires credentials in dedicated fields, not in the server URL.
                launch_args["proxy"] = {"server":f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
                if parsed.username:launch_args["proxy"]["username"]=unquote(parsed.username)
                if parsed.password:launch_args["proxy"]["password"]=unquote(parsed.password)
            process = await pw.chromium.launch(**launch_args)
            try:
                context = await process.new_context(service_workers="block", accept_downloads=False)
                page = await context.new_page()
                async def guard(route):
                    try:
                        validate_public_url(route.request.url)
                    except HTTPException:
                        await route.abort()
                        return
                    await route.continue_()
                await page.route("**/*", guard)
                await page.goto(url, wait_until="domcontentloaded", timeout=18000)
                await page.wait_for_timeout(800)
                content = await page.content()
                result = extract_html(content)
                result["url"] = page.url
                await context.close()
                return result
            finally:
                await process.close()
    current = url
    async with httpx.AsyncClient(timeout=18, follow_redirects=False, trust_env=False, proxy=proxy) as client:
        for _ in range(5):
            validate_public_url(current)
            # streamed response limits payload bytes before reading full HTML
            async with client.stream("GET", current, headers={"User-Agent":"SpecWeaveResearch/1.0", "Accept":"text/html,text/plain"}) as res:
                if res.status_code in (301,302,303,307,308):
                    current = urljoin(current, res.headers.get("location", ""))
                    continue
                res.raise_for_status()
                chunks = bytearray()
                async for chunk in res.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_FETCH:
                        break
                text = chunks.decode("utf-8", errors="replace")
                if "html" in res.headers.get("content-type", "") or "<html" in text[:500].lower():
                    result = extract_html(text)
                else:
                    result = {"title":current, "text":text[:MAX_FETCH]}
                result["url"] = current
                return result
    raise HTTPException(400, "Quá nhiều chuyển hướng")
