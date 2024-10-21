import socket
from contextlib import contextmanager

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
