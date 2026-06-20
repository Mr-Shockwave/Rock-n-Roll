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

function analysisMin(ctx: any): number {
  const raw = ctx.env.CONFIDENCE_ANALYSIS_MIN || "0.5";
  const n = parseFloat(raw);
  return Number.isFinite(n) ? n : 0.5;
}

function visionModel(ctx: any): string {
  return ctx.env.BUTTERBASE_VISION_MODEL || "anthropic/claude-haiku-4.5";
}

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

type RankedRock = { index: number; description: string; confidence: number };

function rankMinerals(perMineral: Record<string, number>): RankedRock[] {
  return Object.entries(perMineral)
    .sort((a, b) => b[1] - a[1])
    .map(([name, confidence], i) => ({
      index: i + 1,
      description: name,
      confidence,
    }));
}

function fmtPct(c: number): string {
  return `${Math.round(c * 100)}%`;
}

function panelScan(ranked: RankedRock[]): string {
  if (!ranked.length) return "No minerals scored.";
  return [
    `${ranked.length} mineral(s) scored`,
    ...ranked.map((r) => `${r.description}: ${fmtPct(r.confidence)}`),
  ].join("\n");
}

function panelConfirm1(ranked: RankedRock[], minC: number): string {
  const qual = ranked.filter((r) => r.confidence >= minC);
  if (!qual.length) return `0 mineral(s) over threshold (${fmtPct(minC)})`;
  return [
    `${qual.length} mineral(s) over threshold`,
    ...qual.map((r) => `${r.description}: ${fmtPct(r.confidence)}`),
  ].join("\n");
}

function panelConfirm2(focusIndex: number, confidence: number, mineral: string): string {
  return `Rock ${focusIndex} (${mineral}): ${fmtPct(confidence)}`;
}

