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

  let body: { session_id?: string };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON body" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const { session_id } = body;
  if (!session_id) {
    return new Response(JSON.stringify({ error: "session_id required" }), {
      status: 400,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  await ctx.db.query(
    `UPDATE scan_sessions
     SET status = 'timeout',
         needs_analysis = false,
         result_message = 'No promising rocks found.',
         updated_at = now()
     WHERE id = $1`,
    [session_id],
  );

  return new Response(
    JSON.stringify({
      session_id,
      status: "timeout",
      result_message: "No promising rocks found.",
    }),
    { status: 200, headers: { "Content-Type": "application/json", ...cors } },
  );
}
