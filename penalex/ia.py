"""Cliente de IA remota: análisis de sentencias con Cloudflare Workers AI
(JSON mode) y validación de citas textuales contra el documento fuente."""
from __future__ import annotations

import json
import random
import re
import time
from typing import Optional

import requests

from .config import Config
from .prompts import PROMPT_SISTEMA, construir_prompt
from .util import (evidencia_coincide, referencia_coincide, autor_coincide,
                   obra_coincide, pagina_coincide)

INSTRUMENTOS_VALIDOS = {
    "copp": "COPP",
    "código orgánico procesal penal": "COPP",
    "codigo organico procesal penal": "COPP",
    "código orgánico del procedimiento penal": "COPP",
    "código adjetivo penal": "COPP",
    "código penal": "Código Penal",
    "codigo penal": "Código Penal",
    "constitución": "Constitución",
    "constitucion": "Constitución",
    "constitución de la república bolivariana de venezuela": "Constitución",
    "código de enjuiciamiento criminal": "Código de Enjuiciamiento Criminal",
    "codigo de enjuiciamiento criminal": "Código de Enjuiciamiento Criminal",
    "ley orgánica de amparo": "Ley Orgánica de Amparo sobre Derechos y Garantías Constitucionales",
    "ley orgánica de amparo sobre derechos y garantías constitucionales": "Ley Orgánica de Amparo sobre Derechos y Garantías Constitucionales",
    "ley orgánica sobre sustancias estupefacientes y psicotrópicas": "Ley Orgánica sobre Sustancias Estupefacientes y Psicotrópicas",
    "ley orgánica para la protección del niño y del adolescente": "LOPNA",
    "lopna": "LOPNA",
    "código orgánico de justicia militar": "Código Orgánico de Justicia Militar",
}

TIPOS_VALIDOS = {"interpretacion", "aplicacion", "reiteracion"}


def normalizar_instrumento(inst: Optional[str]) -> Optional[str]:
    if not inst:
        return None
    k = str(inst).strip().lower()
    if k in ("otro", "n/a", "null", "none", ""):
        return "otro"
    return INSTRUMENTOS_VALIDOS.get(k, "otro")


def limpiar_articulo(art: Optional[str]) -> Optional[str]:
    if not art:
        return None
    s = str(art).strip()
    if s.lower() in ("n/a", "no especificado", "no aplica", "ninguno", "null", "none", ""):
        return None
    s = re.sub(r"[^\d,\-]", "", s)
    s = re.sub(r",+", ",", s).strip(",-")
    return s or None


def _extraer_json(respuesta: str) -> dict:
    """Extrae el primer objeto JSON del texto de respuesta (tolerante)."""
    m = re.search(r"\{[\s\S]*\}", respuesta)
    if not m:
        raise ValueError("No se encontró JSON en la respuesta")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        # quita comas sobrantes antes de ] o }
        data = json.loads(re.sub(r",\s*([\]}])", r"\1", m.group(0)))
    # El modelo puede devolver "criterios" (nuevo) o "doctrinas" (legacy): unificar
    if "criterios" not in data and "doctrinas" in data:
        data["criterios"] = data.pop("doctrinas")
    return data


def validar_criterios(data: dict, texto: str) -> list[dict]:
    """Filtra criterios: solo los que tienen cita textual verificable en el documento.

    Anti-alucinación garantizada por código:
    - parrafo_evidencia DEBE ser substring literal del texto fuente.
    - referencia_citada (si la IA la declara) DEBE aparecer literalmente en el
      texto; si la IA la inventó, se descarta el criterio (nunca se guarda una
      referencia falsa que el redactor podría citar).
    """
    validas = []
    for d in data.get("criterios", []) or []:
        if not isinstance(d, dict):
            continue
        ev = (d.get("parrafo_evidencia") or "").strip()
        if not ev or len(ev) < 40:
            continue
        if not evidencia_coincide(texto, ev):
            continue
        ref = (d.get("referencia_citada") or "").strip()
        if ref:
            # La referencia de la sentencia citada debe verificarse por
            # componentes (número+fecha+sala+ponente presentes en el texto).
            if not referencia_coincide(texto, ref):
                continue
            d["referencia_citada"] = ref[:250]
        else:
            d["referencia_citada"] = None
        d["instrumento"] = normalizar_instrumento(d.get("instrumento"))
        d["articulo_numero"] = limpiar_articulo(d.get("articulo_numero"))
        d["tipo"] = d.get("tipo") if d.get("tipo") in TIPOS_VALIDOS else "interpretacion"
        validas.append(d)
    data["doctrinas"] = validas  # alias interno: la BD usa la tabla "doctrinas"
    data["criterios"] = validas
    return validas


