// Page-level access control. The backend enforces the real boundary for data;
// this makes private *pages* indistinguishable from unknown ones for anonymous
// visitors (they get the generic 404 page), and admin pages for everyone else.
import type { APIContext } from "astro";
import { defineMiddleware } from "astro:middleware";
import type { Me } from "./lib/roles";
import { backendGet } from "./lib/server";

// Pages only admins may use (decision 124). The admin-only settings sections (providers, models, prompts, AI and
// costs, LaTeX, index, site, users…) are tabs of /admin/settings behind a URL hash the server never sees: the page
// leaves them out for the other roles.
const ADMIN_ONLY = ["/admin/audit"];

const notFound = async (context: APIContext) => {
  const res = await context.rewrite("/404");
  return new Response(res.body, { status: 404, headers: res.headers });
};

export const onRequest = defineMiddleware(async (context, next) => {
  const path = context.url.pathname;
  const needsAuthInfo =
    path === "/" || path.startsWith("/admin") || path === "/login" || path === "/setup" || path.startsWith("/courses") || path === "/search" ||
    path.startsWith("/invito/"); // an invite page tells who is signed in already
  let me: Me = { authenticated: false };
  if (needsAuthInfo) {
    const r = await backendGet<Me>(context, "/api/auth/me");
    if (r.data) me = r.data;
  }
  context.locals.auth = me;

  if (path === "/admin" || path.startsWith("/admin/")) {
    const adminOnly = ADMIN_ONLY.some((p) => path === p || path.startsWith(p + "/"));
    if (!me.authenticated || (adminOnly && me.role !== "admin")) return notFound(context);
    const res = await next();
    res.headers.set("cache-control", "no-store");
    return res;
  }
  if (path === "/setup" && !me.needs_setup) {
    return context.redirect(me.authenticated ? "/admin" : "/login");
  }
  if (path === "/login" && me.authenticated) {
    return context.redirect("/admin");
  }
  if (me.needs_setup && (path === "/" || path === "/login")) {
    return context.redirect("/setup");
  }
  // A signed-in user's home is their dashboard; «?public» opens the public site as a visitor sees it.
  if (path === "/" && me.authenticated && !context.url.searchParams.has("public")) {
    return context.redirect("/admin");
  }
  return next();
});
