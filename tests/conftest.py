import socket
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from queue import Empty

import pytest


@pytest.fixture(scope="function")
def bound_socket():
    @contextmanager
    def _bound_socket(port: int):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", port))
            sock.listen()
            yield sock
        except Exception as e:
            pytest.fail(f"Failed to bind to port {port}: {str(e)}")
        finally:
            if sock:
                sock.close()

    return _bound_socket


class MockQueue:
    def __init__(self, values: list | None):
        self.inner = deque([] if values is None else deepcopy(values))

    def put_nowait(self, val):
        self.inner.append(val)

    def put(self, val):
        self.put_nowait(val)

    def get_nowait(self):
        try:
            return self.inner.popleft()
        except IndexError:
            raise Empty

    def get(self):
        return self.get_nowait()

    def report(self):
        return deepcopy(list(self.inner))


@pytest.fixture(scope="function")
def mock_queue():
    def factory(values: list | None = None):
        return MockQueue(values)

    return factory
