// penalex-leer: worker para la cuenta mariolizcanoabg
// - Lee sentencias (html/txt) del R2 de la CUENTA VIEJA vía su dominio público
//   (https://pub-ccca0a4f4395464da7752af871c16943.r2.dev) — sin migrar nada.
// - /ingest compatible: escribe al R2 viejo vía API Cloudflare (secrets de la
//   cuenta vieja) + inserta en Supabase. D1 es opcional (auditoría).
const R2_PUBLIC = 'https://pub-ccca0a4f4395464da7752af871c16943.r2.dev';

const idDe = (u: string) => u.split('/').pop()!
  .replace(/\.(HTM|HTML|htm|html|txt)$/i, '')
  .replace(/[\s.]+$/g, '');

async function escribirR2Viejo(env: any, key: string, body: Uint8Array, contentType: string) {
  // La cuenta nueva NO tiene el bucket; escribimos al R2 viejo vía su API
  // usando la Global Key de la cuenta vieja (secrets).
  if (!env.CF_R2_ACCOUNT_ID || !env.CF_R2_API_KEY || !env.CF_R2_EMAIL) {
    throw new Error('faltan secrets CF_R2_* para escribir al R2 de la cuenta vieja');
  }
  const url = `https://api.cloudflare.com/client/v4/accounts/${env.CF_R2_ACCOUNT_ID}/r2/buckets/penalex-ve-v2/objects/${key}`;
  const resp = await fetch(url, {
    method: 'PUT',
    headers: {
      'X-Auth-Key': env.CF_R2_API_KEY,
      'X-Auth-Email': env.CF_R2_EMAIL,
      'Content-Type': contentType,
    },
    body,
  });
  if (!resp.ok) {
    throw new Error(`R2 viejo PUT ${resp.status}: ${(await resp.text()).slice(0, 200)}`);
  }
}

async function leerR2Publico(key: string): Promise<Response> {
  const resp = await fetch(`${R2_PUBLIC}/${key}`);
  if (!resp.ok) return new Response(`no encontrada (${resp.status})`, { status: 404 });
  const contentType = key.startsWith('txt/')
    ? 'text/plain; charset=utf-8'
    : 'text/html; charset=utf-8';
  return new Response(resp.body, {
    headers: { 'Content-Type': contentType, 'X-R2-Key': key },
  });
}

export default {
  async fetch(req: Request, env: any): Promise<Response> {
    const url = new URL(req.url);
    const partes = url.pathname.split('/').filter(Boolean);

    // GET /sentencia/{sala}/{anio}/{id}  → html (default) o .txt si termina en .txt
    if (req.method === 'GET' && partes[0] === 'sentencia' && partes.length >= 4) {
      const [sala, anio, ...resto] = partes.slice(1);
      const nombre = resto.join('/');
      const esTxt = /\.txt$/i.test(nombre);
      const id = idDe(nombre);
      const key = `${esTxt ? 'txt' : 'html'}/${sala}/${anio}/${id}.${esTxt ? 'txt' : 'html'}`;
      return leerR2Publico(key);
    }

    // GET /r2?key=html/scp/2024/xxx.html — acceso genérico a cualquier objeto
    if (req.method === 'GET' && partes[0] === 'r2') {
      const key = url.searchParams.get('key');
      if (!key) return Response.json({ ok: false, error: 'falta key' }, { status: 400 });
      return leerR2Publico(key);
    }

    if (req.method === 'POST' && url.pathname === '/ingest') {
      if (req.headers.get('x-token') !== env.INGEST_TOKEN) {
        return new Response('forbidden', { status: 403 });
      }
      try {
        const body = await req.json() as {
          url: string; html_b64: string; sala: string;
          tipo?: string; fecha?: string;
        };
        const id = idDe(body.url);
        const binaryString = atob(body.html_b64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
        if (bytes.length < 500) throw new Error(`HTML demasiado corto: ${bytes.length} bytes`);

        const html = new TextDecoder('utf-8').decode(bytes);
        const textoPlano = html
          .replace(/<script[\s\S]*?<\/script>/gi, ' ')
          .replace(/<style[\s\S]*?<\/style>/gi, ' ')
          .replace(/<[^>]+>/g, ' ')
          .replace(/&nbsp;?/g, ' ')
          .replace(/\s+/g, ' ')
          .trim();
        const anio = (body.fecha || '').match(/(\d{4})/)?.[1] || 'sin-anio';
        const htmlKey = `html/${body.sala}/${anio}/${id}.html`;
        const txtKey = `txt/${body.sala}/${anio}/${id}.txt`;

        // Escribir al R2 de la cuenta vieja (compatible con el bucket activo)
        await escribirR2Viejo(env, htmlKey, bytes, 'text/html; charset=utf-8');
        await escribirR2Viejo(env, txtKey, new TextEncoder().encode(textoPlano), 'text/plain; charset=utf-8');

        // Supabase (fuente de verdad)
        const supaResp = await fetch(`${env.SUPABASE_URL}/rest/v1/sentencias`, {
          method: 'POST',
          headers: {
            apikey: env.SUPABASE_SERVICE_KEY,
            Authorization: `Bearer ${env.SUPABASE_SERVICE_KEY}`,
            'Content-Type': 'application/json',
            Prefer: 'resolution=merge-duplicates',
          },
          body: JSON.stringify({
            id, sala: body.sala, tipo: body.tipo || 'sentencia', anio,
            fecha: body.fecha || '', num_sentencia: id.split('-')[1] || null,
            es_jurisprudencia: false,
            r2_html_key: htmlKey, r2_txt_key: txtKey, url_tsj: body.url,
          }),
        });
        if (!supaResp.ok) {
          throw new Error(`Supabase ${supaResp.status}: ${(await supaResp.text()).slice(0, 300)}`);
        }

        // Auditoría D1: opcional (solo si la cuenta tiene la DB)
        if (env.DB) {
          await env.DB.prepare(
            `INSERT INTO inventario_v2 (url, sala, anio, sha256, status, ingest_at, parser_v)
             VALUES (?, ?, ?, ?, 'ingested', datetime('now'), ?)
             ON CONFLICT(url) DO UPDATE SET status='ingested', ingest_at=datetime('now')`
          ).bind(body.url, body.sala, anio, '', env.PARSER_V || '6.1').run();
        }

        return Response.json({ ok: true, id, anio });
      } catch (e: any) {
        return Response.json({ ok: false, error: e.message }, { status: 500 });
      }
    }

    if (url.pathname === '/pendientes') {
      if (!env.DB) return Response.json({ ok: false, error: 'sin D1 en esta cuenta' });
      const d = await env.DB.prepare(`SELECT status, COUNT(*) n FROM inventario_v2 GROUP BY 1`).all();
      return Response.json(d.results);
    }

    return Response.json({
      servicio: 'penalex-leer',
      version: '1.0',
      r2_origen: R2_PUBLIC,
      lee: '/sentencia/{sala}/{anio}/{id}[.txt] | /r2?key=...',
      ingest: '/ingest (compatible, escribe al R2 de la cuenta vieja)',
    });
  },
};