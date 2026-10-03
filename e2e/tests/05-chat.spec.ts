import { expect, test } from "@playwright/test";
import { login, openCourse, pickBlock, watchConsole } from "./helpers";

// A busy assistant (409) and the probing of private URLs (404) may show up in the console.
const EXPECTED = [/status of 404/, /status of 409/];

test.describe.serial("AI workspace", () => {
  test("pick a block of the draft, Spiega opens the chat on it and waits for the question; the answer streams in", async ({ page }) => {
    test.setTimeout(240_000);
    const con = watchConsole(page);
    await login(page);
    await openCourse(page, /Teoria dei Segnali/);
    const doc = page.getByTestId("doc-preview");
    // The draft is typeset by LaTeX.
    await expect(doc.locator(".doc-block img").first()).toBeVisible({ timeout: 60_000 });

    await pickBlock(page);
    // The menu has three buttons, nothing else.
    const menu = page.getByTestId("selection-toolbar");
    await expect(menu.getByRole("button")).toHaveText(["Rimuovi", "Spiega", "Correggi"]);
    await menu.getByRole("button", { name: "Spiega" }).click();

    const panel = page.getByTestId("ai-panel");
    // Nothing is sent: the passage is the context and the chat waits for the question.
    await expect(page.getByTestId("ai-composer")).toBeFocused();
    await expect(page.getByTestId("ai-composer")).toHaveAttribute("placeholder", /non ti è chiaro/);
    await expect(panel.getByText(/selezione di \d+ rig/)).toBeVisible();
    await expect(panel.locator('[data-testid="ai-message"][data-role="user"]')).toHaveCount(0);
    await page.getByTestId("ai-composer").fill("Spiegami meglio questo passaggio.");
    await page.keyboard.press("Enter");
    // The request shows up with what it is about…
    await expect(panel.locator('[data-testid="ai-message"][data-role="user"]').last()).toContainText("Spiegami");
    // …the reply arrives, with the tools the assistant used…
    const reply = panel.locator('[data-testid="ai-message"][data-role="assistant"]').last();
    await expect(reply).toBeVisible({ timeout: 60_000 });
    await expect(reply).toHaveAttribute("data-status", "done", { timeout: 120_000 });
    await expect(reply.getByTestId("ai-steps")).toBeVisible();
    await reply.getByRole("button", { name: /Ha fatto \d+ azion/ }).click();
    await expect(reply.getByTestId("ai-tool-step").first()).toBeVisible();
    // …and an explanation never changes the document.
    await expect(reply.getByTestId("ai-change-card")).toHaveCount(0);
    con.assertClean(EXPECTED);
  });

  test("Correggi: the selection becomes the scope and the chat waits for what to correct", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await openCourse(page, /Teoria dei Segnali/);
    await pickBlock(page);
    await page.getByTestId("selection-toolbar").getByRole("button", { name: "Correggi" }).click();
    await expect(page.getByTestId("ai-composer")).toBeFocused();
    await expect(page.getByTestId("ai-composer")).toHaveAttribute("placeholder", /da correggere/);
    await expect(page.getByTestId("ai-panel").getByText(/selezione di \d+ rig/)).toBeVisible();
    await page.getByLabel("Togli il contesto").click();
    await expect(page.getByTestId("ai-panel").getByText(/selezione di \d+ rig/)).toHaveCount(0);
    con.assertClean(EXPECTED);
  });

  test("ask for an example: the change is applied at once, shown in the draft, and Annulla takes it back", async ({ page }) => {
    test.setTimeout(240_000);
    const con = watchConsole(page);
    await login(page);
    await openCourse(page, /Teoria dei Segnali/);
    const doc = page.getByTestId("doc-preview");
    await expect(doc.locator(".doc-block img").first()).toBeVisible({ timeout: 60_000 });
    const examples = doc.locator(".doc-block");
    const before = await examples.count();

    await page.getByTestId("ai-composer").fill("Aggiungi un esempio dopo la definizione");
    await page.keyboard.press("Enter");

    const card = page.getByTestId("ai-change-card").last();
    await expect(card).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId("ai-undo")).toBeVisible({ timeout: 60_000 });
    // No approval step: the draft already shows the new example (one more block).
    await expect(examples).toHaveCount(before + 1, { timeout: 30_000 });
    await expect(card).toHaveAttribute("data-status", "applied");

    await page.getByTestId("ai-undo").click();
    await expect(card).toHaveAttribute("data-status", "undone", { timeout: 30_000 });
    await expect(card.getByText("Annullato")).toBeVisible();
    await expect(examples).toHaveCount(before, { timeout: 30_000 });
    con.assertClean(EXPECTED);
  });

  test("no «Revisione AI» button in the top bar", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await openCourse(page, /Teoria dei Segnali/);
    await expect(page.getByTestId("ai-review-button")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Revisione AI/ })).toHaveCount(0);
    con.assertClean(EXPECTED);
  });

  test("the conversation is still there after a reload", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await openCourse(page, /Teoria dei Segnali/);
    await expect(page.getByTestId("ai-message").first()).toBeVisible({ timeout: 30_000 });
    await page.reload();
    await expect(page.getByTestId("ai-message").first()).toBeVisible({ timeout: 30_000 });
    con.assertClean(EXPECTED);
  });

  test("Rimuovi sends at once: the passage is to be taken out, the formatting around it tidied", async ({ page }) => {
    const con = watchConsole(page);
    await login(page);
    await openCourse(page, /Teoria dei Segnali/);
    await pickBlock(page);
    await page.getByTestId("selection-toolbar").getByRole("button", { name: "Rimuovi" }).click();
    const user = page.getByTestId("ai-panel").locator('[data-testid="ai-message"][data-role="user"]').last();
    await expect(user).toContainText("Rimuovi questo passaggio");
    await expect(user).toContainText("piccole correzioni di formattazione");
    // Let the assistant finish before the next test starts.
    await expect(page.getByTestId("ai-panel").locator('[data-testid="ai-message"][data-role="assistant"]').last()).toHaveAttribute("data-status", /done|error|cancelled/, { timeout: 120_000 });
    con.assertClean(EXPECTED);
  });
});
