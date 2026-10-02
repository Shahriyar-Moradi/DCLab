"""SDK version. Bumped when the /v1 wire contract the client speaks changes."""

# 0.2.0 (P3.1-A): /v1 error envelope -> typed errors, Idempotency-Key on every
# POST (auto-generated), ETag / If-Match, opaque cursors, User-Agent.
__version__ = "0.2.0"
USER_AGENT = f"dclab-client/{__version__}"
