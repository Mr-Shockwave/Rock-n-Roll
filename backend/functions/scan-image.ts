// Mint a fresh presigned download URL for one stored image object_id.
// Called by the remote (butterbase.dev) frontend ONLY when the camera image
// changes (~once per captured frame), so the slow storage round-trip never sits
// on the fast scan-status/scan-latest poll path. Local (file://) frontends skip
// this entirely and read captures/latest.jpg directly.
function controlBase(ctx: any): string {
  const url = (ctx.env.BUTTERBASE_API_URL || "https://api.butterbase.ai").replace(/\/$/, "");
  if (url.includes("/v1/")) return url.split("/v1/")[0];
  return "https://api.butterbase.ai";
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

  const objectId = new URL(req.url).searchParams.get("object_id");
  if (!objectId) {
    return new Response(JSON.stringify({ url: null }), {
      status: 200,
      headers: { "Content-Type": "application/json", ...cors },
    });
  }

  const appId = ctx.env.BUTTERBASE_APP_ID;
  const apiKey = ctx.env.BUTTERBASE_API_KEY;
  let url: string | null = null;
  try {
    const resp = await fetch(`${controlBase(ctx)}/storage/${appId}/download/${objectId}`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (resp.ok) {
      const data = await resp.json();
      url = data.downloadUrl || data.download_url || null;
    }
  } catch {
    url = null;
  }

  return new Response(JSON.stringify({ url, object_id: objectId }), {
    status: 200,
    headers: { "Content-Type": "application/json", ...cors },
  });
}
