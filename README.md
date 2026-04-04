# aio-network-forward

💡 Overview

aio-network-forward is a lightweight and user-friendly library for creating asynchronous TCP and UDP port forwards in Python. Built on top of `asyncio` and `aio-sockets`, it exposes a very small API for starting and stopping local forwarding endpoints without dealing with the low-level socket lifecycle yourself.

This library is useful when you want to proxy a local port to another TCP or UDP service, build small tunneling utilities, or embed simple forwarding behavior directly into your own async applications.

✨ Features

Simple Port Forwarding API: Start or stop TCP and UDP forwarding with just a few coroutine calls.

Automatic Port Fallback: If the requested local port is already in use, the library can fall back to a random available port.

Bidirectional Relay: TCP traffic is proxied in both directions, and UDP replies are relayed back to the original sender automatically.

IPv4 and IPv6 Support: Address family is selected automatically from the target address.

Embeddable Design: The library is easy to integrate into existing `asyncio` applications and lets you customize logging and listening addresses.

📦 Installation

Install from PyPI:

```shell
pip install aio-network-forward aio-sockets
```

Minimum supported version: Python 3.8

## Usage

### Basic Forward Example

The example below shows both TCP and UDP usage. Replace the startup line depending on which protocol you want to forward.

```python
import asyncio
import aio_sockets as aio
import aio_network_forward as aionf


async def main():
    forward_to = ("127.0.0.1", 8686)

    # TCP
    local_port = await aionf.start_forward_tcp(forward_to, 8080)

    # UDP
    # local_port = await aionf.start_forward_udp(forward_to, 8080)

    print(f"forwarding started on 0.0.0.0:{local_port} -> {forward_to[0]}:{forward_to[1]}")
    await aio.wait_all_tasks_done()


asyncio.run(main())
```

### Bind to a Random Local Port

If the preferred local port is unavailable, the library will automatically retry with a random available port. You can also pass `0` directly if you always want the OS to choose one.

```python
import asyncio
import aio_sockets as aio
import aio_network_forward as aionf


async def main():
    forward_to = ("127.0.0.1", 8686)
    local_port = await aionf.start_forward_tcp(forward_to, 0)
    print(f"allocated local port: {local_port}")
    await aio.wait_all_tasks_done()


asyncio.run(main())
```

### Limit Listening Address to Localhost

By default, the module listens on `0.0.0.0` for IPv4 and `::` for IPv6. You can change that behavior before starting the forward.

```python
import asyncio
import aio_sockets as aio
import aio_network_forward as aionf


async def main():
    aionf.aio_network_forward._listen_ipv4 = "127.0.0.1"
    aionf.aio_network_forward._listen_ipv6 = "::1"

    forward_to = ("127.0.0.1", 8686)
    local_port = await aionf.start_forward_tcp(forward_to, 8585)
    print(f"listening on localhost:{local_port}")
    await aio.wait_all_tasks_done()


asyncio.run(main())
```

### HTTP Forward Demo

This repository includes `network_forward_demo.py`, which starts a local HTTP server on `127.0.0.1:8686`, then exposes it through a forwarding port.

```shell
python network_forward_demo.py
```

After startup, you can open the forwarded address shown in the logs, for example:

```text
http://127.0.0.1:8585
```

## API

### `start_forward_tcp(forward_to_addr, local_port) -> int`

Start a local TCP forward to `forward_to_addr` and return the actual listening port.

### `stop_forward_tcp(forward_to_addr) -> None`

Stop the TCP forward associated with `forward_to_addr` and close related forwarded sockets.

### `start_forward_udp(forward_to_addr, local_port) -> int`

Start a local UDP forward to `forward_to_addr` and return the actual listening port.

### `stop_forward_udp(forward_to_addr) -> None`

Stop the UDP forward associated with `forward_to_addr`.

## Notes

This project depends on `aio-sockets` for its socket wrapper utilities.

`start_forward_tcp()` and `start_forward_udp()` create background tasks only. In a standalone script, you should keep the main coroutine alive with `await aio.wait_all_tasks_done()`, otherwise the program will exit immediately after startup.

UDP forwarding keeps a temporary per-client forwarding socket and closes it automatically after a period of inactivity.
