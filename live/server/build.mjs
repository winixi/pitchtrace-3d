import { build } from "esbuild";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = fileURLToPath(new URL(".", import.meta.url));
const schemaLicensePaths = [
  "ajv/LICENSE",
  "fast-deep-equal/LICENSE",
  "fast-uri/LICENSE",
  "json-schema-traverse/LICENSE",
  "require-from-string/license",
];
const schemaLicenses = await Promise.all(
  schemaLicensePaths.map(async (file) => {
    const license = await readFile(
      path.join(dir, "node_modules", file),
      "utf8",
    );
    return file.split("/")[0] + "\n" + license.trim();
  }),
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
  banner: { js: "/*!\n" + schemaLicenses.join("\n\n") + "\n*/" },
  external: ["bufferutil", "utf-8-validate"],
});
