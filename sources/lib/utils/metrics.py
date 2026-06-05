"""Shared Prometheus metrics used across multiple cogs."""

from prometheus_client import Counter, Gauge

relay_fetch_errors = Counter(
    'relay_fetch_errors_total',
    'Number of relay feed fetch failures',
    ['service'],
)
relay_last_poll = Gauge(
    'relay_last_poll_timestamp',
    'Unix timestamp of the last successful relay poll',
    ['service'],
)
relay_posts = Counter(
    'relay_posts_total',
    'Number of posts forwarded by relay',
    ['service', 'type'],
)
command_errors = Counter(
    'command_errors_total',
    'Number of unhandled slash-command errors',
    ['command'],
)
domain_fixes = Counter(
    'domain_fixes_total',
    'Number of URLs rewritten by the domain fixer',
)
