import { build } from "esbuild";
import { copyFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = fileURLToPath(new URL(".", import.meta.url));
await mkdir(path.join(dir, "dist"), { recursive: true });
await copyFile(
  new URL("../../live/web/dist/index.html", import.meta.url),
  path.join(dir, "dist/PitchTrace-Live-60s.html"),
);
await build({
  absWorkingDir: dir,
  entryPoints: ["server.mjs"],
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node20",
  nodePaths: [path.join(dir, "node_modules")],
  outfile: "server.cjs",
  external: ["bufferutil", "utf-8-validate"],
});
