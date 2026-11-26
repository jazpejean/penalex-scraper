"""Configuración del pipeline: lee secretos y parámetros de variables de entorno
(o de un archivo .env en la raíz del proyecto). Nunca hardcodea secretos.

Variables requeridas según la etapa:
  Scrape:     WORKER_URL, INGEST_TOKEN
  Supabase:   SUPABASE_URL, SUPABASE_SERVICE_KEY
  Qdrant:     QDRANT_URL, QDRANT_API_KEY
  IA remota:  CF_AI_ACCOUNT_ID, CLOUDFLARE_WORKERS_AI_API_KEY
              (también se acepta CF_AI_API_KEY y CUSTOMOPENAI_API_KEY como alias)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Cuenta Cloudflare Workers AI usada por la sesión DSH (no es un secreto).
DEFAULT_CF_ACCOUNT_ID = "a1dc6833be0154e11509f89791ecaa36"

MODEL_ANALISIS_DEFAULT = "@cf/qwen/qwen3-30b-a3b-fp8"
MODEL_EMBEDDING_DEFAULT = "@cf/baai/bge-m3"
MODEL_ANALISIS_ALT = "@cf/deepseek-ai/deepseek-v4-flash-0731"

SALAS = {"scp": "003", "scon": "005"}  # código corto -> SSALAID en el portal

PORTAL_DECISIONES = "https://www.tsj.gob.ve/es/decisiones"
HISTORICO_BASE = "https://historico.tsj.gob.ve/decisiones"


def _cargar_env(ruta: Optional[Path] = None) -> None:
    """Carga un .env simple (VAR=valor) sin sobrescribir vars ya presentes."""
    if ruta is None:
        ruta = Path.cwd() / ".env"
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        k, _, v = linea.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_cargar_env()


@dataclass
class Config:
    """Acceso tipado a la configuración. Las etapas validan lo que necesitan."""

    worker_url: Optional[str] = None
    ingest_token: Optional[str] = None
    supabase_url: Optional[str] = None
    supabase_key: Optional[str] = None
    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None
    r2_public_url: str = "https://pub-ccca0a4f4395464da7752af871c16943.r2.dev"
    cf_account_id: str = DEFAULT_CF_ACCOUNT_ID
    cf_api_key: Optional[str] = None
    cf_api_email: Optional[str] = None  # Global Key (cfk_) usa X-Auth-Email
    # Varias cuentas Workers AI: cada una tiene su propio tope diario de
    # neuronas en el plan gratuito; el analizador rota entre ellas cuando
    # una devuelve 429. Cada entrada: (account_id, api_key, email_opcional).
    cf_accounts: list = field(default_factory=list)
    gemini_api_key: Optional[str] = None
    gemini_api_keys: list = field(default_factory=list)  # varias keys gratis (RPD xN)
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_model: str = "gemini-flash-lite-latest"
    # Canal puter.com: tokens de sesión de cuentas gratuitas (se rotan entre
    # cuentas cuando una devuelve 429/402, igual que las keys de Gemini).
    puter_tokens: list = field(default_factory=list)
    puter_model: str = "deepseek-v4.1-flash"
    puter_base_url: str = "https://api.puter.com/drivers/call"
    max_chars_texto: int = 60000
    min_chars_analisis: int = 3000
    modelo_analisis: str = MODEL_ANALISIS_DEFAULT
    modelo_embedding: str = MODEL_EMBEDDING_DEFAULT
    max_docs: int = 999999
    delay: float = 1.5
    timeout_descarga: int = 120
    reintentos: int = 4
    dry_run: bool = False

    _requeridos: dict = field(default_factory=dict, init=False)

    @classmethod
    def desde_env(cls) -> "Config":
        c = cls(
            worker_url=os.environ.get("WORKER_URL"),
            ingest_token=os.environ.get("INGEST_TOKEN"),
            supabase_url=os.environ.get("SUPABASE_URL"),
            supabase_key=os.environ.get("SUPABASE_SERVICE_KEY")
            or os.environ.get("SUPABASE_KEY"),
            qdrant_url=os.environ.get("QDRANT_URL"),
            qdrant_api_key=os.environ.get("QDRANT_API_KEY"),
            r2_public_url=os.environ.get("R2_PUBLIC_URL",
                                          "https://pub-ccca0a4f4395464da7752af871c16943.r2.dev"),
            cf_account_id=os.environ.get("CF_AI_ACCOUNT_ID", DEFAULT_CF_ACCOUNT_ID),
            cf_api_key=os.environ.get("CLOUDFLARE_WORKERS_AI_API_KEY")
            or os.environ.get("CF_AI_API_KEY")
            or os.environ.get("CUSTOMOPENAI_API_KEY"),
            cf_api_email=os.environ.get("CF_AI_EMAIL"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY"),
            gemini_base_url=os.environ.get(
                "GEMINI_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta/openai",
            ),
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest"),
            puter_model=os.environ.get("PUTER_MODEL", "deepseek-v4.1-flash"),
            puter_base_url=os.environ.get("PUTER_BASE_URL",
                                          "https://api.puter.com/drivers/call"),
            max_chars_texto=int(os.environ.get("MAX_CHARS_TEXTO", "60000")),
            min_chars_analisis=int(os.environ.get("MIN_CHARS_ANALISIS", "3000")),
            modelo_analisis=os.environ.get("MODEL_ANALISIS", MODEL_ANALISIS_DEFAULT),
            modelo_embedding=os.environ.get("MODEL_EMBEDDING", MODEL_EMBEDDING_DEFAULT),
            max_docs=int(os.environ.get("MAX_DOCS", "999999")),
            delay=float(os.environ.get("DELAY", "1.5")),
            timeout_descarga=int(os.environ.get("TIMEOUT_DESCARGA", "120")),
            reintentos=int(os.environ.get("REINTENTOS", "4")),
            dry_run=os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"),
        )
        # GEMINI_API_KEY puede traer varias keys (separadas por coma/espacio):
        # el plan gratuito da ~230 RPD por key; N keys => N×230/día.
        raw = (os.environ.get("GEMINI_API_KEY") or "").replace("\n", ",")
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        c.gemini_api_keys = keys
        c.gemini_api_key = keys[0] if keys else c.gemini_api_key
        # Tokens de puter.com (sesiones de cuentas gratuitas, separadas por
        # coma): se rotan entre cuentas cuando una devuelve 429/402.
        raw_puter = (os.environ.get("PUTER_TOKENS") or "").replace("\n", ",")
        c.puter_tokens = [t.strip() for t in raw_puter.split(",") if t.strip()]
        # Cuentas Workers AI disponibles (rotación cuando una agota neuronas):
        # la principal + cuentas adicionales *_2, *_3, ... en el .env.
        cuentas = []
        principal = (
            os.environ.get("CF_AI_ACCOUNT_ID", DEFAULT_CF_ACCOUNT_ID),
            c.cf_api_key,
            os.environ.get("CF_AI_EMAIL"),
        )
        if principal[1]:
            cuentas.append(principal)
        for sufijo in ("2", "3", "4", "5"):
            acc = os.environ.get(f"CF_AI_ACCOUNT_ID_{sufijo}")
            key = os.environ.get(f"CF_AI_API_KEY_{sufijo}") \
                or os.environ.get(f"CLOUDFLARE_WORKERS_AI_API_KEY_{sufijo}")
            if acc and key:
                cuentas.append((acc, key, os.environ.get(f"CF_AI_EMAIL_{sufijo}")))
        c.cf_accounts = cuentas
        c._requeridos = {
            "scrape": ["worker_url", "ingest_token"],
            "ia": [("cf_api_key", "gemini_api_key", "puter_tokens")],
            "supabase": ["supabase_url", "supabase_key"],
            "qdrant": ["qdrant_url", "qdrant_api_key"],
        }
        return c

    def requerir(self, *etapas: str) -> "Config":
        """Valida que existan las variables de las etapas indicadas.
        Un campo de la forma ("a", "b") exige al menos uno de los dos."""
        faltantes = []
        for e in etapas:
            for campo in self._requeridos.get(e, []):
                if isinstance(campo, tuple):
                    if not any(getattr(self, c) for c in campo):
                        faltantes.append("/".join(campo))
                elif not getattr(self, campo):
                    faltantes.append(campo)
        if faltantes:
            raise RuntimeError(
                "Faltan variables de entorno: "
                + ", ".join(faltantes)
                + ". Créalas en un .env o expórtalas antes de correr."
            )
        return self

    def api_base_ia(self) -> str:
        return f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account_id}/ai/v1"

    def api_base_run(self) -> str:
        """Endpoint nativo de Workers AI (rate limit mucho más alto que /ai/v1)."""
        return f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account_id}/ai/run"