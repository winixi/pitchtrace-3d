import { createServer } from "vite";
const server = await createServer({
  logLevel: "silent",
  server: { host: "127.0.0.1", port: 0, hmr: false },
});
await server.listen();
let html;
try {
  const { port } = server.httpServer.address();
  const res = await fetch(`http://127.0.0.1:${port}/`, {
    signal: AbortSignal.timeout(10000),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  html = await res.text();
} finally {
  await server.close();
}

const checks = [
  ["lang switcher zh-CN btn",   html.includes('data-lang="zh-CN"')],
  ["lang switcher en btn",      html.includes('data-lang="en"')],
  ["data-i18n on <title>",      html.includes('data-i18n="title"')],
  ["data-i18n source.demo",     html.includes('data-i18n="source.demo"')],
  ["data-i18n loading.init",    html.includes('data-i18n="loading.init"')],
  ["data-i18n cameras.heading", html.includes('data-i18n="cameras.heading"')],
  ["data-i18n option.bones",    html.includes('data-i18n="option.bones"')],
  ["data-i18n media.summary",   html.includes('data-i18n="media.summary"')],
  ["HTML 默认英文",              html.includes('<html lang="en">')],
  ["页眉默认显示英文品牌",       html.includes('data-i18n="brand.name">PitchTrace 3D</span>')],
  ["app.js script tag",         html.includes('src/app.js')],
];
let allOk = true;
for (const [name, ok] of checks) {
  console.log((ok ? "✓" : "✗") + " " + name);
  if (!ok) allOk = false;
}
process.exit(allOk ? 0 : 1);
