import type { APIRoute } from "astro";
import { getSite } from "../lib/server";

export const GET: APIRoute = async (ctx) => {
  const site = await getSite(ctx);
  const body = site.allow_indexing
    ? "User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nDisallow: /login\nDisallow: /setup\n"
    : "User-agent: *\nDisallow: /\n";
  return new Response(body, { headers: { "content-type": "text/plain; charset=utf-8" } });
};
