"""Put first on PYTHONPATH to simulate typesafe-sdk not being installed, even where it is."""

raise ImportError("No module named 'typesafe_sdk' (simulated by tests/fixtures/no_sdk)")
