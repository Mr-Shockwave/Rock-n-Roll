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

  const result = await ctx.db.query(
    `UPDATE scan_sessions
     SET status = 'scanning', updated_at = now()
     WHERE id = (
       SELECT id FROM scan_sessions
       WHERE status = 'queued'
       ORDER BY created_at ASC
       LIMIT 1
       FOR UPDATE SKIP LOCKED
     )
     RETURNING id, target_mineral, target_minerals, status, created_at`,
  );

  if (!result.rows.length) {
    return new Response(JSON.stringify({ session: null }), {
      status: 200,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const row = result.rows[0];
  return new Response(
    JSON.stringify({
      session: {
        session_id: row.id,
        target_mineral: row.target_mineral,
        target_minerals: row.target_minerals,
        status: row.status,
        created_at: row.created_at,
      },
    }),
    { status: 200, headers: { "Content-Type": "application/json", ...cors } },
  );
}
