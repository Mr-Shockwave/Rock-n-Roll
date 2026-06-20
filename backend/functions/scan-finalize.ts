function analysisMin(ctx: any): number {
  const n = parseFloat(ctx.env.CONFIDENCE_ANALYSIS_MIN || "0.5");
  return Number.isFinite(n) ? n : 0.5;
}

function analysisMax(ctx: any): number {
  const n = parseFloat(ctx.env.CONFIDENCE_ANALYSIS_MAX || "0.7");
  return Number.isFinite(n) ? n : 0.7;
}

function visionModel(ctx: any): string {
  return ctx.env.BUTTERBASE_VISION_MODEL || "anthropic/claude-haiku-4.5";
}

async function movementGuidance(
  ctx: any,
  targetMineral: string,
  rockDescription: string,
  distanceCm: number,
  angleDeg: number,
): Promise<string> {
  const { BUTTERBASE_APP_ID, BUTTERBASE_API_URL, BUTTERBASE_API_KEY } = ctx.env;

  const aiResp = await fetch(
    `${BUTTERBASE_API_URL}/v1/${BUTTERBASE_APP_ID}/chat/completions`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${BUTTERBASE_API_KEY}`,
      },
      body: JSON.stringify({
        model: visionModel(ctx),
        max_tokens: 250,
        temperature: 0.4,
        messages: [
          {
            role: "system",
            content:
              "You guide a robot arm approaching a rock, but the demo operator walks on foot. " +
              "Give 2-3 short sentences: direction to move, how far (~cm), and body orientation using the approach angle. " +
              "Be practical and concise. Do not mention that you are an AI.",
          },
          {
            role: "user",
            content:
              `Target mineral search: ${targetMineral}\n` +
              `Rock: ${rockDescription}\n` +
              `Estimated distance: ${distanceCm} cm\n` +
              `Approach angle: ${angleDeg} degrees (rotation of rock in image plane)\n` +
              `How should the operator walk forward to inspect this rock?`,
          },
        ],
      }),
    },
  );

  if (!aiResp.ok) {
    return `Move forward about ${distanceCm} cm toward the rock, keeping it centered in view. Adjust your approach to match a ${angleDeg}° orientation relative to the rock's long axis.`;
  }

  const aiJson = await aiResp.json();
  return (aiJson?.choices?.[0]?.message?.content || "").trim();
}

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

  let body: {
    session_id?: string;
    distance_cm?: number;
    angle_deg?: number;
    confirm_confidence_1?: number;
    confirm_confidence_2?: number;
    overlay_object_id?: string;
    final_frame_object_id?: string;
  };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const {
    session_id,
    distance_cm,
    angle_deg,
    confirm_confidence_1,
    confirm_confidence_2,
    overlay_object_id,
    final_frame_object_id,
  } = body;
  if (!session_id) {
    return new Response(JSON.stringify({ error: "session_id required" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const sessionRes = await ctx.db.query(`SELECT * FROM scan_sessions WHERE id = $1`, [session_id]);
  if (!sessionRes.rows.length) {
    return new Response(JSON.stringify({ error: "Session not found" }), {
      status: 404,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }
  const session = sessionRes.rows[0];

  const c1 = confirm_confidence_1 ?? session.confirm_confidence_1 ?? 0;
  const c2 = confirm_confidence_2 ?? session.confirm_confidence_2 ?? 0;
  const avgConfidence = (Number(c1) + Number(c2)) / 2;
  const minC = analysisMin(ctx);
  const maxC = analysisMax(ctx);

  // Show "needs further analysis" when averaged post-stop confidence is at least minC.
  // maxC documents the relaxed upper band (demo criteria are intentionally not strict).
  const needsAnalysis = avgConfidence >= minC;
  let resultMessage = "No promising rocks found.";
  let movement = "";

  const matchedMineral = session.matched_mineral || session.target_mineral;
  if (needsAnalysis) {
    resultMessage = "This rock needs further analysis.";
    movement = await movementGuidance(
      ctx,
      matchedMineral,
      session.rock_description || "detected rock",
      Number(distance_cm ?? -1),
      Number(angle_deg ?? 0),
    );
  }

  await ctx.db.query(
    `UPDATE scan_sessions
     SET status = 'complete',
         avg_confidence = $2,
         distance_cm = $3,
         angle_deg = $4,
         needs_analysis = $5,
         result_message = $6,
         movement_guidance = $7,
         confirm_confidence_1 = COALESCE($8, confirm_confidence_1),
         confirm_confidence_2 = COALESCE($9, confirm_confidence_2),
         overlay_object_id = COALESCE($10, overlay_object_id),
         final_frame_object_id = COALESCE($11, final_frame_object_id),
         updated_at = now()
     WHERE id = $1`,
    [
      session_id,
      avgConfidence,
      distance_cm ?? null,
      angle_deg ?? null,
      needsAnalysis,
      resultMessage,
      movement || null,
      confirm_confidence_1 ?? null,
      confirm_confidence_2 ?? null,
      overlay_object_id ?? null,
      final_frame_object_id ?? null,
    ],
  );

  return new Response(
    JSON.stringify({
      session_id,
      status: "complete",
      avg_confidence: avgConfidence,
      needs_analysis: needsAnalysis,
      result_message: resultMessage,
      matched_mineral: matchedMineral ?? null,
      per_mineral_confidence: session.per_mineral_confidence ?? null,
      distance_cm: distance_cm ?? null,
      angle_deg: angle_deg ?? null,
      movement_guidance: movement,
      analysis_confidence_range: [minC, maxC],
    }),
    { status: 200, headers: { "Content-Type": "application/json", ...cors } },
  );
}
