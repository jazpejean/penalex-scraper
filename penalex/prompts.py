"""Prompts del pipeline de análisis (heredados del notebook Colab)."""

PROMPT_SISTEMA = """Eres un asistente jurídico especializado en derecho penal venezolano.
Analizas sentencias del TSJ y devuelves SIEMPRE un JSON válido.
NUNCA inventes datos. Cada afirmación debe tener cita textual del documento."""

PROMPT_USUARIO = """Analiza esta sentencia y devuelve SOLO un JSON:

{
  "extracto": "CONTEXTO DEL CASO: resumen completo que un abogado necesita para aplicar el criterio: qué ocurrió (hechos relevantes), quiénes son las partes, qué recurso se interpuso y qué decidió la Sala. Máximo 800 caracteres. NO es solo la decisión: incluye el contexto fáctico y procesal.",
  "ratio_decidendi": "razonamiento jurídico central íntegro, máximo 2000 caracteres, o null. DEBE ser una idea COMPLETA: empieza y termina la frase entera, con sujeto, verbo y complemento. PROHIBIDO cortar a mitad de frase o terminar en 'de', 'que', 'para', 'y', 'el', 'la', 'los', 'en', 'con', 'por' u otra palabra que pida continuación. Si no cabe, recorta por el INICIO, nunca por el final: la oración SIEMPRE debe terminar cerrada con punto.",
  "pasajes": ["CITA LITERAL del párrafo más relevante del razonamiento", "... hasta 8 pasajes"],
  "criterios": [{
    "texto_ratio": "CRITERIO DE LA SALA como regla jurídica general aplicable a otros casos, máx 600 caracteres",
    "instrumento": "COPP | Código Penal | Constitución | Código de Enjuiciamiento Criminal | Ley Orgánica de Amparo sobre Derechos y Garantías Constitucionales | Ley Orgánica sobre Sustancias Estupefacientes y Psicotrópicas | otro",
    "articulo_numero": "solo el número (ej: '458') o null",
    "tipo": "interpretacion | aplicacion | reiteracion",
    "referencia_citada": "SOLO si la sentencia analizada cita OTRA sentencia del TSJ como fuente del criterio. Copia literal de su identificación tal como aparece en el texto (ej: 'Sentencia Nº 142, de fecha 26-04-2011, Sala de Casación Penal, ponente Blanca Rosa Mármol de León'). Si no hay sentencia citada, null.",
    "parrafo_evidencia": "CITA TEXTUAL ÍNTEGRA del párrafo del documento que contiene el criterio. Copia literal, entre 150 y 600 chars, sin truncar a mitad de frase."
  }],
  "citas_doctrinales": [{
    "autor": "NOMBRE COMPLETO del autor o autora citada (ej: 'Humberto Cuenca', 'Hernando Grisanti Aveledo'). null si no se menciona autor.",
    "obra": "TÍTULO de la obra, tratado o artículo doctrinal citado (ej: 'Tratado de Derecho Procesal Penal'). Copia literal del texto. null si la sentencia solo menciona al autor sin título de obra.",
    "pagina": "NÚMERO DE PÁGINA (o tomo y página) de la obra citada, SOLO si la sentencia lo indica explícitamente (ej: 'p. 128', 'pág. 250', 'Tomo II, p. 75'). Copia el número tal como aparece. null si la sentencia NO menciona página.",
    "parrafo_evidencia": "CITA TEXTUAL ÍNTEGRA del párrafo donde la Sala invoca la doctrina académica. Copia literal, entre 80 y 500 chars, sin truncar a mitad de frase."
  }]
}

DEFINICIONES:

**extracto**: resumen del caso concreto (qué pasó, quiénes, qué se decidió).

**ratio_decidendi**: razonamiento jurídico central, íntegro. NO truncar.

**pasajes**: LOS PÁRRAFOS DEL TEXTO QUE VALEN LA PENA. Copias LITERALES de los
fragmentos del documento que contienen el razonamiento jurídico esencial: la
motivación del fallo, la doctrina aplicada, la interpretación de la norma, la
valoración probatoria clave, el fundamento de la decisión. Son los pasajes que
un abogado citaría textualmente en un escrito para sostener su argumento.
Reglas:
- COPIA LITERAL del texto, sin parafrasear, sin resumir, sin inventar.
- Cada pasaje entre 150 y 900 caracteres, íntegro (sin truncar a mitad de frase).
- Entre 1 y 8 pasajes por sentencia. Solo los que valen la pena (razonamiento,
  doctrina, fundamento) — NO frases de transición, fórmulas procesales, relleno,
  ni el voto salvado.
- Si la sentencia es de trámite (sin razonamiento de fondo), pasajes: [].

**criterios**: criterios jurisprudenciales de la Sala, cada uno con su cita textual.

**citas_doctrinales**: citas a DOCTRINA ACADÉMICA (autores, tratadistas, obras
jurídicas) que la sentencia invoca como apoyo de su razonamiento — p.ej. "como
enseña el profesor Humberto Cuenca", "según la obra 'Comentarios al Código
Penal'", "doctrina del maestro Grisanti Aveledo". Reglas:
- SOLO si la sentencia cita expresamente a un autor académico o una obra.
- **autor** es el nombre del autor citado; **obra** el título de la obra si
  aparece. Cada cita es un elemento del array (puede haber varias).
- **pagina**: SOLO si la sentencia indica expresamente la página (o tomo y
  página) de la obra citada — copia el número tal como aparece en el texto
  (ej: 'p. 128', 'pág. 250'). Si no la menciona, null. NUNCA inventes una
  página que no esté en el texto.
- **parrafo_evidencia** DEBE ser copia literal del texto donde se hace la cita.
- Si la sentencia NO cita doctrina académica, devuelve "citas_doctrinales": [].
- NO confundir con "referencia_citada" (que es para sentencias del TSJ citadas).

REGLA OBLIGATORIA — citas_doctrinales (BUSCA TODAS, NO OMITAS NINGUNA):
Cada vez que la sentencia mencione a un autor, tratadista, maestro, profesor o
jurista académico, o invoque una obra, tratado, manual o enciclopedia jurídica,
DEBES crear UNA cita por cada mención distinta, aunque la mención sea breve.
Formas típicas que DEBES detectar (con o sin comillas, mayúsculas o minúsculas):
  - "el maestro X manifiesta", "el profesor X señala", "el tratadista X opina"
  - "el autor X, en su obra '...'", "según la doctrina de X"
  - "como lo enseña X", "de acuerdo con X", "cita de X", "X, ob. cit."
  - "la doctrina del doctor X", "el penalista X", "el procesalista X"
  - referencias a obras: "en su obra 'Tratado de Derecho Procesal Penal'",
    "en 'Comentarios al Código Penal'", "Nueva Enciclopedia Jurídica"
Si la sentencia invoca 3 autores distintos, devuelve 3 citas. El autor puede
aparecer en MAYÚSCULAS en el texto (p.ej. "el maestro JOSE RAFAEL MENDOZA") —
copia el nombre tal como aparece. NO dejes fuera ninguna cita por parecer
"menor" o incidental: si la Sala la invocó como apoyo, es una cita.

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
REGLA OBLIGATORIA 2: si la sentencia tiene criterios (criterios NO vacío),
entonces citas_doctrinales DEBE incluir todas las citas a autores/obras que el
texto contenga. Un criterio y una cita doctrinal NO son lo mismo: la cita va en
"citas_doctrinales", el criterio de la Sala en "criterios".

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
  "pasajes": ["La Sala observa que el recurso de casación interpuesto carece de la debida fundamentación, toda vez que el recurrente no indicó de manera clara y precisa cuál fue el artículo infringido ni la forma en que se produjo la infracción. En consecuencia, el recurso debe ser desestimado por manifiestamente infundado."],
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
  "pasajes": ["Corresponde a la Sala Constitucional, por su esencia, al ser la máxima protectora de la Constitución y además ser el garante de la supremacía y efectividad de las normas y principios constitucionales, de acuerdo con el artículo 335 de la Constitución, el conocimiento directo, en única instancia, de las acciones de amparo incoadas contra los altos funcionarios a que se refiere dicho artículo."],
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

EJEMPLO 3 (con citas a doctrina académica):

ENTRADA:
"En este sentido, el maestro JOSE RAFAEL MENDOZA manifiesta, refiriéndose a la
agravante de amenazas a la vida, que constituye un delito de peligro. Por su
parte, el autor nacional Osvaldo Parilli Araujo, en su obra 'Comentarios a la
Ley Orgánica de la Justicia de Paz' (Móvil-Libros 1995), señala que el juez de
la apelación será el de la justicia ordinaria. La Sala acoge estos criterios."

SALIDA:
{
  "extracto": "La Sala acoge los criterios doctrinales citados sobre la agravante de amenazas y la justicia de paz.",
  "ratio_decidendi": "La Sala acoge la doctrina citada, según la cual la amenaza a la vida es un delito de peligro y el juez de la apelación en la justicia de paz es el de la justicia ordinaria.",
  "pasajes": ["En este sentido, el maestro JOSE RAFAEL MENDOZA manifiesta, refiriéndose a la agravante de amenazas a la vida, que constituye un delito de peligro."],
  "criterios": [
    {
      "texto_ratio": "La amenaza a la vida es un delito de peligro, conforme a la doctrina del maestro Mendoza.",
      "instrumento": "Código Penal",
      "articulo_numero": null,
      "tipo": "aplicacion",
      "referencia_citada": null,
      "parrafo_evidencia": "En este sentido, el maestro JOSE RAFAEL MENDOZA manifiesta, refiriéndose a la agravante de amenazas a la vida, que constituye un delito de peligro."
    }
  ],
  "citas_doctrinales": [
    {
      "autor": "JOSE RAFAEL MENDOZA",
      "obra": null,
      "pagina": null,
      "parrafo_evidencia": "En este sentido, el maestro JOSE RAFAEL MENDOZA manifiesta, refiriéndose a la agravante de amenazas a la vida, que constituye un delito de peligro."
    },
    {
      "autor": "Osvaldo Parilli Araujo",
      "obra": "Comentarios a la Ley Orgánica de la Justicia de Paz",
      "pagina": null,
      "parrafo_evidencia": "Por su parte, el autor nacional Osvaldo Parilli Araujo, en su obra 'Comentarios a la Ley Orgánica de la Justicia de Paz' (Móvil-Libros 1995), señala que el juez de la apelación será el de la justicia ordinaria."
    }
  ]
}

EJEMPLO 4 (sentencia de trámite):

ENTRADA:
"Visto el recurso de casación interpuesto por la defensa del acusado, esta Sala
ACUERDA admitirlo y convocar a la audiencia oral y pública..."

SALIDA:
{
  "extracto": "La Sala admite el recurso de casación y convoca a audiencia oral.",
  "ratio_decidendi": null,
  "pasajes": [],
  "criterios": []
}

---

Si la sentencia NO establece criterio general (trámite: admisión, competencia,
radicación, conflicto, extradición) → "criterios": [], "pasajes": [] y
ratio_decidendi: null.

Reglas finales:
- parrafo_evidencia y pasajes DEBEN ser copias literales del texto.
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

# ============================================================================
# ESTÁNDAR ESTRICTO: criterio LITERAL (verificable por código, sin parafraseo)
# ============================================================================
PROMPT_CRITERIO_LITERAL = """Extrae de esta sentencia la REGLA JURÍDICA que la Sala
establece, COPIÁNDOLA LITERALMENTE del texto. NO parafrasees, NO resumas, NO
corrijas, NO agregues palabras. Devuelve SOLO un JSON:

