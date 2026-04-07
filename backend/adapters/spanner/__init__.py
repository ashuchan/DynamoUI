"""GCP Cloud Spanner adapter (Phase 5).

Connection-test path implemented; query / mutation execution stubbed
behind ``CloudDataAdapter``.
"""
from backend.adapters.spanner.adapter import (
    SpannerAdapter,
    SpannerConnectionTester,
    make_spanner_tester,
)

__all__ = ["SpannerAdapter", "SpannerConnectionTester", "make_spanner_tester"]
