"""Embeddings remotos (Cloudflare Workers AI, @cf/baai/bge-m3, 1024 dims)
y upsert en Qdrant vía REST (sin dependencia qdrant-client).

Los vectores son idénticos en dimensiones al BGE-M3 local del notebook,
por lo que las colecciones existentes siguen siendo compatibles.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Iterable, Optional

import requests

from .config import Config


def compute_qdrant_id(sentencia_id: str, suffix: str = "") -> int:
    """ID determinista (mismo esquema que el notebook)."""
    key = f"{sentencia_id}{suffix}".encode("utf-8")
    h = hashlib.sha256(key).digest()
    return int.from_bytes(h[:8], "big") % (2**63 - 1) + 1


class EmbeddingsRemotos:
    """Cliente de embeddings por API (bge-m3, 1024 dimensiones)."""

    def __init__(self, cfg: Config, timeout: int = 120, reintentos: int = 3):
        cfg.requerir("ia")
        self.cfg = cfg
        self.timeout = timeout
        self.reintentos = reintentos
        self.base = cfg.api_base_ia()
        self._s = requests.Session()
        h = {"Content-Type": "application/json"}
        if cfg.cf_api_email:
            # Global Key (cfk_): X-Auth-Key + X-Auth-Email en vez de Bearer
            h["X-Auth-Key"] = cfg.cf_api_key
            h["X-Auth-Email"] = cfg.cf_api_email
        else:
            h["Authorization"] = f"Bearer {cfg.cf_api_key}"
        self._s.headers.update(h)

    def vectorizar(self, textos: Iterable[str], modelo: Optional[str] = None,
                   batch: int = 16) -> list[list[float]]:
        """Vectoriza textos en lotes; devuelve lista de vectores 1024-d."""
        modelo = modelo or self.cfg.modelo_embedding
        out: list[list[float]] = []
        lista = [t for t in textos if t]
        for i in range(0, len(lista), batch):
            lote = lista[i:i + batch]
            ultimo = None
            for _ in range(self.reintentos):
                try:
                    r = self._s.post(
                        f"{self.base}/embeddings",
                        json={"model": modelo, "input": lote},
                        timeout=self.timeout,
                    )
                    if r.status_code == 200:
                        out.extend(d["embedding"] for d in r.json()["data"])
                        break
                    ultimo = f"HTTP {r.status_code}"
                except Exception as e:  # noqa: BLE001
                    ultimo = f"{type(e).__name__}: {str(e)[:80]}"
                time.sleep(3)
            else:
                raise RuntimeError(f"Embeddings falló: {ultimo}")
        return out


class Qdrant:
    """Cliente REST mínimo de Qdrant Cloud (colecciones de 1024 dims)."""

    def __init__(self, cfg: Config, timeout: int = 60):
        cfg.requerir("qdrant")
        url = cfg.qdrant_url.rstrip("/")
        # Qdrant Cloud: si no se indicó puerto, usar 6333 (REST + TLS)
        if not re.search(r":\d+$", url):
            url = f"{url}:6333"
        self.url = url
        self._s = requests.Session()
        self._s.headers.update({
            "api-key": cfg.qdrant_api_key,
            "Content-Type": "application/json",
        })
        self.timeout = timeout

    def crear_si_no_existe(self, nombre: str, dims: int = 1024) -> None:
        r = self._s.get(f"{self.url}/collections/{nombre}", timeout=self.timeout)
        if r.status_code == 404:
            r2 = self._s.put(
                f"{self.url}/collections/{nombre}",
                json={"vectors": {"size": dims, "distance": "Cosine"}},
                timeout=self.timeout,
            )
            if not r2.ok:
                raise RuntimeError(f"No se pudo crear {nombre}: {r2.status_code} {r2.text[:150]}")

    def asegurar_index(self, nombre: str, campo: str = "documento_id") -> None:
        """Payload index (keyword) para poder filtrar/borrar por `campo`."""
        r = self._s.put(
            f"{self.url}/collections/{nombre}/index",
            json={"field_name": campo, "field_schema": "keyword"},
            timeout=self.timeout,
        )
        # 409 = ya existe (idempotente); 200 = creado
        if r.status_code not in (200, 409) and not r.ok:
            raise RuntimeError(
                f"No se pudo indexar {campo} en {nombre}: {r.status_code} {r.text[:150]}")

    def upsert(self, nombre: str, puntos: list[dict]) -> bool:
        """puntos: [{id, vector, payload}]"""
        if not puntos:
            return True
        r = self._s.put(
            f"{self.url}/collections/{nombre}/points?wait=true",
            json={"points": puntos},
            timeout=self.timeout,
        )
        return r.ok

    def borrar_por_sentencia(self, nombre: str, documento_id: str) -> bool:
        r = self._s.post(
            f"{self.url}/collections/{nombre}/points/delete?wait=true",
            json={"filter": {"must": [{"key": "documento_id",
                                       "match": {"value": documento_id}}]}},
            timeout=self.timeout,
        )
        return r.ok