{
  "encontrado": true | false,
  "criterio_textual": "COPIA EXACTA de la oración o período continuo del documento donde la Sala enuncia la regla. Debe empezar y terminar en el propio texto (sin puntos suspensivos, sin corchetes, sin comillas añadidas). Entre 80 y 700 caracteres. Copia carácter por carácter, incluidos errores del original.",
  "instrumento": "COPP | Código Penal | Constitución | Código de Enjuiciamiento Criminal | Ley Orgánica de Amparo sobre Derechos y Garantías Constitucionales | Ley Orgánica sobre Sustancias Estupefacientes y Psicotrópicas | otro",
  "articulo_numero": "número del artículo sobre el que trata la regla, o null",
  "es_fundamento": true | false
}

REGLAS ABSOLUTAS:
- "criterio_textual" debe ser un SUBSTRING EXACTO del documento (se valida por
  código). Si no puedes copiar sin alterar, devuelve "encontrado": false.
- NO incluyas el número de sentencia ni encabezados: solo el párrafo de la regla.
- Si la sentencia es de mero trámite (admisión, competencia, extradición, radicación,
  reposición) y NO enuncia una regla de fondo: {"encontrado": false}.
- "es_fundamento": true solo si esa regla es el FUNDAMENTO de la decisión; false si
  el artículo se menciona de pasada.

TEXTO DE LA SENTENCIA:
{texto}
"""


def construir_prompt_literal(texto: str, max_chars: int = MAX_CHARS_TEXTO) -> str:
    """Prompt del estándar estricto: criterio copiado literalmente."""
    if len(texto) > max_chars:
        final = texto[-int(max_chars * 0.85):]
        inicio = texto[: int(max_chars * 0.15)]
        texto = f"{inicio}\n[…]\n{final}"
    return PROMPT_CRITERIO_LITERAL.replace("{texto}", texto)
