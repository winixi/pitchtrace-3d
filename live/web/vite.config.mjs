import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import { readFileSync, readdirSync } from "node:fs";

const here = new URL(".", import.meta.url);
const read = (path, encoding) => readFileSync(new URL(path, here), encoding);
const escapeHtml = (text) =>
  text.replaceAll("&", "&amp;").replaceAll("<", "&lt;");
const assets = readdirSync(new URL("assets/", here))
  .filter((n) => n.endsWith(".glb"))
  .sort();
const notices = readdirSync(new URL("licenses/", here)).sort();

export default defineConfig({
  base: "./",
  plugins: [
    {
      name: "pitchtrace-offline-assets",
      transformIndexHtml: {
        order: "pre",
        handler(html) {
          const logo =
            "data:image/png;base64," + read("../../assets/pitchtrace-logo.png", "base64");
          const models = assets.map(
            (n) =>
              '<script type="application/octet-stream" id="asset-' +
              n.slice(0, -4) +
              '">' +
              read("assets/" + n, "base64") +
              "</script>",
          );
          return html
            .replaceAll("__BRAND_ICON__", logo)
            .replace("__ASSETS__", () => models.join("\n"))
            .replace("__LICENSES__", () =>
              escapeHtml(
                notices.map((n) => read("licenses/" + n, "utf8")).join("\n\n"),
              ),
            );
        },
      },
    },
    viteSingleFile(),
  ],
  build: { target: "es2022", reportCompressedSize: false },
});
