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

  let body: { target_mineral?: string; target_minerals?: string[] };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  // Accept target_minerals: string[] OR the legacy target_mineral: string.
  // Normalize: trim, lowercase, drop empties, dedupe, cap at 5.
  const raw = Array.isArray(body.target_minerals)
    ? body.target_minerals
    : [body.target_mineral];
  const minerals = Array.from(
    new Set(
      raw
        .map((m) => (typeof m === "string" ? m.trim().toLowerCase() : ""))
        .filter((m) => m.length > 0),
    ),
  ).slice(0, 5);

  if (minerals.length === 0) {
    return new Response(
      JSON.stringify({ error: "target_minerals (or target_mineral) is required" }),
      { status: 400, headers: { "Content-Type": "application/json", ...cors } },
    );
  }

  const result = await ctx.db.query(
    `INSERT INTO scan_sessions (target_mineral, target_minerals, status)
     VALUES ($1, $2, 'queued')
     RETURNING id, status, target_mineral, target_minerals, created_at`,
    [minerals[0], JSON.stringify(minerals)],
  );
  const row = result.rows[0];

  return new Response(
    JSON.stringify({
      session_id: row.id,
      status: row.status,
      target_mineral: row.target_mineral,
      target_minerals: row.target_minerals,
      created_at: row.created_at,
    }),
    { status: 200, headers: { "Content-Type": "application/json", ...cors } },
  );
}
