"""
Auto-discovery of AI providers on localhost.

Uses three discovery methods (in order):
1. Generic localhost scan — reads /proc/net/tcp to find ALL listening TCP
   ports on 127.0.0.1, then probes each for an OpenAI-compatible /v1/models.
2. Docker containers — queries the Docker daemon socket to find ALL running
   containers with published ports, probes each.
3. Known endpoints — a small list used only for friendly naming; any port
   already discovered in step 1/2 gets the friendly name applied.

No hardcoded port or Docker image lists are required for discovery to work.
"""

import httpx
import asyncio
import logging
import os
from typing import List, Dict, Any, Optional, Set, Tuple

from server.services.provider_manager import infer_model_type
from server.core.config import settings, ProviderConfig

logger = logging.getLogger(__name__)

DOCKER_SOCKET = "/var/run/docker.sock"

# Friendly names for well-known (host, port) combos.
# Used purely for display — discovery itself does NOT depend on this list.
FRIENDLY_NAMES: Dict[Tuple[str, int], str] = {
    ("127.0.0.1", 11434): "Ollama",
    ("127.0.0.1", 1234): "LM Studio",
    ("127.0.0.1", 8080): "LocalAI / llama.cpp",
    ("127.0.0.1", 8000): "Speaches / vLLM",
    ("127.0.0.1", 8969): "Speaches",
    ("127.0.0.1", 5000): "text-generation-webui",
}

# Timeout for each probe (seconds)
PROBE_TIMEOUT = 2.0

# Ports to skip during localhost scanning (known non-AI services + AIR itself)
_own_port = int(os.getenv("SERVER_PORT", "5512"))
SKIP_PORTS: Set[int] = {22, 53, 80, 443, 631, 3306, 5432, 5433, 6379, 27017, _own_port}


# ===================================================================== #
#  DiscoveredProvider
# ===================================================================== #


class DiscoveredProvider:
    """Represents a provider found during auto-discovery."""

    def __init__(
        self, name: str, base_url: str, models: List[Dict[str, Any]], detected_types: List[str]
    ):
        self.name = name
        self.base_url = base_url
        self.models = models
        self.detected_types = detected_types  # e.g. ["llm"], ["stt", "tts"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "detected_types": self.detected_types,
            "model_count": len(self.models),
            "models": [
                {"id": m.get("id", "unknown"), "type": m.get("_inferred_type", "llm")}
                for m in self.models
            ],
        }


# ===================================================================== #
#  DiscoveryService
# ===================================================================== #


