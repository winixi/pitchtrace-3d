import { build } from "esbuild";
import { readFile, writeFile, mkdir, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
process.chdir(fileURLToPath(new URL(".", import.meta.url)));
await mkdir("dist", { recursive: true });
const browser = await build({
  entryPoints: ["src/app.js"],
  bundle: true,
  format: "iife",
  target: "es2022",
  minify: true,
  write: false,
});
const script = browser.outputFiles[0].text.replace(/<\/script/gi, "<\\/script");
const files = (await readdir("assets"))
  .filter((n) => n.endsWith(".glb"))
  .sort();
const assets = await Promise.all(
  files.map(
    async (f) =>
      '<script type="application/octet-stream" id="asset-' +
      f.slice(0, -4) +
      '">' +
      (await readFile("assets/" + f)).toString("base64") +
      "</script>",
  ),
);
const template = await readFile("index.template.html", "utf8");
const notices = await Promise.all(
  ["CREDITS.txt", "soccerviewer-MIT.txt", "three-MIT.txt", "ws-MIT.txt"].map(
    async (name) => readFile("licenses/" + name, "utf8"),
  ),
);
await writeFile(
  "dist/PitchTrace-Live-60s.html",
  template
    .replace("__LICENSES__", () =>
      notices.join("\n\n").replaceAll("&", "&amp;").replaceAll("<", "&lt;"),
    )
    .replace("__ASSETS__", () => assets.join("\n"))
    .replace("__APP__", () => script),
);
await build({
  entryPoints: ["server.mjs"],
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node20",
  outfile: "server.cjs",
  external: ["bufferutil", "utf-8-validate"],
});
