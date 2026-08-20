from __future__ import annotations

import asyncio
import inspect
import logging
import socket
import threading
import time
import unittest
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx2
import uvicorn
from mcp.server.mcpserver import MCPServer

from atlas_core.mcp_http import StreamableHTTPMCPClient, _model_dict, _run_in_thread
from atlas_core.tools import MCPToolBridge, ToolGateway


def _serve_streamable_http_mcp() -> tuple[str, Callable[[], None]]:
    server = MCPServer("atlas-streamable-http-test")

    @server.tool()
    def echo(text: str) -> str:
        """Echo text back."""
        return text

    @server.tool()
    def fail(reason: str) -> str:
        """Fail with the provided reason."""
        raise RuntimeError(reason)

    app = server.streamable_http_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()[:2]
    sock.close()
    config = uvicorn.Config(app, host=host, port=port, log_level="error")
    uvicorn_server = uvicorn.Server(config)
    thread = threading.Thread(
        target=lambda: asyncio.run(uvicorn_server.serve()),
        name="atlas-mcp-http-test-server",
        daemon=True,
    )
    thread.start()
    deadline = time.time() + 5
    try:
        while not uvicorn_server.started:
            if not thread.is_alive() or time.time() > deadline:
                raise RuntimeError("test Streamable HTTP MCP server did not start")
            time.sleep(0.02)
    except Exception:
        uvicorn_server.should_exit = True
        thread.join(timeout=5)
        raise

    def stop() -> None:
        uvicorn_server.should_exit = True
        thread.join(timeout=5)

    return f"http://{host}:{port}/mcp", stop


class StreamableHTTPMCPClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpx2").setLevel(logging.WARNING)
        logging.getLogger("mcp").setLevel(logging.WARNING)
        logging.getLogger("uvicorn").setLevel(logging.WARNING)
        cls.url, cls._stop = _serve_streamable_http_mcp()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._stop()

    def test_url_must_be_absolute_http(self) -> None:
        with self.assertRaises(ValueError):
            StreamableHTTPMCPClient("stdio")
        with self.assertRaises(ValueError):
            StreamableHTTPMCPClient("/mcp")
        with self.assertRaises(ValueError):
            StreamableHTTPMCPClient("")

    def test_bridge_methods_stay_synchronous(self) -> None:
        self.assertFalse(inspect.iscoroutinefunction(StreamableHTTPMCPClient.list_tools))
        self.assertFalse(inspect.iscoroutinefunction(StreamableHTTPMCPClient.call_tool))
        client = StreamableHTTPMCPClient(self.url)
        listed = client.list_tools()
        self.assertFalse(inspect.isawaitable(listed))
        called = client.call_tool("echo", {"text": "sync"})
        self.assertFalse(inspect.isawaitable(called))
        self.assertEqual(called["isError"], False)
        self.assertEqual(called["content"][0]["text"], "sync")

    def test_atlas_bridge_invokes_real_streamable_http_server(self) -> None:
        gateway = ToolGateway()
        client = StreamableHTTPMCPClient(self.url)
        ids = MCPToolBridge(client).register_discovered(
            gateway,
            prefix="mcp.test",
            server_name="atlas-streamable-http-test",
            transport="streamable-http",
        )
        self.assertEqual(ids, ("mcp.test.echo", "mcp.test.fail"))
        spec, _handler = gateway.get("mcp.test.echo")
        self.assertEqual(spec.origin.type, "mcp")
        self.assertEqual(spec.origin.server, "atlas-streamable-http-test")
        self.assertEqual(spec.origin.tool_name, "echo")
        self.assertEqual(spec.origin.transport, "streamable-http")
        self.assertEqual(spec.input_schema.get("required"), ["text"])

        result = gateway.invoke("mcp.test.echo", {"text": "via-atlas"}, authority_scope="read")
        self.assertTrue(result.ok)
        self.assertIsNone(result.error)
        self.assertEqual(result.receipt.get("mcp_tool"), "echo")
        self.assertEqual(result.output["isError"], False)
        self.assertEqual(result.output["content"][0]["text"], "via-atlas")
        self.assertEqual(result.output["structuredContent"]["result"], "via-atlas")

        failed = gateway.invoke("mcp.test.fail", {"reason": "bounded-failure"}, authority_scope="read")
        self.assertFalse(failed.ok)
        self.assertEqual(failed.error, "MCP tool reported an error")
        self.assertTrue(failed.output["isError"])

    def test_adapter_contains_async_when_a_loop_is_already_running(self) -> None:
        client = StreamableHTTPMCPClient(self.url)

        async def from_running_loop() -> dict:
            return client.call_tool("echo", {"text": "nested-loop"})

        result = asyncio.run(from_running_loop())
        self.assertEqual(result["content"][0]["text"], "nested-loop")


