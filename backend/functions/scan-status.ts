function controlBase(ctx: any): string {
  const url = (ctx.env.BUTTERBASE_API_URL || "https://api.butterbase.ai").replace(/\/$/, "");
  if (url.includes("/v1/")) return url.split("/v1/")[0];
  return "https://api.butterbase.ai";
}

async function downloadUrl(ctx: any, objectId: string | null): Promise<string | null> {
  if (!objectId) return null;
  const appId = ctx.env.BUTTERBASE_APP_ID;
  const apiKey = ctx.env.BUTTERBASE_API_KEY;
  if (!appId || !apiKey) return null;
  try {
    const resp = await fetch(`${controlBase(ctx)}/storage/${appId}/download/${objectId}`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return data.downloadUrl || data.download_url || null;
  } catch {
    return null;
  }
}

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
  const previewId = row.latest_preview_object_id || row.final_frame_object_id;
  const cameraImageUrl = await downloadUrl(ctx, previewId);

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
      camera_image_url: cameraImageUrl,
      error: row.error,
      created_at: row.created_at,
      updated_at: row.updated_at,
    }),
    { status: 200, headers: { "Content-Type": "application/json", ...cors } },
  );
}
