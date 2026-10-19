test("alpha reconnects", async ({ page, context, request, baseURL, hassetteServer }: Fixtures): Promise<void> => {
  await context.addCookies([{ name: "token", value: "abc", url: baseURL }]);
  await hassetteServer.start();
  await page.goto("/status");
  await expect(page.getByText("reconnected")).toBeVisible({ timeout: 5000 });
});
