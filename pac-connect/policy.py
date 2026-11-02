"""Bounded PAC evaluation; no host callbacks or network APIs in JavaScript."""

import ipaddress
import json
import re
from urllib.parse import urlsplit

import quickjs

HELPERS = r"""
function dnsResolve(h) {
  if (/^\d+\.\d+\.\d+\.\d+$/.test(h)) return h;
  if (!Object.prototype.hasOwnProperty.call(dnsMap,h)) throw Error('unknown-dns');
  return dnsMap[h];
}
function isResolvable(h) {return dnsResolve(h)!==null;}
function dnsDomainIs(h,d) {return h.endsWith(d);}
function isPlainHostName(h) {return !h.includes('.');}
function localHostOrDomainIs(h,d) {return h===d || (!h.includes('.') && d.startsWith(h+'.'));}
function myIpAddress() {throw Error('unsupported-helper');}
function weekdayRange() {throw Error('unsupported-helper');}
function dateRange() {throw Error('unsupported-helper');}
function timeRange() {throw Error('unsupported-helper');}
function shExpMatch(s,p) {
  return new RegExp('^'+p.replace(/[.+^$(){}|[\]\\]/g,'\\$&').replace(/\*/g,'.*').replace(/\?/g,'.')+'$').test(s);
}
function ipNum(s) {
  const a=(s||'').split('.').map(Number);
  return a.length===4 && a.every(v=>Number.isInteger(v)&&v>=0&&v<=255)
    ? a.reduce((n,v)=>((n<<8)|v)>>>0,0) : null;
}
function isInNet(h,p,m) {
  const n=ipNum(ipNum(h)===null?dnsResolve(h):h), mask=ipNum(m), pattern=ipNum(p);
  return n!==null && mask!==null && pattern!==null && (n&mask)===(pattern&mask);
}
"""


def evaluate(source, host, dns_map):
    context = quickjs.Context()
    context.set_memory_limit(64 * 1024 * 1024)
    context.set_max_stack_size(1024 * 1024)
    context.set_time_limit(1.5)
    context.eval("const dnsMap=" + json.dumps(dns_map) + ";" + HELPERS)
    context.eval(source)
    answer = context.eval("FindProxyForURL(" + json.dumps("https://" + host + "/") + "," + json.dumps(host) + ")")
    if not isinstance(answer, str) or len(answer) > 4096:
        raise ValueError("invalid-pac-result")
    return strict_route(answer)


def strict_route(answer):
    entries = [part.strip().split() for part in answer.split(";") if part.strip()]
    if entries == [["DIRECT"]]:
        return None
    # Only HTTPS entries survive; NEVER interpret fallback DIRECT as an initial DIRECT.
    endpoints = []
    if len(entries) > 4:
        raise ValueError("too-many-routes")
    for entry in entries:
        if entry[0] not in {"HTTPS", "PROXY", "DIRECT", "SOCKS", "SOCKS4", "SOCKS5"}:
            raise ValueError("unknown-directive")
        if len(entry) != (1 if entry[0] == "DIRECT" else 2):
            raise ValueError("invalid-directive")
        if entry[0] != "HTTPS":
            continue
        parsed = urlsplit("https://" + entry[1])
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("invalid-proxy")
        if not parsed.hostname or parsed.port is None or not 1 <= parsed.port <= 65535:
            raise ValueError("invalid-proxy")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname):
            raise ValueError("invalid-proxy")
        endpoints.append((parsed.hostname, parsed.port))
    if not endpoints:
        raise ValueError("no-tls-route")
    return endpoints


def public_ip(address):
    if not ipaddress.ip_address(address).is_global:
        raise ValueError("non-public-address")
    return address
