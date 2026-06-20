const DEMO_ROCK_HINTS: Record<string, string> = {
  quartz: "Glassy, translucent to white; may show crystal faces or conchoidal fracture.",
  pumice: "Light, vesicular (holey) volcanic rock; floats on water; pale gray.",
  granite: "Coarse speckled igneous rock; visible grains of quartz (clear), feldspar (pink/white), mica (dark flakes).",
  sandstone: "Sandy, granular sedimentary rock; tan, red, or buff; visible sand grains.",
};

function stopThreshold(ctx: any): number {
  const raw = ctx.env.CONFIDENCE_STOP_THRESHOLD || "0.5";
  const n = parseFloat(raw);
  return Number.isFinite(n) ? n : 0.5;
}

function visionModel(ctx: any): string {
  return ctx.env.BUTTERBASE_VISION_MODEL || "anthropic/claude-haiku-4.5";
}

async function analyzeFrame(
  ctx: any,
  targetMineral: string,
  imageBase64: string,
): Promise<{
  best_confidence: number;
  rock_description: string;
  rocks: Array<{ description: string; confidence: number }>;
}> {
  const { BUTTERBASE_APP_ID, BUTTERBASE_API_URL, BUTTERBASE_API_KEY } = ctx.env;
  const hints = Object.entries(DEMO_ROCK_HINTS)
    .map(([k, v]) => `- ${k}: ${v}`)
    .join("\n");

  const systemPrompt = `You are a geologist assistant for a rock-scanning demo.
The user is searching for a target mineral/rock type: "${targetMineral}".
Rate how likely visible rocks in the image contain or are associated with that target.
Demo rocks in the scene may include:
${hints}

Reply with JSON only (no markdown):
{
  "rocks": [{"description": "brief rock description", "confidence": 0.0-1.0}],
  "best_confidence": 0.0-1.0,
  "best_rock_description": "description of highest-confidence rock"
}
Use confidence as probability the target mineral is present or plausibly associated with the rock.
If no rocks visible, return best_confidence 0.0.`;

  const dataUri = imageBase64.startsWith("data:")
    ? imageBase64
    : `data:image/jpeg;base64,${imageBase64}`;

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
        max_tokens: 400,
        temperature: 0.2,
        messages: [
          { role: "system", content: systemPrompt },
          {
            role: "user",
            content: [
              {
                type: "text",
                text: `Analyze this image for target mineral/rock: ${targetMineral}`,
              },
              { type: "image_url", image_url: { url: dataUri, detail: "low" } },
            ],
          },
        ],
      }),
    },
  );

  if (!aiResp.ok) {
    const errText = await aiResp.text();
    throw new Error(`AI gateway error ${aiResp.status}: ${errText.slice(0, 200)}`);
  }

  const aiJson = await aiResp.json();
  const content = aiJson?.choices?.[0]?.message?.content || "{}";
  const jsonMatch = content.match(/\{[\s\S]*\}/);
  const parsed = JSON.parse(jsonMatch ? jsonMatch[0] : content);

  const rocks = Array.isArray(parsed.rocks) ? parsed.rocks : [];
  let best = typeof parsed.best_confidence === "number" ? parsed.best_confidence : 0;
  if (!best && rocks.length) {
    best = Math.max(...rocks.map((r: any) => Number(r.confidence) || 0));
  }
  const desc =
    parsed.best_rock_description ||
    rocks.sort((a: any, b: any) => (b.confidence || 0) - (a.confidence || 0))[0]?.description ||
    "Unknown rock";

  return {
    best_confidence: Math.max(0, Math.min(1, best)),
    rock_description: desc,
    rocks: rocks.map((r: any) => ({
      description: String(r.description || ""),
      confidence: Math.max(0, Math.min(1, Number(r.confidence) || 0)),
    })),
  };
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

  let body: { session_id?: string; phase?: string; image_base64?: string };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const { session_id, phase = "scan", image_base64 } = body;
  if (!session_id || !image_base64) {
    return new Response(JSON.stringify({ error: "session_id and image_base64 required" }), {
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

  try {
    const analysis = await analyzeFrame(ctx, session.target_mineral, image_base64);
    const threshold = stopThreshold(ctx);
    const action = analysis.best_confidence > threshold ? "stop" : "continue";

    if (phase === "scan") {
      const newMax = Math.max(session.max_confidence || 0, analysis.best_confidence);
      await ctx.db.query(
        `UPDATE scan_sessions
         SET frame_count = frame_count + 1,
             max_confidence = $2,
             rock_description = COALESCE($3, rock_description),
             status = CASE WHEN $4 = 'stop' THEN 'confirming' ELSE status END,
             updated_at = now()
         WHERE id = $1`,
        [session_id, newMax, analysis.rock_description, action],
      );

      return new Response(
        JSON.stringify({
          action,
          best_confidence: analysis.best_confidence,
          max_confidence: newMax,
          rock_description: analysis.rock_description,
          rocks: analysis.rocks,
          stop_threshold: threshold,
        }),
        { status: 200, headers: { "Content-Type": "application/json", ...cors } },
      );
    }

    if (phase === "confirm1" || phase === "confirm2") {
      const col = phase === "confirm1" ? "confirm_confidence_1" : "confirm_confidence_2";
      await ctx.db.query(
        `UPDATE scan_sessions
         SET ${col} = $2,
             rock_description = COALESCE($3, rock_description),
             status = 'confirming',
             updated_at = now()
         WHERE id = $1`,
        [session_id, analysis.best_confidence, analysis.rock_description],
      );

      return new Response(
        JSON.stringify({
          phase,
          best_confidence: analysis.best_confidence,
          rock_description: analysis.rock_description,
          rocks: analysis.rocks,
        }),
        { status: 200, headers: { "Content-Type": "application/json", ...cors } },
      );
    }

    return new Response(JSON.stringify({ error: `Unknown phase: ${phase}` }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  } catch (err: any) {
    await ctx.db.query(
      `UPDATE scan_sessions SET status = 'error', error = $2, updated_at = now() WHERE id = $1`,
      [session_id, String(err?.message || err)],
    );
    return new Response(JSON.stringify({ error: String(err?.message || err) }), {
      status: 500,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }
}
