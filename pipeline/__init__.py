"""Unity hand-drawn style pipeline — zero-infra variant. See .pi/plan.md."""

# Corporate TLS interception (e.g. SealSuite SWG) re-signs HTTPS with an
# enterprise CA that certifi doesn't know. Use the OS trust store instead.
import truststore

truststore.inject_into_ssl()
