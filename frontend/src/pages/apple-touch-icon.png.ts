import type { APIRoute } from "astro";
import { BRAND_VERSION } from "../lib/brand";

// Where Safari looks for the home-screen picture when a page does not say.
export const GET: APIRoute = () =>
  new Response(null, { status: 301, headers: { location: `/icons/icon-180.png?v=${BRAND_VERSION}`, "cache-control": "public, max-age=86400" } });
