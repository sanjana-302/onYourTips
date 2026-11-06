from onyourtips.core.utils import is_network_error


def test_dns_and_route_errors_are_network_errors():
    assert is_network_error("ConnectError: [Errno 8] nodename nor servname provided, or not known")
    assert is_network_error("[Errno -2] Name or service not known")
    assert is_network_error("Temporary failure in name resolution")
    assert is_network_error("[Errno 51] Network is unreachable")


def test_site_problems_are_not_network_errors():
    for msg in (None, "", "HTTP 503", "ReadTimeout", "Connection refused", "HTTP 403 Forbidden"):
        assert not is_network_error(msg)
