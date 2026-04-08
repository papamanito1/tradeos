"""pytest configuration for TradeOS tests."""
import asyncio
import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
