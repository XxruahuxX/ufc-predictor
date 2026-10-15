"""Cliente HTTP "educado" que usan todos los conectores.

Hace tres cosas para no abusar de las webs:
1. Rate limit: espera un mínimo de segundos entre peticiones al mismo dominio.
2. Caché en disco: si ya descargamos una página y no ha caducado, no se vuelve a pedir.
3. Reintentos: si falla por error de red o 5xx, reintenta esperando cada vez más.
"""
import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

USER_AGENT = "ufc-predictor/0.1 (proyecto de estudiante; uso personal)"


class PoliteClient:
    def __init__(
        self,
        cache_dir: str | Path = "cache",
        min_interval: float = 2.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,  # para tests
        sleep=time.sleep,  # para tests: así no esperamos de verdad
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.sleep = sleep
        self._last_request: dict[str, float] = {}  # dominio -> instante de la última petición
        self.http = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=20,
            follow_redirects=True,
            transport=transport,
        )

    def _cache_path(self, url: str) -> Path:
        # El nombre del archivo es el hash de la URL: siempre válido y único
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")

    def _wait_turn(self, domain: str) -> None:
        """Duerme lo necesario para respetar min_interval en este dominio."""
        last = self._last_request.get(domain)
        if last is not None:
            remaining = self.min_interval - (time.monotonic() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request[domain] = time.monotonic()

    def get(self, url: str, max_age: float | None = None) -> str:
        """Devuelve el HTML de la URL.

        max_age: segundos que vale la copia en caché.
                 None = para siempre (páginas que no cambian, como un evento pasado).
                 0    = no usar caché.
        """
        path = self._cache_path(url)
        if path.exists() and max_age != 0:
            age = time.time() - path.stat().st_mtime
            if max_age is None or age < max_age:
                return path.read_text(encoding="utf-8")

        domain = urlparse(url).netloc
        for attempt in range(1, self.max_retries + 1):
            self._wait_turn(domain)
            try:
                response = self.http.get(url)
                if response.status_code < 500:
                    response.raise_for_status()  # 4xx: error nuestro, no se reintenta
                    path.write_text(response.text, encoding="utf-8")
                    return response.text
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
            # Error 5xx o de red: esperamos 2, 4, 8... segundos y reintentamos
            self.sleep(2**attempt)
        raise httpx.HTTPError(f"No se pudo descargar {url} tras {self.max_retries} intentos")
