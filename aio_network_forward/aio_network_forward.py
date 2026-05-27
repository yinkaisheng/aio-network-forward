# Compatible with Python 3.8+

import asyncio
import socket
from typing import Any, Dict, List

import aio_sockets as aio


__version__ = '0.1.1'
logger: aio.LoggerLike = aio.StdoutLogger()


class TCPServerInfo:
    def __init__(self, server: asyncio.base_events.Server):
        self.server = server
        self.forward_socks: List[aio.TCPSocket] = []


# _listen_ipv4 = "127.0.0.1"
# _listen_ipv6 = "::1"
_listen_ipv4 = "0.0.0.0"
_listen_ipv6 = "::"
_tcp_laddr2raddrs: Dict[int, List[aio.IPAddress]] = {}
_tcp_laddr2index: Dict[int, int] = {}
_tcp_lport2server: Dict[int, TCPServerInfo] = {}
_udp_laddr2raddrs: Dict[int, List[aio.IPAddress]] = {}
_udp_laddr2index: Dict[int, int] = {}
_udp_lport2sock: Dict[int, aio.UDPSocket] = {}
_tcp_read_size = 8192 # 8kb


async def _handle_tcp_client(sock: aio.TCPSocket) -> None:
    """
    Handle a TCP client connection and proxy data to a remote forward target.

    This is the callback for each accepted TCP connection. It looks up the
    forwarding destination based on the local listening port, establishes a
    connection to that destination, and then relays data bi-directionally
    between the client and the remote target.

    sock: the local client socket.
    """
    local_addr: aio.IPAddress = sock.getsockname()
    from_addr: aio.IPAddress = sock.getpeername()
    logger.info(f'new client {from_addr} -> {local_addr}')
    raddrs = _tcp_laddr2raddrs[local_addr[1]]
    family=socket.AF_INET6 if ':' in local_addr[0] else socket.AF_INET

    forward_sock = None
    forward_to_addr = None
    for _ in range(len(raddrs)):
        idx = _tcp_laddr2index.get(local_addr[1], 0)
        _tcp_laddr2index[local_addr[1]] = (idx + 1) % len(raddrs)
        addr = raddrs[idx % len(raddrs)]
        try:
            forward_sock: aio.TCPSocket = await aio.open_tcp_connection(
                addr[0], addr[1], family=family)
            forward_to_addr = addr
            break
        except Exception as ex:
            logger.warning(f'new forward {from_addr} -> {local_addr}, ? -> {addr} failed, ex={ex!r}, try next')

    if forward_sock is None:
        logger.error(f'new forward {from_addr} -> {local_addr}, all backends failed')
        await sock.close()
        return

    server_info = _tcp_lport2server[local_addr[1]]
    server_info.forward_socks.append(forward_sock)
    forward_from_addr = forward_sock.getsockname()
    logger.info(f'new forward {from_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}')

    async def local_to_remote_io() -> None:
        forward_len = 0
        try:
            while True:
                data = await sock.recv(_tcp_read_size)
                if not data:
                    logger.info(f'local close {from_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}'
                            f', forward_len={forward_len}')
                    break
                readn = len(data)
                forward_len += readn
                # logger.info(f'forward to remote {from_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}: len={readn}, data={data!r}')
                await forward_sock.send(data)
            await forward_sock.close()
        except Exception as ex:
            logger.error(f'ex={ex!r}')

    async def remote_to_local_io() -> None:
        forward_len = 0
        try:
            while True:
                data = await forward_sock.recv(_tcp_read_size)
                if not data:
                    logger.info(f'forward close {from_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}'
                            f', forward_len={forward_len}')
                    break
                readn = len(data)
                forward_len += readn
                # logger.info(f'forward to local {from_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr} len={len(readn)}, {data!r}')
                await sock.send(data)
            await sock.close()
        except Exception as ex:
            logger.error(f'ex={ex!r}')

    asyncio.create_task(local_to_remote_io())
    asyncio.create_task(remote_to_local_io())


