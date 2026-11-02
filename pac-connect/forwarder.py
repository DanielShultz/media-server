"""Restricted CONNECT relay: PAC-selected routes use verified TLS upstream only."""

import asyncio
import contextlib
import hashlib
import json
import os
import re
import socket
import ssl
import time
import urllib.request

from policy import evaluate, public_ip

TLS = ssl.create_default_context()
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
MAX_AGE = 86400


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("pac-redirect")


async def resolve(host):
    addresses = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM), 5
    )
    return [public_ip(item[4][0]) for item in addresses]


def download(url):
    if not url.startswith("https://"):
        raise ValueError("https-pac-required")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # Never inherit a host proxy environment for the PAC bootstrap.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=TLS), NoRedirect()
    )
    with opener.open(request, timeout=10) as response:
        if response.status != 200 or response.url != url:
            raise ValueError("pac-response")
        chunks = []
        size = 0
        deadline = time.monotonic() + 10
        while True:
            if time.monotonic() > deadline or size > 2 * 1024 * 1024:
                raise ValueError("pac-download-limit")
            chunk = response.read1(65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        raw = b"".join(chunks)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("pac-size")
    return raw


class Policy:
    def __init__(self, url, hosts):
        self.url = url
        self.hosts = frozenset(hosts)
        self.snapshot = None
        self.routes = {}
        self.evaluating = asyncio.Lock()

    def ready(self):
        return self.snapshot is not None and time.monotonic() - self.snapshot[0] <= MAX_AGE

    async def refresh(self):
        raw = await asyncio.to_thread(download, self.url)
        addresses = await asyncio.gather(*(resolve(host) for host in sorted(self.hosts)))
        dns_map = {host: values[0] for host, values in zip(sorted(self.hosts), addresses, strict=True)}
        source = raw.decode("utf-8-sig")
        # Qualify the whole allowed set before atomically replacing the last good snapshot.
        routes = {}
        for host in sorted(self.hosts):
            routes[host] = await asyncio.to_thread(evaluate, source, host, dns_map)
        async with self.evaluating:
            self.snapshot = (time.monotonic(), hashlib.sha256(raw).hexdigest())
            self.routes = routes
        print(json.dumps({"event": "pac-ready", "sha256": self.snapshot[1], "hosts": len(routes)}), flush=True)

    async def update_loop(self):
        while True:
            try:
                await self.refresh()
            except Exception as error:
                print(json.dumps({"event": "pac-refresh-failed", "kind": type(error).__name__}), flush=True)
            await asyncio.sleep(300)  # Also bounds failure retries; never refetch faster than 60 s.

    def route(self, host):
        if host not in self.hosts or not self.ready():
            raise ValueError("route-unavailable")
        return self.routes[host]


async def dial(host, port, encrypted=False):
    addresses = await resolve(host)
    return await asyncio.wait_for(
        asyncio.open_connection(
            addresses[0], port, ssl=TLS if encrypted else None, server_hostname=host if encrypted else None, limit=16384
        ),
        5,
    )


async def header(reader):
    result = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    if len(result) > 16384:
        raise ValueError("header-size")
    return result


async def close(writer):
    writer.close()
    with contextlib.suppress(ConnectionError, TimeoutError, ssl.SSLError):
        await asyncio.wait_for(writer.wait_closed(), 2)


async def tunnel(host, routes):
    if routes is None:
        return await dial(host, 443)
    # Failures may try another TLS endpoint, never a direct connection.
    for proxy_host, port in routes:
        writer = None
        try:
            reader, writer = await dial(proxy_host, port, encrypted=True)
            writer.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode("ascii"))
            await writer.drain()
            reply = (await header(reader)).split(b"\r\n", 1)[0].split()
            if len(reply) < 2 or reply[1] != b"200":
                raise ValueError("upstream-connect")
            return reader, writer
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            if writer:
                await close(writer)
    raise ConnectionError("tls-route-failed")


async def pump(reader, writer):
    while data := await asyncio.wait_for(reader.read(65536), 60):
        writer.write(data)
        await writer.drain()


async def relay(client_reader, client_writer, remote_reader, remote_writer):
    tasks = [
        asyncio.create_task(pump(client_reader, remote_writer)),
        asyncio.create_task(pump(remote_reader, client_writer)),
    ]
    try:
        await asyncio.wait(tasks, timeout=90, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def respond(writer, status):
    writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()


class Proxy:
    def __init__(self, policy):
        self.policy = policy
        self.slots = asyncio.Semaphore(8)

    async def handle(self, reader, writer):
        remote_writer = None
        connected = False
        try:
            if self.slots.locked():
                await respond(writer, "503 Unavailable")
                return
            async with self.slots:
                request = (await header(reader)).split(b"\r\n", 1)[0].decode("ascii").split()
                if request == ["GET", "/health", "HTTP/1.1"]:
                    await respond(writer, "200 OK" if self.policy.ready() else "503 Unavailable")
                    return
                if len(request) != 3 or request[0] != "CONNECT" or request[2] != "HTTP/1.1":
                    await respond(writer, "405 Method Not Allowed")
                    return
                match = re.fullmatch(r"([a-zA-Z0-9.-]{1,253}):443", request[1])
                if not match:
                    await respond(writer, "403 Forbidden")
                    return
                host = match[1].lower()
                if host not in self.policy.hosts:
                    await respond(writer, "403 Forbidden")
                    return
                routes = self.policy.route(host)
                remote_reader, remote_writer = await tunnel(host, routes)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                connected = True
                await relay(reader, writer, remote_reader, remote_writer)
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            if not connected:
                with contextlib.suppress(ConnectionError):
                    await respond(writer, "502 Bad Gateway")
        finally:
            if remote_writer:
                await close(remote_writer)
            await close(writer)


async def main():
    hosts = os.environ["PAC_ALLOWED_HOSTS"].split(",")
    if not hosts or any(not re.fullmatch(r"[a-z0-9.-]{1,253}", host) for host in hosts):
        raise ValueError("invalid-host-list")
    policy = Policy(os.environ["PAC_URL"], hosts)
    updater = asyncio.create_task(policy.update_loop())
    proxy = Proxy(policy)
    server = await asyncio.start_server(proxy.handle, "0.0.0.0", 3128, limit=16384)
    try:
        async with server:
            await server.serve_forever()
    finally:
        updater.cancel()
        await asyncio.gather(updater, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
