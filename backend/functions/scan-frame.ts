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

// Parse the requested minerals from the session (jsonb may arrive as array or
// string), falling back to the legacy single target_mineral.
function parseMinerals(session: any): string[] {
  let list = session.target_minerals;
  if (typeof list === "string") {
    try {
      list = JSON.parse(list);
    } catch {
      list = null;
    }
  }
  if (Array.isArray(list) && list.length) {
    return list.map((m: any) => String(m).toLowerCase()).filter((m: string) => m.length);
  }
  return session.target_mineral ? [String(session.target_mineral).toLowerCase()] : [];
}

// Merge fresh per-mineral confidences into the running map, keeping the max.
function mergeConfidence(
  existing: any,
  fresh: Record<string, number>,
): Record<string, number> {
  let cur = existing;
  if (typeof cur === "string") {
    try {
      cur = JSON.parse(cur);
    } catch {
      cur = {};
    }
  }
  if (!cur || typeof cur !== "object") cur = {};
  const out: Record<string, number> = { ...cur };
  for (const [k, v] of Object.entries(fresh)) {
    out[k] = Math.max(Number(out[k]) || 0, v);
  }
  return out;
}

async function analyzeFrame(
  ctx: any,
  minerals: string[],
  imageBase64: string,
): Promise<{
  per_mineral: Record<string, number>;
  best_confidence: number;
  matched_mineral: string | null;
  rock_description: string;
}> {
  const { BUTTERBASE_APP_ID, BUTTERBASE_API_URL, BUTTERBASE_API_KEY } = ctx.env;
  const hints = Object.entries(DEMO_ROCK_HINTS)
    .map(([k, v]) => `- ${k}: ${v}`)
    .join("\n");
  const mineralList = minerals.join(", ");

  const systemPrompt = `You are a geologist assistant for a rock-scanning demo.
The user is searching for these target minerals/rock types: ${mineralList}.
Look at the SINGLE main (largest, most central) rock in the image and rate, for EACH
target mineral, how likely that rock contains or is plausibly associated with it.
Demo rocks may include:
${hints}

Reply with JSON only (no markdown):
{
  "per_mineral": { ${minerals.map((m) => `"${m}": 0.0-1.0`).join(", ")} },
  "best_rock_description": "brief description of the main rock"
}
Each confidence is the probability that target mineral is present/associated with the main rock.
If no rock is visible, set every confidence to 0.0.`;

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
                text: `Score the main rock for these minerals: ${mineralList}`,
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

  const perRaw =
    parsed.per_mineral && typeof parsed.per_mineral === "object" ? parsed.per_mineral : {};
  const per_mineral: Record<string, number> = {};
  for (const m of minerals) {
    const v = Number(perRaw[m]);
    per_mineral[m] = Math.max(0, Math.min(1, Number.isFinite(v) ? v : 0));
  }

  // argmax → matched mineral (ANY/OR semantics)
  let matched_mineral: string | null = null;
  let best_confidence = -1;
  for (const m of minerals) {
    if (per_mineral[m] > best_confidence) {
      best_confidence = per_mineral[m];
      matched_mineral = m;
    }
  }
  best_confidence = Math.max(0, best_confidence);

  return {
    per_mineral,
    best_confidence,
    matched_mineral,
    rock_description: String(parsed.best_rock_description || "Unknown rock"),
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

  let body: {
    session_id?: string;
    phase?: string;
    image_base64?: string;
    preview_object_id?: string;
  };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const { session_id, phase = "scan", image_base64, preview_object_id } = body;
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
    const isConfirm = phase === "confirm1" || phase === "confirm2";
    // Scan phase scores ALL requested minerals; confirm phases re-score only the
    // already-matched mineral.
    const allMinerals = parseMinerals(session);
    const scoreMinerals =
      isConfirm && session.matched_mineral
        ? [String(session.matched_mineral).toLowerCase()]
        : allMinerals;

    const analysis = await analyzeFrame(ctx, scoreMinerals, image_base64);
    const threshold = stopThreshold(ctx);
    const action = analysis.best_confidence > threshold ? "stop" : "continue";
    const mergedConf = mergeConfidence(session.per_mineral_confidence, analysis.per_mineral);

    if (phase === "scan") {
      const newMax = Math.max(session.max_confidence || 0, analysis.best_confidence);
      await ctx.db.query(
        `UPDATE scan_sessions
         SET frame_count = frame_count + 1,
             max_confidence = $2,
             rock_description = COALESCE($3, rock_description),
             status = CASE WHEN $4 = 'stop' THEN 'confirming' ELSE status END,
             latest_preview_object_id = COALESCE($5, latest_preview_object_id),
             per_mineral_confidence = $6,
             matched_mineral = CASE WHEN $4 = 'stop' THEN $7 ELSE matched_mineral END,
             updated_at = now()
         WHERE id = $1`,
        [
          session_id,
          newMax,
          analysis.rock_description,
          action,
          preview_object_id ?? null,
          JSON.stringify(mergedConf),
          analysis.matched_mineral,
        ],
      );

      return new Response(
        JSON.stringify({
          action,
          best_confidence: analysis.best_confidence,
          max_confidence: newMax,
          matched_mineral: action === "stop" ? analysis.matched_mineral : null,
          per_mineral: analysis.per_mineral,
          per_mineral_confidence: mergedConf,
          rock_description: analysis.rock_description,
          stop_threshold: threshold,
        }),
        { status: 200, headers: { "Content-Type": "application/json", ...cors } },
      );
    }

    if (isConfirm) {
      const col = phase === "confirm1" ? "confirm_confidence_1" : "confirm_confidence_2";
      await ctx.db.query(
        `UPDATE scan_sessions
         SET ${col} = $2,
             rock_description = COALESCE($3, rock_description),
             status = 'confirming',
             latest_preview_object_id = COALESCE($4, latest_preview_object_id),
             per_mineral_confidence = $5,
             updated_at = now()
         WHERE id = $1`,
        [
          session_id,
          analysis.best_confidence,
          analysis.rock_description,
          preview_object_id ?? null,
          JSON.stringify(mergedConf),
        ],
      );

      return new Response(
        JSON.stringify({
          phase,
          best_confidence: analysis.best_confidence,
          matched_mineral: session.matched_mineral ?? analysis.matched_mineral,
          per_mineral: analysis.per_mineral,
          rock_description: analysis.rock_description,
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