async def start_forward_tcp(forward_to_addr: aio.IPAddress, local_port: int) -> int:
    """
    Start a TCP forwarding listener on a local port.

    forward_to_addr: remote target address to forward inbound connections to.
    local_port: local port to bind (0 for random if unavailable).

    If local_port is already forwarded by this module, append forward_to_addr
    to the existing backend list (load balancing). Otherwise create a new server.

    When adding more backends to the same listener, pass the port returned by the
    first call as local_port. Passing 0 again creates a separate listener instead
    of appending. If the requested local_port was unavailable and a random port was
    chosen, later calls must use that returned port, not the originally requested one.

    Returns the actual local port in use.
    """
    if local_port in _tcp_laddr2raddrs:
        _tcp_laddr2raddrs[local_port].append(forward_to_addr)
        logger.info(f'add backend {forward_to_addr} to port {local_port}')
        return local_port

    family=socket.AF_INET6 if ':' in forward_to_addr[0] else socket.AF_INET
    local_ip = _listen_ipv6 if family == socket.AF_INET6 else _listen_ipv4
    try:
        server: asyncio.base_events.Server = await aio.start_tcp_server(
            _handle_tcp_client, local_ip, local_port, family=family)
    except Exception as ex:
        logger.warning(f'{local_port} may be used, use random port, ex={ex!r}')
        server: asyncio.base_events.Server = await aio.start_tcp_server(
            _handle_tcp_client, local_ip, 0, family=family)
    sock: asyncio.transports.TransportSocket = server.sockets[0]
    local_addr: aio.IPAddress = sock.getsockname()
    _tcp_laddr2raddrs[local_addr[1]] = [forward_to_addr]
    _tcp_lport2server[local_addr[1]] = TCPServerInfo(server)
    logger.info(f'serving on {local_addr} for {forward_to_addr}')

    async def tcp_serve_forever():
        try:
            await server.serve_forever()
        except asyncio.exceptions.CancelledError as ex:
            logger.error(f'{ex!r}')
        except Exception as ex:
            logger.error(f'{ex!r}')
        finally:
            _tcp_laddr2raddrs.pop(local_addr[1], None)
            _tcp_laddr2index.pop(local_addr[1], None)
            _tcp_lport2server.pop(local_addr[1], None)

    asyncio.create_task(tcp_serve_forever())
    return local_addr[1]


async def stop_forward_tcp(forward_to_addr: aio.IPAddress) -> None:
    """
    Stop forwarding for a specific remote address.

    If the local port has other backends, keep the server running.
    If this is the last backend, close the server entirely.
    """
    listen_port = None
    for port, raddrs in _tcp_laddr2raddrs.items():
        if forward_to_addr in raddrs:
            listen_port = port
            break
    if listen_port is None:
        return

    raddrs = _tcp_laddr2raddrs[listen_port]
    raddrs.remove(forward_to_addr)

    if len(raddrs) > 0:
        logger.info(f'removed backend {forward_to_addr} from port {listen_port}, remaining: {raddrs}')
    else:
        _tcp_laddr2raddrs.pop(listen_port, None)
        _tcp_laddr2index.pop(listen_port, None)
        server_info = _tcp_lport2server.pop(listen_port, None)
        if server_info is not None:
            logger.info(f'close server {listen_port} for {forward_to_addr}')
            for sock in server_info.forward_socks:
                await sock.close()
            server_info.forward_socks.clear()
            server_info.server.close()
            await server_info.server.wait_closed()


async def _udp_forward_to_local(src_addr: aio.IPAddress, local_addr: aio.IPAddress,
                               forward_from_addr: aio.IPAddress, forward_to_addr: aio.IPAddress,
                               local_sock: aio.UDPSocket, forward_sock: aio.UDPSocket,
                               udp_src_addr_to_sock: Dict[aio.IPAddress, aio.UDPSocket]) -> None:
    """
    Relay UDP responses from the forwarding socket back to the original source.

    This function is started per source client address and terminates after
    timeout or when the remote endpoint closes.
    """
    timeout = 30
    while True:
        try:
            data, addr = await forward_sock.recvfrom_timeout(timeout=timeout)
            if data is None:
                logger.info(f'close forward {src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}')
                break
            # logger.info(f'forward to local {src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr} len={len(data)}, {data!r}')
            local_sock.sendto(data, src_addr)
        except asyncio.TimeoutError as ex:
            logger.warning(f'close forward after {timeout}s no data {src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}')
            break
        except Exception as ex:
            # Windows: if remote endpoint is not listening, recvfrom raises OSError
            # Linux: if remote endpoint is not listening, recvfrom raises ConnectionRefusedError
            logger.error(f'{src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}'
                f' forward_sock.recvfrom got an ex={ex!r}')
    forward_sock.close()
    udp_src_addr_to_sock.pop(src_addr, None)


