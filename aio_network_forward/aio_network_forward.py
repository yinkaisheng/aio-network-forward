#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# author: yinkaisheng@foxmail.com
# support python 3.8+
import os
import sys
import time
import socket
import asyncio
import inspect
from datetime import datetime
from typing import (Any, AsyncGenerator, Callable, Deque, Dict, Generator, List, Iterable, Iterator, Sequence, Set, Tuple, Union)
import aio_sockets as aio


__version__ = '0.1.0'


class TCPServerInfo:
    def __init__(self, server: asyncio.base_events.Server):
        self.server = server
        self.forward_socks: List[aio.TCPSocket] = []


# _listen_ipv4 = "127.0.0.1"
# _listen_ipv6 = "::1"
_listen_ipv4 = "0.0.0.0"
_listen_ipv6 = "::"
_tcp_laddr2raddr: Dict[int, aio.IPAddress] = {}
_tcp_faddr2server: Dict[aio.IPAddress, TCPServerInfo] = {}
_udp_faddr2sock: Dict[aio.IPAddress, aio.UDPSocket] = {}
_tcp_read_size = 8192 # 8kb
logfunc = print


async def _handle_tcp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """
    Handle a TCP client connection and proxy data to a remote forward target.

    This is the callback for each accepted TCP connection. It looks up the
    forwarding destination based on the local listening port, establishes a
    connection to that destination, and then relays data bi-directionally
    between the client and the remote target.

    reader: the local client stream reader.
    writer: the local client stream writer.
    """
    sock = aio.TCPSocket(reader, writer)
    local_addr: aio.IPAddress = sock.getsockname()
    from_addr: aio.IPAddress = sock.getpeername()
    logfunc(f'new client {from_addr} -> {local_addr}')
    forward_to_addr = _tcp_laddr2raddr[local_addr[1]]
    family=socket.AF_INET6 if ':' in local_addr[0] else socket.AF_INET

    try:
        f_reader, f_writer = await asyncio.open_connection(
            forward_to_addr[0], forward_to_addr[1], family=family)
        forward_sock = aio.TCPSocket(f_reader, f_writer)
        server_info = _tcp_faddr2server[forward_to_addr]
        server_info.forward_socks.append(forward_sock)
        forward_from_addr = forward_sock.getsockname()
        logfunc(f'new forward {from_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}')
    except Exception as ex:
        logfunc(f'new forward {from_addr} -> {local_addr}, ? -> {forward_to_addr} failed, ex={ex!r}')
        await sock.close()
        return

    async def local_to_remote_io() -> None:
        forward_len = 0
        try:
            while True:
                data = await sock.recv(_tcp_read_size)
                if not data:
                    logfunc(f'local close {from_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}'
                            f', forward_len={forward_len}')
                    break
                readn = len(data)
                forward_len += readn
                # logfunc(f'forward to remote {from_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}: len={readn}, data={data!r}')
                await forward_sock.send(data)
            await forward_sock.close()
        except BaseException as ex:
            logfunc(f'ex={ex!r}')

    async def remote_to_local_io() -> None:
        forward_len = 0
        try:
            while True:
                data = await forward_sock.recv(_tcp_read_size)
                if not data:
                    logfunc(f'forward close {from_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}'
                            f', forward_len={forward_len}')
                    break
                readn = len(data)
                forward_len += readn
                # logfunc(f'forward to local {from_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr} len={len(readn)}, {data!r}')
                await sock.send(data)
            await sock.close()
        except BaseException as ex:
            logfunc(f'ex={ex!r}')

    asyncio.create_task(local_to_remote_io())
    asyncio.create_task(remote_to_local_io())


async def start_forward_tcp(forward_to_addr: aio.IPAddress, local_port: int) -> int:
    """
    Start a TCP forwarding listener on a local port.

    forward_to_addr: remote target address to forward inbound connections to.
    local_port: local port to bind (0 for random if unavailable).

    Returns the actual local port in use.
    """
    family=socket.AF_INET6 if ':' in forward_to_addr[0] else socket.AF_INET
    local_ip = _listen_ipv6 if family == socket.AF_INET6 else _listen_ipv4
    try:
        server: asyncio.base_events.Server = await asyncio.start_server(
            _handle_tcp_client, local_ip, local_port, family=family)
    except Exception as ex:
        logfunc(f'{local_port} may be used, use random port, ex={ex!r}')
        server: asyncio.base_events.Server = await asyncio.start_server(
            _handle_tcp_client, local_ip, 0, family=family)
    sock: asyncio.transports.TransportSocket = server.sockets[0]
    local_addr: aio.IPAddress = sock.getsockname()
    _tcp_laddr2raddr[local_addr[1]] = forward_to_addr
    _tcp_faddr2server[forward_to_addr] = TCPServerInfo(server)
    logfunc(f'serving on {local_addr} for {forward_to_addr}')

    async def tcp_serve_forever():
        try:
            await server.serve_forever()
        except asyncio.exceptions.CancelledError as ex:
            logfunc(f'{ex!r}')
        except Exception as ex:
            logfunc(f'{ex!r}')
        finally:
            _tcp_laddr2raddr.pop(local_addr[1], None)

    asyncio.create_task(tcp_serve_forever())
    return local_addr[1]


