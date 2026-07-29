"""Module 0 — temp file hosting via local server + cloudflared quick tunnel (plan §4).

Anonymous temp hosts (transfer.sh, 0x0.st, ...) are blocked on some corporate
networks. A cloudflared quick tunnel exposes a local HTTP server at a public
https://<random>.trycloudflare.com URL over an outbound-only connection, which
passes corporate firewalls. ModelArk fetches the URLs from outside; the tunnel
must stay up until the Seedance task has finished reading its inputs.

Note: no local URL verification is done here — corporate DNS may not resolve
trycloudflare.com from this machine, but that does not affect ModelArk's fetch.
"""

import re
import select
import shutil
import socket
import subprocess
import time
from pathlib import Path

TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class TunnelHost:
    """Serve a directory at a public cloudflared quick-tunnel URL."""

    def __init__(self, serve_dir: str, startup_timeout: float = 30.0):
        self.serve_dir = Path(serve_dir).resolve()
        self.startup_timeout = startup_timeout
        self.base_url: str | None = None
        self._server: subprocess.Popen | None = None
        self._tunnel: subprocess.Popen | None = None

    def __enter__(self) -> "TunnelHost":
        self.serve_dir.mkdir(parents=True, exist_ok=True)
        port = _free_port()
        self._server = subprocess.Popen(
            ["python3", "-m", "http.server", str(port),
             "--bind", "127.0.0.1", "--directory", str(self.serve_dir)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._tunnel = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}",
             "--no-autoupdate"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        try:
            self.base_url = self._await_tunnel_url()
        except Exception:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc) -> None:
        for proc in (self._tunnel, self._server):
            if proc and proc.poll() is None:
                proc.terminate()
        for proc in (self._tunnel, self._server):
            if proc:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def stage(self, src_path: str, name: str | None = None) -> str:
        """Copy src_path into the served directory; return its public URL."""
        src = Path(src_path)
        dest = self.serve_dir / (name or src.name)
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        return self.url_for(dest.name)

    def url_for(self, name: str) -> str:
        if not self.base_url:
            raise RuntimeError("tunnel is not up — use TunnelHost as a context manager")
        return f"{self.base_url}/{name}"

    def _await_tunnel_url(self) -> str:
        assert self._tunnel and self._tunnel.stderr
        deadline = time.monotonic() + self.startup_timeout
        log_tail: list[str] = []
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self._tunnel.stderr], [], [], 1.0)
            if ready:
                line = self._tunnel.stderr.readline()
                if not line:
                    break
                log_tail.append(line)
                m = TUNNEL_URL_RE.search(line)
                if m:
                    return m.group(0)
            elif self._tunnel.poll() is not None:
                break
        raise RuntimeError(
            f"cloudflared tunnel did not come up in {self.startup_timeout}s:\n"
            + "".join(log_tail)[-2000:]
        )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
