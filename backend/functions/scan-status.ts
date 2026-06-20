// Mint a fresh presigned download URL for a stored object id. Presigned URLs
// expire (~1h), so we generate a new one on every status poll — the browser
// always gets a working <img src>. Uses the function's API key (platform auth
// can read any object), so the frontend never needs credentials.
async function downloadUrl(ctx: any, objectId: string | null): Promise<string | null> {
  if (!objectId) return null;
  const { BUTTERBASE_APP_ID, BUTTERBASE_API_URL, BUTTERBASE_API_KEY } = ctx.env;
  try {
    const r = await fetch(
      `${BUTTERBASE_API_URL}/storage/${BUTTERBASE_APP_ID}/download/${objectId}`,
      { headers: { Authorization: `Bearer ${BUTTERBASE_API_KEY}` } },
    );
    if (!r.ok) return null;
    const j = await r.json();
    return j.downloadUrl || j.download_url || null;
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

  // Mint fresh presigned image URLs (preview updates live; overlay/final on complete).
  const [previewUrl, overlayUrl, finalFrameUrl] = await Promise.all([
    downloadUrl(ctx, row.latest_preview_object_id),
    downloadUrl(ctx, row.overlay_object_id),
    downloadUrl(ctx, row.final_frame_object_id),
  ]);

  return new Response(
    JSON.stringify({
      session_id: row.id,
      target_mineral: row.target_mineral,
      target_minerals: row.target_minerals,
      matched_mineral: row.matched_mineral,
      per_mineral_confidence: row.per_mineral_confidence,
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
      preview_image_url: previewUrl,
      overlay_image_url: overlayUrl,
      final_frame_image_url: finalFrameUrl,
      created_at: row.created_at,
      updated_at: row.updated_at,
    }),
    { status: 200, headers: { "Content-Type": "application/json", ...cors } },
  );
}
