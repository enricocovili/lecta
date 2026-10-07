import type { APIRoute } from "astro";
import { BRAND_VERSION } from "../lib/brand";

// Asked for by pages that have no <link rel="icon"> (a PDF opened in its own tab): the same favicon as everywhere else.
export const GET: APIRoute = () =>
  new Response(null, { status: 301, headers: { location: `/favicon.svg?v=${BRAND_VERSION}`, "cache-control": "public, max-age=86400" } });