class DiscoveryService:
    """
    Discovers OpenAI-compatible AI services on the local machine.

    Discovery order:
      1. /proc/net/tcp  — all localhost TCP listeners
      2. Docker socket  — all containers with published ports
      3. EXTRA_SCAN_PORTS from env
    """

    def __init__(self):
        self._last_results: List[DiscoveredProvider] = []

    # ------------------------------------------------------------------ #
    #  1. Generic localhost TCP scan via /proc/net/tcp
    # ------------------------------------------------------------------ #

    def _read_local_tcp_ports(self) -> List[int]:
        """
        Parse /proc/net/tcp and /proc/net/tcp6 to find all TCP ports
        in LISTEN state bound to localhost or wildcard addresses.

        Returns a sorted list of unique port numbers.
        """
        LISTEN_STATE = "0A"  # TCP_LISTEN in hex

        # Valid IPv4 local bindings
        VALID_IPV4_HEX = {
            "0100007F",  # 127.0.0.1
            "00000000",  # 0.0.0.0
        }

        # Valid IPv6 local bindings
        VALID_IPV6_HEX = {
            "00000000000000000000000001000000",  # ::1
            "00000000000000000000000000000000",  # ::
        }

        ports: Set[int] = set()

        def _parse_proc_file(proc_path: str, valid_ips: Set[str]):
            if not os.path.exists(proc_path):
                logger.debug(f"[Discovery] {proc_path} not found — skipping")
                return

            try:
                with open(proc_path, "r") as f:
                    # Skip header line
                    lines = f.readlines()[1:]

                for line in lines:
                    parts = line.strip().split()
                    if len(parts) < 4:
                        continue

                    # parts[1] = local_address (hex IP:hex port)
                    # parts[3] = state (hex)
                    state = parts[3]
                    if state != LISTEN_STATE:
                        continue

                    local_addr = parts[1]
                    try:
                        ip_hex, port_hex = local_addr.split(":")
                        port = int(port_hex, 16)
                    except ValueError:
                        continue

                    # Only include matching localhost or wildcard listeners
                    if ip_hex in valid_ips:
                        if port not in SKIP_PORTS:
                            ports.add(port)

            except Exception as e:
                logger.warning(f"[Discovery] Failed to read {proc_path}: {e}")

        _parse_proc_file("/proc/net/tcp", VALID_IPV4_HEX)
        _parse_proc_file("/proc/net/tcp6", VALID_IPV6_HEX)

        if ports:
            logger.debug(
                f"[Discovery] Local proc net scan found {len(ports)} listening port(s): {sorted(ports)}"
            )

        return sorted(ports)

    # ------------------------------------------------------------------ #
    #  2. Docker: probe ALL containers with published ports
    # ------------------------------------------------------------------ #

    async def _query_docker_socket(self, path: str) -> Optional[Any]:
        """GET request to the Docker daemon via its Unix socket."""
        if not os.path.exists(DOCKER_SOCKET):
            logger.debug("[Discovery] Docker socket not found — skipping Docker scan")
            return None

        try:
            transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
            async with httpx.AsyncClient(transport=transport, timeout=3.0) as client:
                resp = await client.get(f"http://localhost{path}")
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.debug(f"[Discovery] Docker socket query failed: {e}")
        return None

    async def _scan_docker_containers(self) -> List[Tuple[str, str, int, str]]:
        """
        Query Docker for ALL running containers.  For each container with
        published ports, return (label, host, port, base_path) tuples.

        No image-name matching — we probe every exposed port.
        """
        containers = await self._query_docker_socket("/containers/json")
        if not containers:
            return []

        docker_endpoints: List[Tuple[str, str, int, str]] = []

        logger.debug(f"[Discovery] Docker: found {len(containers)} running container(s)")

        for container in containers:
            image = container.get("Image", "")
            container_names = container.get("Names", [])
            cname = container_names[0].lstrip("/") if container_names else "unknown"

            # Extract host port mappings
            ports = container.get("Ports", [])
            for port_info in ports:
                public_port = port_info.get("PublicPort")
                if not public_port:
                    continue

                host_ip = port_info.get("IP", "0.0.0.0")
                if host_ip in ("0.0.0.0", "::"):
                    host_ip = "127.0.0.1"

                label = f"Docker: {cname}"

                docker_endpoints.append((label, host_ip, public_port, "/v1"))
                logger.debug(
                    f"[Discovery] Docker container: {cname} (image={image}) "
                    f"-> {host_ip}:{public_port}"
                )

        return docker_endpoints

    # ------------------------------------------------------------------ #
    #  3. Probe an endpoint for /v1/models
    # ------------------------------------------------------------------ #

    async def _probe_endpoint(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        name: str,
        host: str,
        port: int,
        base_path: str,
    ) -> Optional[DiscoveredProvider]:
        """
        Probe a single endpoint using the shared client and semaphore.
        """
        base_url = f"http://{host}:{port}{base_path}"
        url = f"{base_url.rstrip('/')}/models"

        try:
            async with semaphore:
                resp = await client.get(url, headers={"Content-Type": "application/json"})
                if resp.status_code != 200:
                    return None

                data = resp.json()
                if isinstance(data, list):
                    models = data
                elif isinstance(data, dict):
                    models = data.get("data", [])
                else:
                    models = []

                if not models:
                    return None

                # Classify each model
                detected_types: Set[str] = set()
                for model in models:
                    mtype = infer_model_type(model, default_type="llm")
                    model["_inferred_type"] = mtype
                    detected_types.add(mtype)

                # Apply friendly name if we recognise the port
                friendly = FRIENDLY_NAMES.get((host, port))
                display_name = friendly if friendly else name

                logger.info(
                    f"[Discovery] ✓ {display_name} at {base_url} "
                    f"— {len(models)} model(s), types: {sorted(detected_types)}"
                )

                return DiscoveredProvider(
                    name=display_name,
                    base_url=base_url,
                    models=models,
                    detected_types=sorted(detected_types),
                )

        except Exception:
            # Connection refused, timeout, parse error — all expected
            return None

    # ------------------------------------------------------------------ #
    #  Full scan orchestration
    # ------------------------------------------------------------------ #

    async def scan(self) -> List[DiscoveredProvider]:
        """
        Run a full scan in three phases:
          1. All localhost TCP listeners  (/proc/net/tcp)
          2. All Docker containers with published ports
          3. Extra user-defined targets from EXTRA_SCAN_PORTS env

        All candidate (host, port) pairs are de-duplicated, then probed
        in parallel for /v1/models.
        """
        # Collect candidate endpoints: (label, host, port, base_path)
        candidates: List[Tuple[str, str, int, str]] = []

        # --- Phase 1: Local /proc/net sockets ---
        try:
            proc_ports = self._read_local_tcp_ports()
            for port in proc_ports:
                label = f"localhost:{port}"
                candidates.append((label, "127.0.0.1", port, "/v1"))
            logger.debug(f"[Discovery] Phase 1: {len(proc_ports)} localhost listener(s) to probe")
        except Exception as e:
            logger.warning(f"[Discovery] Localhost scan failed (non-fatal): {e}")

        # --- Phase 2: Docker containers ---
        try:
            docker_endpoints = await self._scan_docker_containers()
            candidates.extend(docker_endpoints)
            logger.debug(
                f"[Discovery] Phase 2: {len(docker_endpoints)} Docker endpoint(s) to probe"
            )
        except Exception as e:
            logger.warning(f"[Discovery] Docker scan failed (non-fatal): {e}")

        # --- Phase 3: Extra user-defined targets ---
        extras = getattr(settings, "EXTRA_SCAN_PORTS", "")
        if extras:
            for entry in extras.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                try:
                    if "/" in entry:
                        host_port, path = entry.split("/", 1)
                        path = "/" + path
                    else:
                        host_port = entry
                        path = "/v1"
                    host, port_str = host_port.rsplit(":", 1)
                    candidates.append(("Custom", host, int(port_str), path))
                except ValueError:
                    logger.warning(
                        f"[Discovery] Ignoring malformed EXTRA_SCAN_PORTS entry: {entry}"
                    )

        # --- De-duplicate by (host, port, path) ---
        # Prefer Docker label over generic "localhost:PORT"
        seen: Dict[Tuple[str, int, str], Tuple[str, str, int, str]] = {}
        for name, host, port, path in candidates:
            key = (host, port, path)
            if key not in seen:
                seen[key] = (name, host, port, path)
            else:
                # If the existing label is generic and this one is richer, replace
                existing_name = seen[key][0]
                if existing_name.startswith("localhost:") and not name.startswith("localhost:"):
                    seen[key] = (name, host, port, path)

        unique_endpoints = list(seen.values())
        logger.info(f"[Discovery] Total unique endpoints to probe: {len(unique_endpoints)}")

        # --- Probe all in parallel with shared client and semaphore ---
        sem = asyncio.Semaphore(10)  # Max 10 concurrent probes
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=True) as client:
            tasks = [
                self._probe_endpoint(client, sem, name, host, port, path)
                for name, host, port, path in unique_endpoints
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        discovered = []
        for res in results:
            if isinstance(res, DiscoveredProvider):
                discovered.append(res)

        logger.info(
            f"[Discovery] Scan complete: {len(discovered)} provider(s) "
            f"responded out of {len(unique_endpoints)} probed"
        )

        self._last_results = discovered
        return discovered

    # ------------------------------------------------------------------ #
    #  Filtering against already-configured providers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize a base URL for comparison."""
        url = url.rstrip("/")
        url = url.replace("localhost", "127.0.0.1")
        return url.lower()

    def filter_new(
        self, discovered: List[DiscoveredProvider], configured: List[ProviderConfig]
    ) -> List[DiscoveredProvider]:
        """
        Return discovered providers that have at least one type NOT already
        configured.  If a server is already configured as TTS but also
        offers STT, the STT capability is still surfaced.
        """
        configured_pairs = {(self._normalize_url(p.base_url), p.type) for p in configured}

        new_providers = []
        for dp in discovered:
            norm_url = self._normalize_url(dp.base_url)
            uncovered_types = [
                t for t in dp.detected_types if (norm_url, t) not in configured_pairs
            ]
            if uncovered_types:
                new_providers.append(
                    DiscoveredProvider(
                        name=dp.name,
                        base_url=dp.base_url,
                        models=dp.models,
                        detected_types=uncovered_types,
                    )
                )

        return new_providers

    @property
    def last_results(self) -> List[DiscoveredProvider]:
        return self._last_results


# Module-level singleton
discovery_service = DiscoveryService()