async def _udp_serve_forever(local_port: int, local_addr: aio.IPAddress, local_sock: aio.UDPSocket) -> None:
    """
    Serve incoming UDP packets on a local socket and forward them to target.

    Maintains per-source UDP sockets for proper bidirectional reply forwarding.
    Uses round-robin to select backend address for each new source client.
    """
    udp_src_addr_to_sock: Dict[aio.IPAddress, aio.UDPSocket] = {}
    while True:
        data, src_addr = await local_sock.recvfrom()
        if data is None:
            logger.info(f'close server {local_addr}')
            break

        forward_sock = udp_src_addr_to_sock.get(src_addr, None)
        if forward_sock is None:
            raddrs = _udp_laddr2raddrs.get(local_port)
            if not raddrs:
                logger.warning(f'no backends for port {local_port}')
                continue
            idx = _udp_laddr2index.get(local_port, 0)
            _udp_laddr2index[local_port] = (idx + 1) % len(raddrs)
            forward_to_addr = raddrs[idx % len(raddrs)]
            family=socket.AF_INET6 if ':' in forward_to_addr[0] else socket.AF_INET
            forward_sock = await aio.create_udp_socket(remote_addr=forward_to_addr, family=family)
            forward_from_addr = forward_sock.getsockname()
            udp_src_addr_to_sock[src_addr] = forward_sock
            logger.info(f'new forward {src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}')
            asyncio.create_task(_udp_forward_to_local(src_addr, local_addr, forward_from_addr, forward_to_addr,
                local_sock, forward_sock, udp_src_addr_to_sock))
        # logger.info(f'forward to remote {src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr} len={len(data)}, {data!r}')
        forward_sock.sendto(data, None)

    for src_addr, forward_sock in udp_src_addr_to_sock.items():
        forward_sock.close()
    udp_src_addr_to_sock.clear()


async def start_forward_udp(forward_to_addr: aio.IPAddress, local_port: int) -> int:
    """
    Start a UDP forwarding listener on a local port.

    forward_to_addr: remote target address to send UDP packets to.
    local_port: local port to bind (0 for random if unavailable).

    If local_port is already forwarded by this module, append forward_to_addr
    to the existing backend list (load balancing). Otherwise create a new socket.

    When adding more backends to the same listener, pass the port returned by the
    first call as local_port. Passing 0 again creates a separate listener instead
    of appending. If the requested local_port was unavailable and a random port was
    chosen, later calls must use that returned port, not the originally requested one.

    Returns the actual local port in use.
    """
    if local_port in _udp_laddr2raddrs:
        _udp_laddr2raddrs[local_port].append(forward_to_addr)
        logger.info(f'add backend {forward_to_addr} to UDP port {local_port}')
        return local_port

    family=socket.AF_INET6 if ':' in forward_to_addr[0] else socket.AF_INET
    local_ip = _listen_ipv6 if family == socket.AF_INET6 else _listen_ipv4
    try:
        sock = await aio.create_udp_socket(local_addr=(local_ip, local_port))
    except Exception as ex:
        logger.warning(f'{local_port} may be used, use random port, ex={ex!r}')
        sock = await aio.create_udp_socket(local_addr=(local_ip, 0))
    local_addr = sock.getsockname()
    logger.info(f'serving on {local_addr} for {forward_to_addr}')
    _udp_laddr2raddrs[local_addr[1]] = [forward_to_addr]
    _udp_lport2sock[local_addr[1]] = sock

    asyncio.create_task(_udp_serve_forever(local_addr[1], local_addr, sock))
    return local_addr[1]


async def stop_forward_udp(forward_to_addr: aio.IPAddress) -> None:
    """
    Stop forwarding for a specific remote address.

    If the local port has other backends, keep the socket running.
    If this is the last backend, close the socket entirely.
    """
    listen_port = None
    for port, raddrs in _udp_laddr2raddrs.items():
        if forward_to_addr in raddrs:
            listen_port = port
            break
    if listen_port is None:
        return

    raddrs = _udp_laddr2raddrs[listen_port]
    raddrs.remove(forward_to_addr)

    if len(raddrs) > 0:
        logger.info(f'removed backend {forward_to_addr} from UDP port {listen_port}, remaining: {raddrs}')
    else:
        _udp_laddr2raddrs.pop(listen_port, None)
        _udp_laddr2index.pop(listen_port, None)
        sock = _udp_lport2sock.pop(listen_port, None)
        if sock is not None:
            logger.info(f'close UDP socket {listen_port} for {forward_to_addr}')
            sock.close()
