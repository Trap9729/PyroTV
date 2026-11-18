import ipaddress

import pytest

from pyrotv.network import ALLOWED_NETWORKS, is_allowed_client


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "::1",
        "10.42.0.7",
        "::ffff:10.42.0.7",
        "::ffff:127.0.0.1",
        "::1%lo",
    ],
)
def test_allowed(host):
    assert is_allowed_client(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "10.42.1.7",
        "192.168.1.5",
        "8.8.8.8",
        "testclient",
        "",
        None,
        "::ffff:192.168.1.5",
        "::ffff:8.8.8.8",
        " 127.0.0.1",
        "127.0.0.1 ",
        "fe80::1%eth0",
        "2001:db8::1",
    ],
)
def test_rejected(host):
    assert is_allowed_client(host) is False


def test_allowed_networks():
    assert ALLOWED_NETWORKS == (
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("10.42.0.0/24"),
    )