function focusFromSession(session: any): {
  index: number;
  description: string | null;
  mineral: string | null;
} {
  const index = Number(session.focus_rock_index) || 1;
  const mineral = session.matched_mineral || session.target_mineral || null;
  return { index, description: mineral ? String(mineral) : null, mineral };
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

async function analyzeFocusedRock(
  ctx: any,
  targetMineral: string,
  imageBase64: string,
  focusRockDescription: string,
): Promise<{
  best_confidence: number;
  rock_description: string;
}> {
  const { BUTTERBASE_APP_ID, BUTTERBASE_API_URL, BUTTERBASE_API_KEY } = ctx.env;

  const systemPrompt = `You are a geologist assistant for a rock-scanning demo.
The user is searching for target mineral/rock type: "${targetMineral}".
A prior frame identified this specific rock as the best candidate:
"${focusRockDescription}"

Analyze ONLY that rock in the current image. Ignore all other rocks.
Reply with JSON only (no markdown):
{
  "confidence": 0.0-1.0,
  "rock_description": "brief description confirming you are looking at the same rock"
}
Use confidence as probability the target mineral is present in THIS rock only.
If the focused rock is not visible, return confidence 0.0.`;

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
        max_tokens: 250,
        temperature: 0.2,
        messages: [
          { role: "system", content: systemPrompt },
          {
            role: "user",
            content: [
              {
                type: "text",
                text:
                  `Re-check ONLY this rock for target mineral/rock: ${targetMineral}\n` +
                  `Focused rock: ${focusRockDescription}`,
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
  const confidence = Math.max(0, Math.min(1, Number(parsed.confidence) || 0));

  return {
    best_confidence: confidence,
    rock_description: String(parsed.rock_description || focusRockDescription),
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
    focus_rock_description?: string;
    camera_object_id?: string;
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

  const {
    session_id,
    phase = "scan",
    image_base64,
    focus_rock_description,
    camera_object_id,
    preview_object_id,
  } = body;
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
  const previewId = camera_object_id || preview_object_id || null;

  try {
    if (phase === "confirm2") {
      const focus = focusFromSession(session);
      const matchedMineral = focus.mineral || parseMinerals(session)[0] || "unknown";
      const focusDescription =
        (focus_rock_description || session.rock_description || focus.description || "").trim();
      if (!focusDescription) {
        return new Response(
          JSON.stringify({ error: "focus_rock_description required for confirm2" }),
          { status: 400, headers: { "Content-Type": "application/json", ...cors } },
        );
      }

      const focused = await analyzeFocusedRock(
        ctx,
        matchedMineral,
        image_base64,
        focusDescription,
      );

      const agentText = panelConfirm2(focus.index, focused.best_confidence, matchedMineral);

      await ctx.db.query(
        `UPDATE scan_sessions
         SET confirm_confidence_2 = $2,
             rock_description = COALESCE($3, rock_description),
             status = 'confirming',
             ui_phase = 'confirm2',
             agent_panel_text = $4,
             latest_preview_object_id = COALESCE($5, latest_preview_object_id),
             final_frame_object_id = COALESCE($5, final_frame_object_id),
             updated_at = now()
         WHERE id = $1`,
        [
          session_id,
          focused.best_confidence,
          focused.rock_description,
          agentText,
          previewId,
        ],
      );

      return new Response(
        JSON.stringify({
          phase,
          best_confidence: focused.best_confidence,
          rock_description: focused.rock_description,
          focus_rock_description: focusDescription,
          focus_rock_index: focus.index,
          matched_mineral: matchedMineral,
          agent_panel_text: agentText,
        }),
        { status: 200, headers: { "Content-Type": "application/json", ...cors } },
      );
    }

    const isConfirm = phase === "confirm1";
    const allMinerals = parseMinerals(session);
    const scoreMinerals =
      isConfirm && session.matched_mineral
        ? [String(session.matched_mineral).toLowerCase()]
        : allMinerals;

    const analysis = await analyzeFrame(ctx, scoreMinerals, image_base64);
    const threshold = stopThreshold(ctx);
    const action = analysis.best_confidence > threshold ? "stop" : "continue";
    const mergedConf = mergeConfidence(session.per_mineral_confidence, analysis.per_mineral);
    const ranked = rankMinerals(isConfirm ? analysis.per_mineral : mergedConf);

    if (phase === "scan") {
      const newMax = Math.max(session.max_confidence || 0, analysis.best_confidence);
      const isStop = action === "stop";
      const scanRanked = rankMinerals(analysis.per_mineral);
      const agentText = isStop ? "Stop" : panelScan(scanRanked);
      const focusIndex = 1;
      const rankedJson = isStop ? JSON.stringify(scanRanked) : session.ranked_rocks_json;

      await ctx.db.query(
        `UPDATE scan_sessions
         SET frame_count = frame_count + 1,
             max_confidence = $2,
             rock_description = COALESCE($3, rock_description),
             status = CASE WHEN $4 = 'stop' THEN 'confirming' ELSE 'scanning' END,
             ui_phase = $5,
             agent_panel_text = $6,
             focus_rock_index = COALESCE($7, focus_rock_index),
             ranked_rocks_json = COALESCE($8, ranked_rocks_json),
             latest_preview_object_id = COALESCE($9, latest_preview_object_id),
             per_mineral_confidence = $10,
             matched_mineral = CASE WHEN $4 = 'stop' THEN $11 ELSE matched_mineral END,
             updated_at = now()
         WHERE id = $1`,
        [
          session_id,
          newMax,
          analysis.rock_description,
          action,
          isStop ? "stop" : "scan",
          agentText,
          focusIndex,
          rankedJson,
          previewId,
          JSON.stringify(mergedConf),
          analysis.matched_mineral,
        ],
      );

      return new Response(
        JSON.stringify({
          action: isStop ? "stop" : "continue",
          best_confidence: analysis.best_confidence,
          max_confidence: newMax,
          matched_mineral: isStop ? analysis.matched_mineral : null,
          per_mineral: analysis.per_mineral,
          per_mineral_confidence: mergedConf,
          rock_description: analysis.rock_description,
          ranked_rocks: scanRanked,
          stop_threshold: threshold,
          agent_panel_text: agentText,
          focus_rock_index: focusIndex,
        }),
        { status: 200, headers: { "Content-Type": "application/json", ...cors } },
      );
    }

    if (phase === "confirm1") {
      const minC = analysisMin(ctx);
      const qualRanked = ranked.filter((r) => r.confidence >= minC);
      const agentText = panelConfirm1(ranked, minC);
      const focus = focusFromSession(session);
      const bestConfidence = analysis.best_confidence;

      await ctx.db.query(
        `UPDATE scan_sessions
         SET confirm_confidence_1 = $2,
             ui_phase = 'confirm1',
             agent_panel_text = $3,
             latest_preview_object_id = COALESCE($4, latest_preview_object_id),
             per_mineral_confidence = $5,
             updated_at = now()
         WHERE id = $1`,
        [
          session_id,
          bestConfidence,
          agentText,
          previewId,
          JSON.stringify(mergedConf),
        ],
      );

      return new Response(
        JSON.stringify({
          phase,
          best_confidence: bestConfidence,
          focus_rock_description: session.rock_description || focus.description,
          focus_rock_index: focus.index,
          matched_mineral: session.matched_mineral ?? analysis.matched_mineral,
          per_mineral: analysis.per_mineral,
          qualifying_rocks: qualRanked,
          ranked_rocks: ranked,
          analysis_confidence_min: minC,
          agent_panel_text: agentText,
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
