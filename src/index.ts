const hex = (b: ArrayBuffer) => [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, '0')).join('');
// id normalizado: el portal entrega nombres como "…2225 .HTM" o "…0418.. .HTM"
// (espacio/puntos antes de la extensión); se limpia para que id y claves R2
// queden sin espacios finales (los archivos del portal SÍ tienen el espacio,
// así que la descarga usa el nombre original y solo se normaliza el id).
const idDe = (u: string) => u.split('/').pop()!
  .replace(/\.(HTM|HTML|htm|html)$/i, '')
  .replace(/[\s.]+$/g, '');

const LOGO_URL_CORRECTO = 'https://pub-ccca0a4f4395464da7752af871c16943.r2.dev/assets/assets_logo.jpg';

const PATRONES_LOGO = [
  // logo roto de la era 2000-2006: todas las variantes de la ruta a la imagen
  // de Word incrustada, p.ej. "./{id}_archivos/image001.jpg",
  // "{id}_archivos/image001.jpg" o "file://temis/.../{id}_archivos/image001.jpg"
  // (en <img> y en <v:imagedata>). Se reemplaza el texto completo de la ruta
  // por el logo público correcto.
  /[^"'\s]*_archivos\/image001\.(jpg|jpeg|png|gif)/gi,
];

const STOPWORDS_PONENTE = new Set([
  'VISTOS','VISTO','EN','EL','LOS','LAS','SE','POR',
  'MEDIANTE','CONSTA','CUMPLIDA','PARA','QUE','UN','UNA','Y','O',
  'ANTE','ESTE','ESTA','ESTOS','ESTAS','HA','HAN','SON','ES','AL',
]);

function limpiar(s: string): string {
  return s.replace(/<[^>]*>/g, ' ').replace(/&nbsp;?/g, ' ').replace(/\s+/g, ' ').trim();
}

function extraerAnio(fecha: string): string {
  if (!fecha) return '';
  const iso = fecha.match(/(\d{4})-\d{2}-\d{2}/);
  if (iso) return iso[1];
  const esp = fecha.match(/de\s+(\d{4})/i);
  if (esp) return esp[1];
  return '';
}

function validarFecha(fecha: string): string {
  if (!fecha) return '';
  const m = fecha.match(/(\d{4})/);
  if (!m) return '';
  const anio = parseInt(m[1]);
  if (anio < 1990 || anio > 2030) return '';
  return fecha.trim();
}

function extraerPonente(texto: string): string {
  const patrones = [
    /(?:Ponencia\s+de\s+la\s+Magistrad[ao]|Magistrad[ao]\s+Ponente|Ponencia\s+del\s+Magistrad[ao])\s+(?:Doctor(?:a)?|Dr(?:a)?)\.?\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñÜü.\s]{4,120}?)(?=\s+(?:En|en|La|la|El|el|Vistos|vistos|Visto|visto|Se|se|Por|por|Mediante|mediante|Consta|consta|Cumplida|cumplida)\s|[.,;:]|$)/,
    /Ponente[:\s]+(?:Doctor(?:a)?|Dr(?:a)?)\.?\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñÜü.\s]{4,120}?)(?=\s+(?:En|en|La|la|El|el|Vistos|vistos|Visto|visto|Se|se|Por|por|Mediante|mediante|Consta|consta|Cumplida|cumplida)\s|[.,;:]|$)/,
    /Magistrad[ao]\s+(?:Doctor(?:a)?|Dr(?:a)?)\.?\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñÜü.\s]{4,120}?)(?=\s+(?:En|en|Vistos|vistos|Se|se|Por|por|Mediante|mediante)\s|[.,;:]|$)/,
  ];

  for (const re of patrones) {
    const m = texto.match(re);
    if (!m) continue;

    const palabras = m[1].trim().split(/\s+/);
    const nombre: string[] = [];
    for (const p of palabras) {
      const limpia = p.replace(/[.,;:]+$/g, '');
      if (!limpia) break;
      if (STOPWORDS_PONENTE.has(limpia.toUpperCase())) break;
      if (!/^[A-ZÁÉÍÓÚÑ]/.test(limpia)) break;
      nombre.push(limpia);
      if (nombre.length >= 6) break;
    }

    if (nombre.length >= 2 && nombre.length <= 6) {
      return nombre.join(' ');
    }
  }
  return '';
}

function validarHtml(html: string): void {
  if (html.length < 500) {
    throw new Error(`HTML demasiado corto: ${html.length} bytes`);
  }
  const lower = html.toLowerCase();
  const marcadores = [
    'error 1015',
    'you are being rate limited',
    'has banned you temporarily',
    'access denied',
    'enable cookies',
  ];
  for (const m of marcadores) {
    if (lower.includes(m)) {
      throw new Error(`HTML contiene marcador de error: ${m}`);
    }
  }
  if (!lower.includes('tribunal') && !lower.includes('tsj')) {
    throw new Error('HTML no parece ser una decisión del TSJ');
  }
}

