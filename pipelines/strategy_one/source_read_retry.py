"""Classify transport failures eligible for restarting a read-only source unit."""
from http.client import IncompleteRead, RemoteDisconnected
from urllib.error import HTTPError, URLError


def restartable_read_error(exc):
    if isinstance(exc, HTTPError):
        return False  # Authentication, authority and server SQL errors remain strict.
    if isinstance(exc, URLError):
        exc = exc.reason
    return isinstance(exc, (IncompleteRead, RemoteDisconnected, ConnectionError, TimeoutError))