# alias de compatibilidad (nombre anterior)
validar_doctrinas = validar_criterios


def validar_citas_doctrinales(data: dict, texto: str) -> list[dict]:
    """Filtra citas a DOCTRINA ACADÉMICA (autor/obra) extraídas por la IA.

    Anti-alucinación garantizada por código (igual que criterios):
    - parrafo_evidencia DEBE ser substring literal del texto fuente.
    - autor (si se declara) DEBE coincidir con el texto (autor_coincide).
    - obra (si se declara) DEBE coincidir con el texto (obra_coincide).
    - pagina (si se declara) DEBE aparecer literalmente (pagina_coincide).
    - Sin evidencia o sin autor/obra verificable -> se descarta la cita.
    """
    validas = []
    citas = data.get("citas_doctrinales") or []
    if not isinstance(citas, list):
        citas = []
    for d in citas:
        if not isinstance(d, dict):
            continue
        ev = (d.get("parrafo_evidencia") or "").strip()
        autor = (d.get("autor") or "").strip()
        obra = (d.get("obra") or "").strip()
        pagina = (d.get("pagina") or "").strip()
        if not ev or len(ev) < 40:
            continue
        if not evidencia_coincide(texto, ev):
            continue
        if not autor and not obra:
            continue
        if autor and not autor_coincide(texto, autor):
            continue
        if obra and not obra_coincide(texto, obra):
            continue
        if pagina and not pagina_coincide(texto, pagina):
            continue
        validas.append({
            "autor": autor[:200] or None,
            "obra": obra[:250] or None,
            "pagina": pagina[:40] or None,
            "parrafo_evidencia": ev,
        })
        if len(validas) >= 12:
            break
    data["citas_doctrinales"] = validas
    return validas



def validar_pasajes(data: dict, texto: str) -> list[str]:
    """Filtra los pasajes que la IA marcó como valiosos: cada uno DEBE ser una
    cita literal verificable en el documento fuente (anti-alucinación por
    código). Devuelve lista de pasajes válidos, máx 8, entre 150 y 900 chars."""
    validos = []
    pasajes = data.get("pasajes") or []
    if not isinstance(pasajes, list):
        pasajes = []
    for p in pasajes:
        if not isinstance(p, str):
            continue
        t = p.strip()
        if len(t) < 150 or len(t) > 900:
            continue
        if not evidencia_coincide(texto, t):
            continue
        validos.append(t)
        if len(validos) >= 8:
            break
    data["pasajes"] = validos
    return validos


