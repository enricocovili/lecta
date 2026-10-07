import type { APIRoute } from "astro";
import { faviconSvg } from "../lib/brand";

// The URL in the pages carries the logo's version: a new logo is a new URL, so the long cache is safe.
export const GET: APIRoute = () =>
  new Response(faviconSvg(), { headers: { "content-type": "image/svg+xml", "cache-control": "public, max-age=604800" } });
