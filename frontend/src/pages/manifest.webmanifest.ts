import type { APIRoute } from "astro";
import { BRAND_VERSION } from "../lib/brand";

// The installed app (the phone's home-screen icon opens the lessons), with the icons of the current logo.
export const GET: APIRoute = () => {
  const v = `?v=${BRAND_VERSION}`;
  const manifest = {
    name: "Lecta",
    short_name: "Lecta",
    start_url: "/admin/lessons",
    display: "standalone",
    background_color: "#f3f2ed",
    theme_color: "#fcfbf7",
    icons: [
      { src: `/icons/icon-192.png${v}`, sizes: "192x192", type: "image/png", purpose: "any maskable" },
      { src: `/icons/icon-512.png${v}`, sizes: "512x512", type: "image/png", purpose: "any maskable" },
      { src: `/favicon.svg${v}`, sizes: "any", type: "image/svg+xml", purpose: "any" },
    ],
  };
  return new Response(JSON.stringify(manifest, null, 2), {
    headers: { "content-type": "application/manifest+json", "cache-control": "public, max-age=3600" },
  });
};
