export default async function handler(req: Request, ctx: any): Promise<Response> {
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
  };

  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: cors });
  }
  if (req.method !== "GET") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const url = new URL(req.url);
  const sessionId = url.searchParams.get("session_id");
  if (!sessionId) {
    return new Response(JSON.stringify({ error: "session_id query param required" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const result = await ctx.db.query(`SELECT * FROM scan_sessions WHERE id = $1`, [sessionId]);
  if (!result.rows.length) {
    return new Response(JSON.stringify({ error: "Session not found" }), {
      status: 404,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const row = result.rows[0];
  return new Response(
    JSON.stringify({
      session_id: row.id,
      target_mineral: row.target_mineral,
      status: row.status,
      frame_count: row.frame_count,
      max_confidence: row.max_confidence,
      confirm_confidence_1: row.confirm_confidence_1,
      confirm_confidence_2: row.confirm_confidence_2,
      avg_confidence: row.avg_confidence,
      distance_cm: row.distance_cm,
      angle_deg: row.angle_deg,
      rock_description: row.rock_description,
      result_message: row.result_message,
      movement_guidance: row.movement_guidance,
      needs_analysis: row.needs_analysis,
      error: row.error,
      created_at: row.created_at,
      updated_at: row.updated_at,
    }),
    { status: 200, headers: { "Content-Type": "application/json", ...cors } },
  );
}
