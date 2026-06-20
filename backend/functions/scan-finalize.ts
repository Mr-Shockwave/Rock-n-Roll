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

function successHeadline(focusIndex: number): string {
  return `Rock ${focusIndex || 1} needs more analysis.`;
}

function finalAgentPanel(
  headline: string,
  distanceCm: number | null,
  angleDeg: number | null,
  guidance: string,
): string {
  return [
    headline,
    `Distance: ${distanceCm ?? "—"}`,
    `Angle: ${angleDeg ?? "—"}`,
    guidance,
  ]
    .filter(Boolean)
    .join("\n");
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
    geometry?: boolean;
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
    geometry = false,
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
  const focusIndex = Number(session.focus_rock_index) || 1;

  if (geometry) {
    if (session.status !== "pending_geometry") {
      return new Response(JSON.stringify({ error: "Session is not awaiting geometry" }), {
        status: 400,
        headers: { "Content-Type": "application/json", ...cors },
      });
    }

    const headline = successHeadline(focusIndex);
    const movement = await movementGuidance(
      ctx,
      session.target_mineral,
      session.rock_description || "detected rock",
      Number(distance_cm ?? -1),
      Number(angle_deg ?? 0),
    );
    const agentText = finalAgentPanel(
      headline,
      distance_cm ?? null,
      angle_deg ?? null,
      movement,
    );

    await ctx.db.query(
      `UPDATE scan_sessions
       SET status = 'complete',
           distance_cm = $2,
           angle_deg = $3,
           movement_guidance = $4,
           result_message = $5,
           agent_panel_text = $6,
           ui_phase = 'complete',
           secondary_message = NULL,
           updated_at = now()
       WHERE id = $1`,
      [session_id, distance_cm ?? null, angle_deg ?? null, movement || null, headline, agentText],
    );

    return new Response(
      JSON.stringify({
        session_id,
        status: "complete",
        needs_analysis: true,
        needs_geometry: false,
        result_message: headline,
        distance_cm: distance_cm ?? null,
        angle_deg: angle_deg ?? null,
        movement_guidance: movement,
        agent_panel_text: agentText,
        focus_rock_index: focusIndex,
        avg_confidence: session.avg_confidence,
      }),
      { status: 200, headers: { "Content-Type": "application/json", ...cors } },
    );
  }

  const c1 = confirm_confidence_1 ?? session.confirm_confidence_1 ?? 0;
  const c2 = confirm_confidence_2 ?? session.confirm_confidence_2 ?? 0;
  const avgConfidence = (Number(c1) + Number(c2)) / 2;
  const minC = analysisMin(ctx);
  const maxC = analysisMax(ctx);

  const needsAnalysis = avgConfidence >= minC;

  if (needsAnalysis) {
    const headline = successHeadline(focusIndex);
    await ctx.db.query(
      `UPDATE scan_sessions
       SET status = 'pending_geometry',
           avg_confidence = $2,
           needs_analysis = true,
           result_message = $3,
           agent_panel_text = $3,
           ui_phase = 'pending_geometry',
           confirm_confidence_1 = COALESCE($4, confirm_confidence_1),
           confirm_confidence_2 = COALESCE($5, confirm_confidence_2),
           secondary_message = NULL,
           updated_at = now()
       WHERE id = $1`,
      [
        session_id,
        avgConfidence,
        headline,
        confirm_confidence_1 ?? null,
        confirm_confidence_2 ?? null,
      ],
    );

    return new Response(
      JSON.stringify({
        session_id,
        status: "pending_geometry",
        avg_confidence: avgConfidence,
        needs_analysis: true,
        needs_geometry: true,
        result_message: headline,
        focus_rock_index: focusIndex,
        analysis_confidence_range: [minC, maxC],
      }),
      { status: 200, headers: { "Content-Type": "application/json", ...cors } },
    );
  }

  const mistakeHeadline = "This might be a mistake";
  const secondary = "Continue moving";
  const agentText = `${mistakeHeadline}\n${secondary}`;

  await ctx.db.query(
    `UPDATE scan_sessions
     SET status = 'scanning',
         avg_confidence = $2,
         needs_analysis = false,
         result_message = $3,
         secondary_message = $4,
         agent_panel_text = $5,
         ui_phase = 'mistake',
         confirm_confidence_1 = COALESCE($6, confirm_confidence_1),
         confirm_confidence_2 = COALESCE($7, confirm_confidence_2),
         updated_at = now()
     WHERE id = $1`,
    [
      session_id,
      avgConfidence,
      mistakeHeadline,
      secondary,
      agentText,
      confirm_confidence_1 ?? null,
      confirm_confidence_2 ?? null,
    ],
  );

  return new Response(
    JSON.stringify({
      session_id,
      status: "scanning",
      avg_confidence: avgConfidence,
      needs_analysis: false,
      needs_geometry: false,
      mistake_continue: true,
      result_message: mistakeHeadline,
      secondary_message: secondary,
      agent_panel_text: agentText,
      analysis_confidence_range: [minC, maxC],
    }),
    { status: 200, headers: { "Content-Type": "application/json", ...cors } },
  );
}
