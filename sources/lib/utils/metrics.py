"""Shared Prometheus metrics used across multiple cogs."""

from prometheus_client import Counter, Gauge, Histogram

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
api_call_latency = Histogram(
    'api_call_latency_seconds',
    'External API call latency in seconds',
    ['service'],
)
scheduler_job_failures = Counter(
    'scheduler_job_failures_total',
    'Number of APScheduler job failures',
    ['job'],
)
