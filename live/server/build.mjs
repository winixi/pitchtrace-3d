import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = fileURLToPath(new URL(".", import.meta.url));
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
