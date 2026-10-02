import { expect, test } from "@playwright/test";
import { login, watchConsole } from "./helpers";

// The app used to be called Appunti: a browser that remembers settings under the old names keeps them, and the
// session cookies carry the new name.
test.describe("after the rename to Lecta", () => {
  test("a browser's remembered settings and unsaved lesson drafts move to the new keys once", async ({ page }) => {
    const con = watchConsole(page);
    await page.goto("/login");
    await page.evaluate(() => {
      localStorage.clear();
      localStorage.setItem("appunti.theme", "dark");
      localStorage.setItem("appunti.ws.ai", "false");
      localStorage.setItem("appunti:lesson:7:unsaved", JSON.stringify({ notes: "bozza non salvata" }));
      localStorage.setItem("lecta.ws.rail", "false"); // already on the new name: never overwritten
      localStorage.setItem("appunti.ws.rail", "true");
    });
    await page.reload();
    const stored = await page.evaluate(() => Object.fromEntries(Object.entries(localStorage)));
    expect(stored).toEqual({
      "lecta.theme": "dark",
      "lecta.ws.ai": "false",
      "lecta:lesson:7:unsaved": JSON.stringify({ notes: "bozza non salvata" }),
      "lecta.ws.rail": "false",
    });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    con.assertClean([/status of 404/]);
  });

  test("the session and CSRF cookies are lecta_*", async ({ page, context }) => {
    await login(page);
    const names = (await context.cookies()).map((c) => c.name);
    expect(names).toEqual(expect.arrayContaining(["lecta_session", "lecta_csrf"]));
    expect(names.filter((n) => n.startsWith("appunti"))).toEqual([]);
  });
});