class AnalizadorIA:
    """Analiza sentencias usando un modelo remoto (Gemini API OpenAI-compatible,
    puter.com driver, o Cloudflare Workers AI, según qué key exista en el
    entorno; orden de fallback: Gemini → puter → Workers AI)."""

    def __init__(self, cfg: Config, timeout: int = 120, reintentos: int = 12):
        cfg.requerir("ia")
        self.cfg = cfg
        self.timeout = timeout
        self.reintentos = reintentos
        # True cuando TODAS las keys del proveedor devuelven 429 de forma
        # persistente (RPD del día agotado en el plan gratuito): el batch debe
        # CORTAR la pasada y esperar al reset en vez de fallar doc por doc.
        self.rate_limited = False
        self._s = requests.Session()
        # Gemini gana si está presente su key; Workers AI (Qwen) como canal
        # paralelo: cuando Gemini agota su RPD diario, el lote cambia a Qwen
        # (gratis, sin RPD) y sigue procesando en vez de dormir hasta el reset.
        # puter.com (sesiones gratuitas) es un canal intermedio: Gemini → puter
        # → Workers AI.
        if cfg.gemini_api_keys:
            self.proveedor = "gemini"
            # Rotación de keys gratis: cada key tiene ~230 RPD/día;
            # N keys => N×230 llamadas diarias antes del corte.
            self._keys = list(cfg.gemini_api_keys)
            # Cada modelo Lite tiene RPD propio (500/día): rotar modelo amplía
            # la cuota sin crear más cuentas.
            self._gemini_modelos = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
            self._modelo_idx = 0
            self._ultima_llamada = {}
            self._min_intervalo = 6.0
            # Desfase inicial: dos procesos con el mismo _key_idx=0 martillan
            # la misma key y disparan 429 por RPM aunque haya 9 libres.
            import random as _rnd
            self._key_idx = _rnd.randrange(len(self._keys))
            self._keys_agotadas = set()
            self._rpm_espera = {}   # idx -> instante hasta el que esperar
            self._s.headers.update({
                "Authorization": f"Bearer {self._keys[0]}",
                "Content-Type": "application/json",
            })
        elif cfg.puter_tokens:
            self.proveedor = "puter"
            self._tokens_puter = list(cfg.puter_tokens)
            self._token_puter_idx = 0
            self._aplicar_headers_puter()
            # saltar cuentas sin créditos conocidas (1,2,3,4)
            self._saltar_cuentas_sin_saldo()
        else:
            self.proveedor = "workers"
            # Varias cuentas Workers AI (cada una con su tope de neuronas/día):
            # se rota entre ellas cuando una devuelve 429.
            self._cuentas = list(cfg.cf_accounts)
            self._cuenta_idx = 0
            self._aplicar_headers_workers()

    def _aplicar_headers_workers(self) -> None:
        """Headers según la cuenta Workers AI activa: Bearer (token cfut_) o
        X-Auth-Key + X-Auth-Email (Global Key cfk_, como la cuenta nueva)."""
        cuenta = self._cuentas[self._cuenta_idx]
        acc, key, email = cuenta
        h = {"Content-Type": "application/json"}
        if email:
            h["X-Auth-Key"] = key
            h["X-Auth-Email"] = email
        else:
            h["Authorization"] = f"Bearer {key}"
        self._s.headers.update(h)

    def _aplicar_headers_puter(self) -> None:
        """Headers del driver puter: el token NO va en Authorization sino en el
        body (campo auth_token); Content-Type propio del driver (actually=json)."""
        self._s.headers.update({"Content-Type": "text/plain;actually=json"})

    # Cuentas de puter sin créditos / caducadas (se saltan al entrar)
    _PUTER_SIN_SALDO = {0, 1, 2, 3}

    def _saltar_cuentas_sin_saldo(self) -> None:
        if self.proveedor != "puter":
            return
        for i in range(len(self._tokens_puter)):
            if i not in self._PUTER_SIN_SALDO:
                self._token_puter_idx = i
                self._aplicar_headers_puter()
                return

    def _rotar_token_puter(self) -> bool:
        """Cambia a la siguiente cuenta puter (otra sesión => otro cupo diario).
        False si todas quedaron agotadas."""
        if self.proveedor != "puter" or len(self._tokens_puter) <= 1:
            return False
        if self._token_puter_idx + 1 < len(self._tokens_puter):
            self._token_puter_idx += 1
            print(f"      [puter] rotando a cuenta #{self._token_puter_idx + 1} "
                  f"de {len(self._tokens_puter)}", flush=True)
            return True
        return False

    def _cambiar_a_puter(self) -> bool:
        """Cambia el proveedor activo a puter.com cuando Gemini agotó su RPD
        diario. Devuelve True si hay tokens de puter."""
        if not self.cfg.puter_tokens:
            return False
        self.proveedor = "puter"
        self.rate_limited = False
        self._tokens_puter = list(self.cfg.puter_tokens)
        self._token_puter_idx = 0
        self._aplicar_headers_puter()
        print(f"      [fallback] Gemini agotado -> puter "
              f"({len(self._tokens_puter)} cuentas)", flush=True)
        return True

    def _rotar_cuenta_workers(self) -> bool:
        """Cambia a la siguiente cuenta Workers AI (otra cuenta => otro tope
        diario de neuronas). False si todas quedaron agotadas."""
        if self.proveedor != "workers" or len(self._cuentas) <= 1:
            return False
        if self._cuenta_idx + 1 < len(self._cuentas):
            self._cuenta_idx += 1
            self._aplicar_headers_workers()
            print(f"      [cuenta CF] rotando a cuenta #{self._cuenta_idx + 1} "
                  f"de {len(self._cuentas)}", flush=True)
            return True
        return False

    def _cambiar_a_workers(self) -> bool:
        """Cambia el proveedor activo a Cloudflare Workers AI (Qwen) cuando
        Gemini o puter agotaron su cupo diario. Devuelve True si hay key de
        Workers AI."""
        if not self.cfg.cf_api_key:
            return False
        self.proveedor = "workers"
        self.rate_limited = False
        self._cuentas = list(self.cfg.cf_accounts)
        self._cuenta_idx = 0
        self._aplicar_headers_workers()
        print("      [fallback] agotado -> Workers AI (Qwen)", flush=True)
        return True

    def _rotar_key(self) -> bool:
        """Marca la key actual como agotada y pasa a otra DISPONIBLE.

        False si ya no queda ninguna sin agotar (=> cambiar de proveedor).
        """
        if self.proveedor != "gemini" or not self._keys:
            return False
        self._keys_agotadas.add(self._key_idx)
        for paso in range(1, len(self._keys) + 1):
            idx = (self._key_idx + paso) % len(self._keys)
            if idx not in self._keys_agotadas:
                self._key_idx = idx
                self._s.headers.update({
                    "Authorization": f"Bearer {self._keys[idx]}"})
                print(f"      [key] rotando a key #{idx + 1} de {len(self._keys)} "
                      f"(agotadas {len(self._keys_agotadas)})", flush=True)
                return True
        return False

    def _esperar_cupo(self) -> None:
        """Espacia las llamadas de la MISMA key (~13 RPM) para no disparar 429.

        Gemini permite 15 RPM por cuenta; con varios procesos compitiendo, sin
        este espaciado local se supera el límite y todo falla en cascada.
        """
        if self.proveedor != "gemini":
            return
        ahora = time.time()
        ultima = self._ultima_llamada.get(self._key_idx, 0)
        espera = self._min_intervalo - (ahora - ultima)
        if espera > 0:
            time.sleep(espera)
        self._ultima_llamada[self._key_idx] = time.time()

    def _rr_key(self) -> None:
        """Round-robin: avanza a la siguiente key disponible en CADA llamada
        para repartir la cuota (evita agotar una sola key mientras hay 15 libres)."""
        if self.proveedor != "gemini" or len(self._keys) <= 1:
            return
        for paso in range(1, len(self._keys) + 1):
            idx = (self._key_idx + paso) % len(self._keys)
            if idx not in self._keys_agotadas:
                self._key_idx = idx
                self._s.headers.update({
                    "Authorization": f"Bearer {self._keys[idx]}"})
                return

    def _chat_url(self, modelo: Optional[str]) -> str:
        """Endpoint del proveedor activo."""
        if self.proveedor == "gemini":
            # OpenAI-compatible: https://ai.google.dev/gemini-api/docs/openai
            return f"{self.cfg.gemini_base_url}/chat/completions"
        if self.proveedor == "puter":
            return self.cfg.puter_base_url
        # Workers AI: /ai/run nativo (techo ~38 req/min) en vez del
        # OpenAI-compat /ai/v1/chat/completions (techo ~3-5 req/min).
        m = modelo or self.cfg.modelo_analisis
        acc = self._cuentas[self._cuenta_idx][0]  # account_id de la cuenta activa
        return f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/{m}"

    def _modelo(self, modelo: Optional[str]) -> str:
        """Nombre del modelo a usar según el proveedor activo."""
        if self.proveedor == "gemini":
            if modelo:
                return modelo.replace("@cf/", "")
            mods = getattr(self, "_gemini_modelos", None)
            if mods:
                return mods[self._modelo_idx]
            return self.cfg.gemini_model.replace("@cf/", "")
        if self.proveedor == "puter":
            return (modelo or self.cfg.puter_model).replace("@cf/", "")
        return modelo or self.cfg.modelo_analisis

    def completar(self, mensajes: list[dict], json_mode: bool = True,
                  max_tokens: int = 8192) -> Optional[dict]:
        """Chat libre reutilizando la infraestructura (proveedores, retry, 429).

        Devuelve el JSON parseado o None. NO aplica validaciones del análisis."""
        modelo_resuelto = self._modelo(None)
        ultimo = None
        for i in range(self.reintentos):
            try:
                if self.proveedor == "puter":
                    payload = {
                        "interface": "puter-chat-completion",
                        "driver": "ai-chat",
                        "method": "complete",
                        "args": {
                            "messages": mensajes,
                            "model": modelo_resuelto,
                            "max_tokens": max_tokens,
                            "temperature": 0,
                        },
                        "auth_token": self._tokens_puter[self._token_puter_idx],
                    }
                    r = self._s.post(
                        self._chat_url(None), data=json.dumps(payload),
                        timeout=self.timeout)
                else:
                    body = {"model": modelo_resuelto, "messages": mensajes,
                            "max_tokens": max_tokens, "temperature": 0}
                    if json_mode:
                        body["response_format"] = {"type": "json_object"}
                    r = self._s.post(self._chat_url(None), json=body,
                                     timeout=self.timeout)
                if r.status_code == 200:
                    j = r.json()
                    if self.proveedor == "puter":
                        contenido = (j.get("result") or {}) \
                            .get("message", {}).get("content") or ""
                    else:
                        resultado = j.get("result") or j
                        try:
                            contenido = (resultado.get("choices") or [{}])[0] \
                                .get("message", {}).get("content") or ""
                        except (AttributeError, IndexError):
                            contenido = ""
                    if not contenido.strip():
                        ultimo = "contenido vacío"
                        if i < 3:
                            time.sleep(4 + i * 4)
                            continue
                        return None
                    if json_mode:
                        return _extraer_json(contenido)
                    return {"contenido": contenido}
                ultimo = f"HTTP {r.status_code}: {r.text[:120]}"
                if r.status_code in (429, 402):
                    if self.proveedor == "gemini" and self._rotar_key():
                        continue
                    if self.proveedor == "gemini" and self._cambiar_a_puter():
                        continue
                    if self.proveedor in ("gemini", "puter") and self._cambiar_a_workers():
                        continue
                    if self.proveedor == "puter" and self._rotar_token_puter():
                        continue
                    if self.proveedor == "workers" and self._rotar_cuenta_workers():
                        continue
                    self.rate_limited = True
                    return None
                if r.status_code in (500, 503):
                    if i >= 3:
                        return None
                    time.sleep(8 * (i + 1))
                    continue
                return None
            except ValueError:
                return None
            except Exception as e:  # noqa: BLE001
                ultimo = f"{type(e).__name__}: {str(e)[:120]}"
                if i >= 3:
                    return None
                time.sleep(6 * (i + 1))
        print(f"      [IA falló] {ultimo}", flush=True)
        return None

    def analizar(self, texto: str, modelo: Optional[str] = None,
                 prompt: Optional[str] = None) -> Optional[dict]:
        """Devuelve {extracto, ratio_decidendi, doctrinas} validado, o None si falla.

        `prompt`: si se pasa, se usa en lugar del prompt estándar (estándar
        estricto de criterio literal). En ese modo no se aplican los
        validadores de citas del esquema estándar.
        """
        estricto = prompt is not None
        prompt = prompt or construir_prompt(texto, max_chars=self.cfg.max_chars_texto)
        self._rr_key()   # reparte la cuota entre las keys en cada llamada
        self._esperar_cupo()
        modelo_resuelto = self._modelo(modelo)
        ultimo = None
        for i in range(self.reintentos):
            try:
                if self.proveedor == "puter":
                    # Driver puter: el token va en el body (auth_token), el
                    # Content-Type es propio del driver (text/plain;actually=json).
                    payload = {
                        "interface": "puter-chat-completion",
                        "driver": "ai-chat",
                        "method": "complete",
                        "args": {
                            "messages": [
                                {"role": "system", "content": PROMPT_SISTEMA},
                                {"role": "user", "content": prompt},
                            ],
                            "model": modelo_resuelto,
                            "max_tokens": 8192,
                            "temperature": 0,
                        },
                        "auth_token": self._tokens_puter[self._token_puter_idx],
                    }
                    r = self._s.post(
                        self._chat_url(modelo),
                        data=json.dumps(payload),
                        timeout=self.timeout,
                    )
                else:
                    body = {
                        "model": modelo_resuelto,
                        "messages": [
                            {"role": "system", "content": PROMPT_SISTEMA},
                            {"role": "user", "content": prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": 8192,
                        "temperature": 0,
                    }
                    r = self._s.post(
                        self._chat_url(modelo), json=body, timeout=self.timeout
                    )
                if r.status_code == 200:
                    j = r.json()
                    if self.proveedor == "puter":
                        # Respuesta puter: {"success": true, "result": {..., "message": {"content": ...}}}
                        contenido = (j.get("result") or {}) \
                            .get("message", {}).get("content") or ""
                    else:
                        # Workers AI envuelve en {"result": {...}}; Gemini
                        # OpenAI-compat devuelve {"choices": [...]} al nivel raíz.
                        resultado = j.get("result") or j
                        try:
                            contenido = (resultado.get("choices") or [{}])[0] \
                                .get("message", {}).get("content") or ""
                        except (AttributeError, IndexError):
                            contenido = ""
                    if not contenido.strip():
                        # El modelo devuelve HTTP 200 con contenido vacío de
                        # forma ALEATORIA (~50% hoy, independiente de la carga):
                        # 2-3 intentos recuperan la mayoría. Los docs que fallan
                        # siempre (hard-fail) agotan el contador y quedan
                        # pendientes (el batch los detecta como estancamiento).
                        ultimo = "contenido vacío"
                        if i < 3:
                            time.sleep(4 + i * 4)
                            continue
                        return None
                    data = _extraer_json(contenido)
                    # Anti-basura: si el modelo devuelve la clave como valor
                    # ('ratio_decidendi') o un texto trivial, no se guarda:
                    # la sentencia queda pendiente y se reintenta en otra pasada.
                    ratio_raw = (data.get("ratio_decidendi") or "").strip()
                    if ratio_raw and (len(ratio_raw) < 40
                                      or ratio_raw.lower() == "ratio_decidendi"):
                        raise ValueError("ratio_decidendi basura")
                    # Anti-corte de idea: aunque el JSON cierre con punto, si la
                    # frase termina en conjunción/preposición/artículo que pide
                    # continuación, la idea quedó a medias → descartar y reintentar.
                    # Las palabras cortas matchean SOLO como palabra completa
                    # (lookbehind/lookahead): 'legal' no termina en 'al' palabra.
                    if ratio_raw and re.search(
                            r"(?:porque|ya que|dado que|puesto que|de modo que|"
                            r"de manera que|cuando|donde|como|aunque|si bien|"
                            r"por cuanto|en tanto|siempre que|a menos que|"
                            r"con tal de|pero|sino|pues|entonces|además|"
                            r"incluso|más bien|es decir|el cual|la cual|"
                            r"los cuales|las cuales|cuyo|cuyos|cuya|el que|"
                            r"la que|lo que|tal como|según|respecto de|"
                            r"respecto a|en cuanto a|esto es|lo anterior|"
                            r"no obstante|pese a|a pesar de|en caso de|"
                            r"en virtud de)\s*\.?\s*$"
                            r"|(?<![a-záéíóúüñ])"
                            r"(?:que|de|en|con|por|para|entre|sobre|al|del|"
                            r"el|la|los|las|un|una|y)"
                            r"(?![a-záéíóúüñ])\s*\.?\s*$",
                            ratio_raw.rstrip(), re.IGNORECASE):
                        raise ValueError("ratio_decidendi idea a medias")
                    # Ratio sin cierre: si el modelo truncó a mitad de frase
                    # terminando en sustantivo ('...artículo 458 eiusdem',
                    # '...de la Región Capital'), no es conjunción pero falta el
                    # punto final → la idea quedó a medias → descartar y reintentar.
                    if ratio_raw and len(ratio_raw) > 60 and \
                            ratio_raw.rstrip()[-1] not in ".)”\"'\u2019…":
                        raise ValueError("ratio_decidendi sin cierre")
                    if estricto:
                        return data
                    validar_doctrinas(data, texto)
                    validar_pasajes(data, texto)
                    validar_citas_doctrinales(data, texto)
                    return data
                ultimo = f"HTTP {r.status_code}: {r.text[:120]}"
                if r.status_code in (429, 402):
                    print("      [429] " + (r.text or "")[:260], flush=True)
                    # rate limit / cupo agotado. Plan gratuito: ~230 RPD por key
                    # Gemini; puter por cuenta; Workers AI por cuenta CF.
                    # Gemini: rotar keys; al agotarlas, pasar a puter y luego a
                    # Workers AI. puter: rotar cuentas; al agotarlas, Workers.
                    if self.proveedor == "gemini" and \
                       self._modelo_idx + 1 < len(self._gemini_modelos):
                        self._modelo_idx += 1
                        print(f"      [modelo] rotando a "
                              f"{self._gemini_modelos[self._modelo_idx]}", flush=True)
                        continue
                    if self.proveedor == "gemini" and self._rotar_key():
                        continue  # nueva key, reintentar de inmediato
                    # IMPORTANTE: si ya estamos en puter, primero agotar TODAS
                    # sus cuentas antes de saltar a Workers AI. Con el orden
                    # anterior se abandonaba puter sin probar cuentas con saldo.
                    if self.proveedor == "puter" and self._rotar_token_puter():
                        continue  # otra cuenta puter, reintentar
                    # Solo saltar a puter/workers si NO hay keys de Gemini.
                    # Con keys disponibles, un 429 suele ser RPM: se espera y
                    # se reintenta en Gemini en vez de ir a proveedores muertos.
                    if self.proveedor == "gemini":
                        if not self._keys:
                            self._cambiar_a_puter()
                        time.sleep(8 * (i + 1))
                        continue
                    if self.proveedor == "puter" and self._cambiar_a_workers():
                        continue  # reintentar este doc con Qwen
                    if self.proveedor == "workers" and self._rotar_cuenta_workers():
                        continue  # otra cuenta CF, reintentar
                    self.rate_limited = True
                    return None
                if r.status_code in (500, 503):
                    if i >= 3:
                        return None
                    time.sleep(8 * (i + 1))
                    continue
                return None
            except ValueError as e:
                # Respuesta sin JSON parseable: no reintentar (la sentencia
                # queda pendiente y se reintenta en una pasada posterior).
                return None
            except Exception as e:  # noqa: BLE001
                # timeouts/errores de red: el modelo a veces se cuelga; 3
                # reintentos máx (si no, 40 min por doc en el peor caso)
                ultimo = f"{type(e).__name__}: {str(e)[:120]}"
                if i >= 3:
                    return None
                time.sleep(6 * (i + 1))
        print(f"      [IA falló] {ultimo}", flush=True)
        return None