def _unused_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _serve_accept_only() -> tuple[str, Callable[[], None]]:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    host, port = sock.getsockname()[:2]
    stop = threading.Event()
    conns: list[socket.socket] = []

    def run() -> None:
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _addr = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            conn.settimeout(None)
            conns.append(conn)

    thread = threading.Thread(target=run, name="atlas-mcp-http-blackhole", daemon=True)
    thread.start()

    def shutdown() -> None:
        stop.set()
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass
        try:
            sock.close()
        except OSError:
            pass
        thread.join(timeout=2)

    return f"http://{host}:{port}/mcp", shutdown


def _serve_raw_http(body: bytes, *, content_type: str = "application/json") -> tuple[str, Callable[[], None]]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._reply()

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self._reply()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="atlas-mcp-http-raw", daemon=True)
    thread.start()
    host, port = server.server_address[:2]

    def shutdown() -> None:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    return f"http://{host}:{port}/mcp", shutdown


class StreamableHTTPMCPClientHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpx2").setLevel(logging.WARNING)
        logging.getLogger("mcp").setLevel(logging.CRITICAL)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("httpcore2").setLevel(logging.WARNING)

    def test_timeout_policy_must_be_finite_and_positive(self) -> None:
        url = "http://127.0.0.1:1/mcp"
        for kwargs in (
            {"timeout_sec": 0},
            {"timeout_sec": -1},
            {"timeout_sec": float("inf")},
            {"read_timeout_sec": 0},
            {"read_timeout_sec": float("nan")},
        ):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                StreamableHTTPMCPClient(url, **kwargs)

    def test_timeout_policy_is_constructor_configurable(self) -> None:
        defaults = StreamableHTTPMCPClient("http://127.0.0.1:1/mcp")
        self.assertEqual(defaults.timeout_sec, 30.0)
        self.assertEqual(defaults.read_timeout_sec, 300.0)
        client = StreamableHTTPMCPClient(
            "http://127.0.0.1:1/mcp",
            timeout_sec=2.5,
            read_timeout_sec=4.0,
        )
        self.assertEqual(client.timeout_sec, 2.5)
        self.assertEqual(client.read_timeout_sec, 4.0)
        self.assertEqual(client.timeout.connect, 2.5)
        self.assertEqual(client.timeout.read, 4.0)

    def test_transport_failure_on_connection_refused(self) -> None:
        url = f"http://127.0.0.1:{_unused_port()}/mcp"
        client = StreamableHTTPMCPClient(url, timeout_sec=1.0, read_timeout_sec=1.0)
        with self.assertRaises((OSError, httpx2.TransportError)):
            client.list_tools()

    def test_timeout_when_server_does_not_respond(self) -> None:
        url, stop = _serve_accept_only()
        self.addCleanup(stop)
        client = StreamableHTTPMCPClient(url, timeout_sec=0.4, read_timeout_sec=0.4)
        started = time.monotonic()
        with self.assertRaises((TimeoutError, httpx2.TimeoutException)):
            client.list_tools()
        self.assertLess(time.monotonic() - started, 3.0)

    def test_timeout_applies_when_a_loop_is_already_running(self) -> None:
        url, stop = _serve_accept_only()
        self.addCleanup(stop)
        client = StreamableHTTPMCPClient(url, timeout_sec=0.4, read_timeout_sec=0.4)

        async def from_running_loop() -> list:
            return client.list_tools()

        started = time.monotonic()
        with self.assertRaises((TimeoutError, httpx2.TimeoutException)):
            asyncio.run(from_running_loop())
        self.assertLess(time.monotonic() - started, 3.0)

    def test_malformed_http_response_is_rejected(self) -> None:
        for body in (b"not-json", b"[]", b'"nope"'):
            with self.subTest(body=body):
                url, stop = _serve_raw_http(body)
                self.addCleanup(stop)
                client = StreamableHTTPMCPClient(url, timeout_sec=1.0, read_timeout_sec=1.0)
                with self.assertRaises(Exception) as ctx:
                    client.list_tools()
                self.assertNotIsInstance(ctx.exception, (TypeError, ValueError, TimeoutError))

    def test_non_object_payload_is_rejected(self) -> None:
        class Payload:
            def model_dump(self, **_kwargs: object) -> object:
                return ["not", "an", "object"]

        with self.assertRaisesRegex(TypeError, "non-object payload"):
            _model_dict(Payload())

        class Scalar:
            def model_dump(self, **_kwargs: object) -> object:
                return "nope"

        with self.assertRaisesRegex(TypeError, "non-object payload"):
            _model_dict(Scalar())

    def test_worker_thread_cannot_wait_indefinitely(self) -> None:
        def hang() -> None:
            time.sleep(30)

        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            _run_in_thread(hang, timeout_sec=0.3)
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
