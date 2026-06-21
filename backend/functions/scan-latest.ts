// Returns the most recent (non-abandoned) scan session so the frontend can
// auto-follow whatever scan is currently active — no session_id needed. Same
// field shape as scan-status (minus the per-id lookup). Fast SELECT only.
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

  const result = await ctx.db.query(
    `SELECT * FROM scan_sessions
     WHERE status <> 'abandoned'
     ORDER BY created_at DESC
     LIMIT 1`,
  );
  if (!result.rows.length) {
    return new Response(JSON.stringify({ session_id: null }), {
      status: 200,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const row = result.rows[0];
  let rankedRocks = [];
  try {
    rankedRocks = JSON.parse(row.ranked_rocks_json || "[]");
  } catch {
    rankedRocks = [];
  }

  return new Response(
    JSON.stringify({
      session_id: row.id,
      target_mineral: row.target_mineral,
      target_minerals: row.target_minerals,
      matched_mineral: row.matched_mineral,
      per_mineral_confidence: row.per_mineral_confidence,
      status: row.status,
      ui_phase: row.ui_phase,
      frame_count: row.frame_count,
      max_confidence: row.max_confidence,
      confirm_confidence_1: row.confirm_confidence_1,
      confirm_confidence_2: row.confirm_confidence_2,
      avg_confidence: row.avg_confidence,
      distance_cm: row.distance_cm,
      angle_deg: row.angle_deg,
      rock_description: row.rock_description,
      result_message: row.result_message,
      secondary_message: row.secondary_message,
      movement_guidance: row.movement_guidance,
      needs_analysis: row.needs_analysis,
      agent_panel_text: row.agent_panel_text,
      focus_rock_index: row.focus_rock_index,
      ranked_rocks: rankedRocks,
      latest_preview_object_id: row.latest_preview_object_id,
      overlay_object_id: row.overlay_object_id,
      final_frame_object_id: row.final_frame_object_id,
      error: row.error,
      created_at: row.created_at,
      updated_at: row.updated_at,
    }),
    { status: 200, headers: { "Content-Type": "application/json", ...cors } },
  );
}