async function procesarHtml(
  url: string,
  b64Html: string,
  sala: string,
  tipo: string,
  fechaScraper: string | undefined,
  env: any
) {
  const id = idDe(url);

  const binaryString = atob(b64Html);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }

  const sha = hex(await crypto.subtle.digest('SHA-256', bytes));

  let html = new TextDecoder('utf-8').decode(bytes);

  validarHtml(html);

  for (const patron of PATRONES_LOGO) {
    html = html.replace(patron, LOGO_URL_CORRECTO);
  }

  html = html.replace(
    /<meta[^>]*charset\s*=\s*["']?(windows-1252|iso-8859-1|latin-?1|ISO-8859-1)["']?[^>]*>/gi,
    '<meta charset="utf-8">'
  );
  html = html.replace(
    /<meta[^>]*http-equiv\s*=\s*["']?Content-Type["']?[^>]*content\s*=\s*["']?[^"']*charset=[^"'\s;]+[^>]*>/gi,
    '<meta charset="utf-8">'
  );

  const htmlBytesCorregidos = new TextEncoder().encode(html);

  const textoPlano = html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;?/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();

  const g = (re: RegExp) => limpiar(html.match(re)?.[1] ?? '');

  let fecha = validarFecha(fechaScraper?.trim() || '');
  if (!fecha) {
    fecha = validarFecha(g(/(\d{1,2}\s+de\s+\w+\s+de\s+\d{4})/i));
  }

  const anio = extraerAnio(fecha) || 'sin-anio';
  const num = id.match(/^\d+-(\d+)-/)?.[1] || g(/Sentencia\s*N[.º°]?\s*(\d+)/i);
  const exp = g(/alfanum[eé]rico\s+(A[A-Z]\d+-[A-Z]-\d{4}-\d+)/i) || g(/(A[A-Z]\d+-[A-Z]-\d{4}-\d+)/i);
  const ponente = extraerPonente(textoPlano);

  const htmlKey = `html/${sala}/${anio}/${id}.html`;
  const txtKey  = `txt/${sala}/${anio}/${id}.txt`;

  await env.BUCKET.put(htmlKey, htmlBytesCorregidos, {
    httpMetadata: { contentType: 'text/html; charset=utf-8' }
  });
  await env.BUCKET.put(txtKey, new TextEncoder().encode(textoPlano), {
    httpMetadata: { contentType: 'text/plain; charset=utf-8' }
  });

  // IMPORTANTE: no enviamos 'ponente' ni 'analyzed_at'.
  // - 'ponente' lo pone SOLO la IA (con el texto completo, más preciso).
  // - 'analyzed_at' no se resetea si ya estaba analizado.
  const supaResp = await fetch(`${env.SUPABASE_URL}/rest/v1/sentencias`, {
    method: 'POST',
    headers: {
      apikey: env.SUPABASE_SERVICE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_KEY}`,
      'Content-Type': 'application/json',
      Prefer: 'resolution=merge-duplicates'
    },
    body: JSON.stringify({
      id,
      sala,
      tipo: tipo || 'sentencia',
      anio,
      fecha,
      num_sentencia: num,
      expediente: exp,
      es_jurisprudencia: false, // booleano nunca null; lo pone true SOLO el scraper de jurisprudencias
      r2_html_key: htmlKey,
      r2_txt_key: txtKey,
      url_tsj: url,
    })
  });

  if (!supaResp.ok) {
    const err = await supaResp.text();
    throw new Error(`Supabase ${supaResp.status}: ${err.slice(0, 300)}`);
  }

  await env.DB.prepare(
    `INSERT INTO inventario_v2 (url, sala, anio, sha256, status, ingest_at, parser_v)
     VALUES (?, ?, ?, ?, 'ingested', datetime('now'), ?)
     ON CONFLICT(url) DO UPDATE SET
       sha256 = excluded.sha256,
       status = 'ingested',
       ingest_at = datetime('now'),
       parser_v = excluded.parser_v,
       last_error = NULL`
  ).bind(url, sala, anio, sha, env.PARSER_V).run();

  return { ok: true, id, anio, ponente, fecha, tiene_ponente: !!ponente };
}

export default {
  async fetch(req: Request, env: any): Promise<Response> {
    const url = new URL(req.url);

    if (req.method === 'POST' && url.pathname === '/ingest') {
      if (req.headers.get('x-token') !== env.INGEST_TOKEN) {
        return new Response('forbidden', { status: 403 });
      }
      try {
        const body = await req.json() as {
          url: string;
          html_b64: string;
          sala: string;
          tipo?: string;
          fecha?: string;
        };
        const r = await procesarHtml(
          body.url,
          body.html_b64,
          body.sala,
          body.tipo || 'sentencia',
          body.fecha,
          env
        );
        return Response.json(r);
      } catch (e: any) {
        return Response.json({ ok: false, error: e.message }, { status: 500 });
      }
    }

    if (url.pathname === '/pendientes') {
      const d = await env.DB.prepare(
        `SELECT status, COUNT(*) n FROM inventario_v2 GROUP BY 1`
      ).all();
      return Response.json(d.results);
    }

    // /estado?sala=scp&anio=2024 — resumen por sala/año para verificación
    if (url.pathname === '/estado') {
      if (req.headers.get('x-token') !== env.INGEST_TOKEN) {
        return new Response('forbidden', { status: 403 });
      }
      const sala = url.searchParams.get('sala');
      const anio = url.searchParams.get('anio');
      if (!sala || !anio) {
        return Response.json({ ok: false, error: 'faltan sala y anio' }, { status: 400 });
      }
      const d = await env.DB.prepare(
        `SELECT sala, anio, COUNT(*) total, SUM(status='ingested') ingested,
                SUM(status='quarantine') quarantena
         FROM inventario_v2 WHERE sala = ? AND anio = ? GROUP BY sala, anio`
      ).bind(sala, anio).all();
      const fila = (d.results as any[])[0] ?? null;
      return Response.json({ ok: true, sala, anio, ...fila });
    }

    return Response.json({
      servicio: 'penalex-scraper',
      version: '6.4',
      parser_v: env.PARSER_V,
      logo_reemplazado: LOGO_URL_CORRECTO
    });
  },
};
