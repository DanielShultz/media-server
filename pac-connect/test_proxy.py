import asyncio
import ssl
import time
import unittest
from unittest.mock import AsyncMock, patch

import quickjs
from forwarder import MAX_AGE, Policy, Proxy, dial, tunnel
from policy import evaluate, public_ip, strict_route


class PolicyTests(unittest.TestCase):
    def test_strict_tls_only(self):
        self.assertEqual(
            strict_route("HTTPS proxy.invalid:443; PROXY proxy.invalid:80; DIRECT"), [("proxy.invalid", 443)]
        )
        self.assertIsNone(strict_route("DIRECT"))
        for invalid in (
            "PROXY proxy.invalid:80; DIRECT",
            "",
            "HTTPS a.invalid",
            "HTTPS user:secret@a.invalid:443",
            "HTTPS a.invalid:443/path",
            "FTP a.invalid:443; DIRECT",
            "HTTPS a.invalid:443 extra",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                strict_route(invalid)

    def test_dns_and_helpers(self):
        source = """function FindProxyForURL(u,h) {
          if (dnsDomainIs(h,'.invalid') && shExpMatch(h,'*.invalid') &&
              isInNet(h,'192.0.2.0','255.255.255.0')) return 'HTTPS proxy.invalid:443; DIRECT';
          return 'DIRECT'; }"""
        self.assertEqual(evaluate(source, "site.invalid", {"site.invalid": "192.0.2.1"}), [("proxy.invalid", 443)])
        with self.assertRaises(quickjs.JSException):
            evaluate(source, "site.invalid", {})

    def test_no_host_api(self):
        source = """function FindProxyForURL(){
          if ([typeof require,typeof fetch,typeof XMLHttpRequest,typeof std,typeof os]
              .some(v=>v!=='undefined')) throw Error('host-api'); return 'DIRECT';}"""
        self.assertIsNone(evaluate(source, "site.invalid", {}))

    def test_runaway_is_interrupted(self):
        with self.assertRaises(quickjs.JSException):
            evaluate("function FindProxyForURL(){while(true){}}", "site.invalid", {})

    def test_private_egress_rejected(self):
        for address in ("127.0.0.1", "100.64.0.1", "169.254.169.254", "::1"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                public_ip(address)


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_tls_context_and_sni(self):
        with (
            patch("forwarder.resolve", AsyncMock(return_value=["192.0.2.1"])),
            patch("forwarder.asyncio.open_connection", AsyncMock(return_value=("reader", "writer"))) as connect,
        ):
            await dial("proxy.invalid", 443, encrypted=True)
        self.assertTrue(connect.call_args.kwargs["ssl"].check_hostname)
        self.assertEqual(connect.call_args.kwargs["ssl"].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(connect.call_args.kwargs["server_hostname"], "proxy.invalid")

    async def test_tls_failure_never_falls_back_direct(self):
        with (
            patch("forwarder.dial", AsyncMock(side_effect=ssl.SSLCertVerificationError())) as connect,
            self.assertRaises(ConnectionError),
        ):
            await tunnel("site.invalid", [("proxy.invalid", 443)])
        connect.assert_awaited_once_with("proxy.invalid", 443, encrypted=True)

    async def test_connect_preserves_payload(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"HTTP/1.1 200 OK\r\n\r\nPAYLOAD")
        writer = AsyncMock()
        writer.write = lambda data: None
        with patch("forwarder.dial", AsyncMock(return_value=(reader, writer))):
            actual, _ = await tunnel("site.invalid", [("proxy.invalid", 443)])
        self.assertEqual(await actual.read(7), b"PAYLOAD")

    async def test_direct_control_only_when_initial_direct(self):
        with patch("forwarder.dial", AsyncMock(return_value=("reader", "writer"))) as connect:
            await tunnel("site.invalid", None)
        connect.assert_awaited_once_with("site.invalid", 443)

    async def test_no_policy_and_expired_cache_fail_closed(self):
        policy = Policy("https://pac.invalid/proxy.pac", ["site.invalid"])
        self.assertFalse(policy.ready())
        with self.assertRaises(ValueError):
            policy.route("site.invalid")
        policy.snapshot = (time.monotonic() - MAX_AGE - 1, "hash")
        policy.routes = {"site.invalid": None}
        with self.assertRaises(ValueError):
            policy.route("site.invalid")

    async def test_refresh_failure_does_not_extend_cache_age(self):
        policy = Policy("https://pac.invalid/proxy.pac", ["site.invalid"])
        before = (time.monotonic() - 10, "original-hash")
        policy.snapshot = before
        policy.routes = {"site.invalid": [("proxy.invalid", 443)]}
        with patch("forwarder.download", side_effect=OSError), self.assertRaises(OSError):
            await policy.refresh()
        self.assertEqual(policy.snapshot, before)
        self.assertEqual(policy.route("site.invalid"), [("proxy.invalid", 443)])

    async def test_restricted_http_boundary(self):
        policy = Policy("https://pac.invalid/proxy.pac", ["site.invalid"])
        proxy = Proxy(policy)
        server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0, limit=16384)
        port = server.sockets[0].getsockname()[1]
        cases = [
            (b"CONNECT other.invalid:443 HTTP/1.1", 403),
            (b"CONNECT site.invalid:80 HTTP/1.1", 403),
            (b"GET https://site.invalid/ HTTP/1.1", 405),
            (b"CONNECT site.invalid:443 HTTP/1.1", 502),
            (b"GET /health HTTP/1.1", 503),
        ]
        try:
            with patch("forwarder.dial", AsyncMock()) as connect:
                for request, status in cases:
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.write(request + b"\r\n\r\n")
                    await writer.drain()
                    response = await asyncio.wait_for(reader.read(), 3)
                    self.assertEqual(int(response.split()[1]), status)
                    writer.close()
                    await writer.wait_closed()
                connect.assert_not_awaited()
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
