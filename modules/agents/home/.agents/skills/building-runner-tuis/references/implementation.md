# Implementation notes

## Reference implementations

Two existing runners contain proven code. They are the user's own projects (written on Windows, so `process.py` below is the Windows implementation; the POSIX path is in the Core behavior notes). Locate each checkout under the user's projects directory by its relative path, for example `find ~ -type d -path '*BackTestingResearch/high_freq_trading/runner'`, and ask the user when it is not on this machine. Copy only the files listed below. Do not open either runner's `tui.py`: their screens break designing-tuis rules, and copied UI code brings those breaks back. Write the TUI from designing-tuis and its framework notes.

**BackTestingResearch runner** at `BackTestingResearch/high_freq_trading/runner/`
- `supervisor.py`: supervision loop, backoff constants, separate crash and build budgets, healthy-uptime reset, `stop_requested`, injectable spawn for tests.
- `process.py`: Windows Job Objects through ctypes without pywin32, psutil fallback, staged stop that re-enumerates children after the graceful phase.
- `__main__.py`: pre-launch port sweep separating own leftovers from foreign holders; `--force`, `--only`, `--no-autostart`.

**LLM-WebUI runner** at `LLM-WebUI/scripts/promptsage/runner/`
- `ownership.py`: suspended launch, then job assignment, then resume, so no child escapes.
- `processes.py`: readiness by verifying declared ports are listening under owned PIDs.
- `ports.py`: port-owner release with create_time check against PID reuse and a protected-PID list.
- `runner.py`: `--check` JSON preflight with exit code.

Neither has dynamic port allocation or dependency ordering. Write those fresh.

## Dynamic port allocation (Python)

A port is free only when no process listens on it on any address, IPv4 or IPv6. A bind probe alone is not proof: on Windows, binding `127.0.0.1:8000` succeeds while another process listens on `0.0.0.0:8000`, and the service then shadows it. Check the OS listener table first, then confirm with a wildcard bind.

```python
import socket

import psutil


def port_has_listener(port: int) -> bool:
    """True if any process listens on `port` on any address, IPv4 or IPv6."""
    return any(
        connection.status == psutil.CONN_LISTEN
        and connection.laddr
        and connection.laddr.port == port
        for connection in psutil.net_connections(kind="tcp")
    )


def wildcard_bind_succeeds(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            return False
        return True


def allocate_port(preferred_port: int | None) -> int:
    """Return a free port, trying `preferred_port` first.

    The probe closes before the service binds, so a race is possible. Callers
    treat a bind failure at service start as a retryable start error and
    allocate again.
    """
    if (
        preferred_port is not None
        and not port_has_listener(preferred_port)
        and wildcard_bind_succeeds(preferred_port)
    ):
        return preferred_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("0.0.0.0", 0))
            candidate_port = probe.getsockname()[1]
        if not port_has_listener(candidate_port):
            return candidate_port
```

Do not set `SO_REUSEADDR` on a probe: on Windows it lets the probe succeed on a port that is in use. Pinned-port preflight uses the same `port_has_listener` check, never a bind probe. A unit test for allocation must hold a real listener on `0.0.0.0` and assert the preferred port is skipped.

## Dependency order with dynamic ports

1. Topologically sort services by `depends_on`. A cycle is a config error reported at startup.
2. Start a provider, allocate its ports, wait for readiness.
3. Render dependents' env from resolved provider ports, for example `BACKEND_URL=http://127.0.0.1:{backend.port}`, then start them.
4. When a provider restarts on a new port, restart its dependents so they pick up the new env.

## Testing

- Pure functions such as `should_restart`, `next_delay`, port policy, and topological order get unit tests with an injected clock.
- The supervisor gets tests with a fake spawn: crash loops reach `failed`, user stops never restart, a build failure parks without using the crash budget.
- The TUI gets a headless snapshot with a fake supervisor snapshot at a fixed size.
