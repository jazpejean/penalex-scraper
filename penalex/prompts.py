"""Prompts del pipeline de análisis (heredados del notebook Colab)."""

PROMPT_SISTEMA = """Eres un asistente jurídico especializado en derecho penal venezolano.
Analizas sentencias del TSJ y devuelves SIEMPRE un JSON válido.
NUNCA inventes datos. Cada afirmación debe tener cita textual del documento."""

PROMPT_USUARIO = """Analiza esta sentencia y devuelve SOLO un JSON:

{
  "extracto": "resumen objetivo del caso, máximo 300 caracteres",
  "ratio_decidendi": "razonamiento jurídico central íntegro, máximo 2000 caracteres, o null",
  "criterios": [{
    "texto_ratio": "CRITERIO DE LA SALA como regla jurídica general aplicable a otros casos, máx 600 caracteres",
    "instrumento": "COPP | Código Penal | Constitución | Código de Enjuiciamiento Criminal | Ley Orgánica de Amparo sobre Derechos y Garantías Constitucionales | Ley Orgánica sobre Sustancias Estupefacientes y Psicotrópicas | otro",
    "articulo_numero": "solo el número (ej: '458') o null",
    "tipo": "interpretacion | aplicacion | reiteracion",
    "referencia_citada": "SOLO si la sentencia analizada cita OTRA sentencia del TSJ como fuente del criterio. Copia literal de su identificación tal como aparece en el texto (ej: 'Sentencia Nº 142, de fecha 26-04-2011, Sala de Casación Penal, ponente Blanca Rosa Mármol de León'). Si no hay sentencia citada, null.",
    "parrafo_evidencia": "CITA TEXTUAL ÍNTEGRA del párrafo del documento que contiene el criterio. Copia literal, entre 150 y 600 chars, sin truncar a mitad de frase."
  }]
}

DEFINICIONES:

**extracto**: resumen del caso concreto (qué pasó, quiénes, qué se decidió).

**ratio_decidendi**: razonamiento jurídico central, íntegro. NO truncar.

**criterios**: criterios jurisprudenciales de la Sala, cada uno con su cita textual.

REGLA CRÍTICA para criterios:
Un CRITERIO es una regla jurídica general de la Sala aplicable a OTROS casos,
una interpretación o principio que la Sala fija al decidir (el "porqué" de lo
resuelto): p.ej. cómo se valora la prueba, qué exige la motivación, el alcance
del in dubio pro reo, cuándo procede el recurso...
**texto_ratio** es el criterio formulado como regla/principio.
**parrafo_evidencia** es la CITA TEXTUAL que un abogado citaría en un escrito.
Debe ser copia literal del documento. NO parafrasear. NO resumir.

EXTRACCIÓN COMPLETA — IMPORTANTÍSIMO:
Una sentencia de fondo puede fundarse en VARIOS criterios (citar varias
sentencias del TSJ, p.ej. sobre valoración probatoria, pruebas indiciarias,
in dubio pro reo, carga de la prueba, motivación...). Extrae TODOS los
criterios por los que la Sala decidió, SIN LÍMITE DE CANTIDAD: cada uno con
su cita textual literal. No recortes a "los más relevantes": si la sentencia
se apoya en 5 u 8 criterios, devuélvelos TODOS.

SOLO criterios jurídicos relevantes y buscables (reglas generales, estándares,
doctrina de institutos). NO incluyas frases de transición, fórmulas procesales,
relleno, ni el voto salvado (es disidencia, no es por lo que la Sala decidió).
Máximo 12 criterios por sentencia.

REGLA OBLIGATORIA: si ratio_decidendi NO es null, entonces criterios DEBE tener
al menos 1 elemento. El ratio_decidendi es solo el resumen; el criterio contiene
el desarrollo completo con la cita textual.

EJEMPLO 1:

ENTRADA:
"La Sala observa que el recurso de casación interpuesto carece de la debida
fundamentación, toda vez que el recurrente no indicó de manera clara y precisa
cuál fue el artículo infringido ni la forma en que se produjo la infracción.
En consecuencia, el recurso debe ser desestimado por manifiestamente infundado."

SALIDA:
{
  "extracto": "La Sala desestima el recurso de casación por falta de fundamentación.",
  "ratio_decidendi": "El recurso de casación debe indicar con precisión el artículo infringido y la forma en que se produjo la infracción; de lo contrario es manifiestamente infundado.",
  "criterios": [
    {
      "texto_ratio": "El recurso de casación debe indicar con precisión el artículo infringido y la forma en que se produjo la infracción.",
      "instrumento": "COPP",
      "articulo_numero": "458",
      "tipo": "interpretacion",
      "referencia_citada": null,
      "parrafo_evidencia": "Esta Sala observa que el recurso de casación interpuesto carece de la debida fundamentación, toda vez que el recurrente no indicó de manera clara y precisa cuál fue el artículo infringido ni la forma en que se produjo la infracción. En consecuencia, el recurso debe ser desestimado por manifiestamente infundado."
    }
  ]
}

EJEMPLO 2:

ENTRADA:
"Corresponde a la Sala Constitucional, por su esencia, al ser la máxima protectora
de la Constitución y además ser el garante de la supremacía y efectividad de las
normas y principios constitucionales, de acuerdo con el artículo 335 de la
Constitución, el conocimiento directo, en única instancia, de las acciones de amparo
incoadas contra los altos funcionarios a que se refiere dicho artículo."

SALIDA:
{
  "extracto": "La Sala Constitucional establece su competencia en materia de amparo contra altos funcionarios.",
  "ratio_decidendi": "Corresponde a la Sala Constitucional el conocimiento directo en única instancia de las acciones de amparo contra altos funcionarios y contra decisiones de última instancia que infrinjan normas constitucionales.",
  "criterios": [
    {
      "texto_ratio": "Corresponde a la Sala Constitucional conocer en única instancia las acciones de amparo contra altos funcionarios.",
      "instrumento": "Ley Orgánica de Amparo sobre Derechos y Garantías Constitucionales",
      "articulo_numero": "8",
      "tipo": "interpretacion",
      "referencia_citada": null,
      "parrafo_evidencia": "Corresponde a la Sala Constitucional, por su esencia, al ser la máxima protectora de la Constitución y además ser el garante de la supremacía y efectividad de las normas y principios constitucionales, de acuerdo con el artículo 335 de la Constitución, el conocimiento directo, en única instancia, de las acciones de amparo incoadas contra los altos funcionarios a que se refiere dicho artículo."
    }
  ]
}

EJEMPLO 3 (sentencia de trámite):

ENTRADA:
"Visto el recurso de casación interpuesto por la defensa del acusado, esta Sala
ACUERDA admitirlo y convocar a la audiencia oral y pública..."

SALIDA:
{
  "extracto": "La Sala admite el recurso de casación y convoca a audiencia oral.",
  "ratio_decidendi": null,
  "criterios": []
}

---

Si la sentencia NO establece criterio general (trámite: admisión, competencia,
radicación, conflicto, extradición) → "criterios": [] y ratio_decidendi: null.

Reglas finales:
- parrafo_evidencia DEBE ser copia literal del texto.
- Sin texto fuera del JSON.

TEXTO:
---
{texto}
---

JSON:"""

MAX_CHARS_TEXTO = 60000  # límite de texto enviado al modelo (Gemini: 1M tokens)
MAX_RATIO_CHARS = 3000   # según prompt
MAX_EXTRACTO_CHARS = 500


def construir_prompt(texto: str, max_chars: int = MAX_CHARS_TEXTO) -> str:
    """Construye el prompt. Si el texto excede el límite, conserva la parte
    FINAL (donde vive el ratio decidendi, ~38-82% del documento) en vez del
    inicio, más un fragmento del inicio para contexto del extracto."""
    if len(texto) > max_chars:
        final = texto[-int(max_chars * 0.85):]
        inicio = texto[: int(max_chars * 0.15)]
        texto = f"{inicio}\n[…]\n{final}"
    return PROMPT_USUARIO.replace("{texto}", texto)