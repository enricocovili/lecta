// Page-level access control. The backend enforces the real boundary for data;
// this makes private *pages* indistinguishable from unknown ones for anonymous
// visitors (they get the generic 404 page).
import { defineMiddleware } from "astro:middleware";
import { backendGet } from "./lib/server";

type Me = { authenticated: boolean; username?: string; csrf?: string; needs_setup?: boolean };

export const onRequest = defineMiddleware(async (context, next) => {
  const path = context.url.pathname;
  const needsAuthInfo =
    path === "/" || path.startsWith("/admin") || path === "/login" || path === "/setup" || path.startsWith("/courses") || path === "/search";
  let me: Me = { authenticated: false };
  if (needsAuthInfo) {
    const r = await backendGet<Me>(context, "/api/auth/me");
    if (r.data) me = r.data;
  }
  context.locals.auth = me;

  if (path === "/admin" || path.startsWith("/admin/")) {
    if (!me.authenticated) {
      const res = await context.rewrite("/404");
      return new Response(res.body, { status: 404, headers: res.headers });
    }
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
  return next();
});
