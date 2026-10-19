test("beta writes state", async ({ page, context, request, baseURL, hassetteServer }: Fixtures): Promise<void> => {
  const resp = await request.post("/api/state", { data: { "light.kitchen": "on" } });
  expect(resp.ok()).toBe(true);
  const saved = await hassetteServer.readState();
  expect(saved["light.kitchen"]).toEqual("on");
});