async def stop_forward_tcp(forward_to_addr: aio.IPAddress) -> None:
    """
    Stop a TCP forward listener and close all associated connections.

    forward_to_addr: the remote target address that was previously forwarded.
    """
    server_info = _tcp_faddr2server.pop(forward_to_addr, None)
    if server_info is not None:
        for listen_port, forward_addr in _tcp_laddr2raddr.items():
            if forward_addr == forward_to_addr:
                break
        logfunc(f'close server {listen_port} for {forward_to_addr}')
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
                logfunc(f'close forward {src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}')
                break
            # logfunc(f'forward to local {src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr} len={len(data)}, {data!r}')
            local_sock.sendto(data, src_addr)
        except (TimeoutError, asyncio.TimeoutError) as ex:
            logfunc(f'close forward after {timeout}s no data {src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}')
            break
        except Exception as ex:
            # Windows: if remote endpoint is not listening, recvfrom raises OSError
            # Linux: if remote endpoint is not listening, recvfrom raises ConnectionRefusedError
            logfunc(f'{src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}'
                f' forward_sock.recvfrom got an ex={ex!r}')
    forward_sock.close()
    udp_src_addr_to_sock.pop(src_addr, None)


async def _udp_serve_forever(forward_to_addr: aio.IPAddress, local_addr: aio.IPAddress, local_sock: aio.UDPSocket) -> None:
    """
    Serve incoming UDP packets on a local socket and forward them to target.

    Maintains per-source UDP sockets for proper bidirectional reply forwarding.
    """
    family=socket.AF_INET6 if ':' in forward_to_addr[0] else socket.AF_INET
    udp_src_addr_to_sock: Dict[aio.IPAddress, aio.UDPSocket] = {}
    while True:
        data, src_addr = await local_sock.recvfrom()
        if data is None:
            logfunc(f'close server {local_addr} for {forward_to_addr}')
            break

        forward_sock = udp_src_addr_to_sock.get(src_addr, None)
        if forward_sock is None:
            forward_sock = await aio.create_udp_socket(remote_addr=forward_to_addr, family=family)
            forward_from_addr = forward_sock.getsockname()
            udp_src_addr_to_sock[src_addr] = forward_sock
            logfunc(f'new forward {src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr}')
            asyncio.create_task(_udp_forward_to_local(src_addr, local_addr, forward_from_addr, forward_to_addr,
                local_sock, forward_sock, udp_src_addr_to_sock))
        # logfunc(f'forward to remote {src_addr} -> {local_addr}, {forward_from_addr} -> {forward_to_addr} len={len(data)}, {data!r}')
        forward_sock.sendto(data, None)

    for src_addr, forward_sock in udp_src_addr_to_sock.items():
        forward_sock.close()
    udp_src_addr_to_sock.clear()


async def start_forward_udp(forward_to_addr: aio.IPAddress, local_port: int) -> int:
    """
    Start a UDP forwarding listener on a local port.

    forward_to_addr: remote target address to send UDP packets to.
    local_port: local port to bind (0 for random if unavailable).

    Returns the actual local port in use.
    """
    family=socket.AF_INET6 if ':' in forward_to_addr[0] else socket.AF_INET
    local_ip = _listen_ipv6 if family == socket.AF_INET6 else _listen_ipv4
    try:
        sock = await aio.create_udp_socket(local_addr=(local_ip, local_port))
    except Exception as ex:
        logfunc(f'{local_port} may be used, use random port, ex={ex!r}')
        sock = await aio.create_udp_socket(local_addr=(local_ip, 0))
    local_addr = sock.getsockname()
    logfunc(f'serving on {local_addr} for {forward_to_addr}')
    _udp_faddr2sock[forward_to_addr] = sock

    asyncio.create_task(_udp_serve_forever(forward_to_addr, local_addr, sock))
    return local_addr[1]


async def stop_forward_udp(forward_to_addr: aio.IPAddress) -> None:
    """
    Stop a UDP forward listener and close its socket.

    forward_to_addr: the remote target address that was previously forwarded.
    """
    sock = _udp_faddr2sock.pop(forward_to_addr, None)
    if sock is not None:
        sock.close()
