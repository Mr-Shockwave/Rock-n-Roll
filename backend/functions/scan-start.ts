export default async function handler(req: Request, ctx: any): Promise<Response> {
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
  };

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: cors });
  }
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  let body: { target_mineral?: string };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const target = (body.target_mineral || "").trim();
  if (!target) {
    return new Response(JSON.stringify({ error: "target_mineral is required" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const result = await ctx.db.query(
    `INSERT INTO scan_sessions (target_mineral, status)
     VALUES ($1, 'queued')
     RETURNING id, status, target_mineral, created_at`,
    [target],
  );
  const row = result.rows[0];

  return new Response(
    JSON.stringify({
      session_id: row.id,
      status: row.status,
      target_mineral: row.target_mineral,
      created_at: row.created_at,
    }),
    { status: 200, headers: { "Content-Type": "application/json", ...cors } },
  );
}
