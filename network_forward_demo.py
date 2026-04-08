#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import threading

import aio_sockets as aio
import aio_network_forward as aionf

from log_util import logger, config_logger


async def forward_http_server(http_addr: aio.IPAddress, local_port: int) -> None:
    forward_port = await aionf.start_forward_tcp(http_addr, local_port)
    logger.info(f'you can also access the HTTP server via http://{aionf.aio_network_forward._listen_ipv4}:{forward_port}')
    await aio.wait_all_tasks_done()


def start_http_server(host: str, port: int):
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    server = HTTPServer((host, port), SimpleHTTPRequestHandler)
    logger.info(f'start HTTP server, listening on {host}:{port}')
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()


def main():
    aio.aio_sockets.logger = logger
    aionf.aio_network_forward.logger = logger
    aionf.aio_network_forward._listen_ipv4 = '127.0.0.1' # listen on localhost only
    # aio_nf.aio_network_forward._listen_ipv4 = '0.0.0.0'
    config_logger(logger, log_level='info')

    http_addr = ('127.0.0.1', 8686)
    start_http_server(http_addr[0], http_addr[1])
    asyncio.run(forward_http_server(http_addr, 8585))


if __name__ == '__main__':
    main()
