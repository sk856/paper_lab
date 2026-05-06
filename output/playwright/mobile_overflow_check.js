async page => {
  const routes = ['paper','datachart','ai','plagiarism','polish','correction','config','history'];
  const results = [];
  await page.setViewportSize({ width: 360, height: 780 });
  for (const name of routes) {
    await page.goto(`http://127.0.0.1:8765/#/${name}`);
    await page.waitForTimeout(400);
    const data = await page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      const bodyW = document.body.scrollWidth;
      const docW = document.documentElement.scrollWidth;
      const offenders = Array.from(document.querySelectorAll('body *')).map((el) => {
        const rect = el.getBoundingClientRect();
        return {
          tag: el.tagName.toLowerCase(),
          id: el.id || '',
          cls: String(el.className || '').slice(0, 80),
          text: String(el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 60),
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
        };
      }).filter((item) => item.width > 0 && (item.left < -2 || item.right > vw + 2)).slice(0, 10);
      return { vw, bodyW, docW, offenders };
    });
    results.push({ page: name, ...data });
  }
  return results;
}
