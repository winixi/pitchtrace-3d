import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { WebSocketServer, WebSocket } from "ws";
import { demoFrame } from "../../live/web/src/match.js";
import { validFrame } from "../../live/web/src/protocol.js";
const host = process.env.PITCHTRACE_HOST || "127.0.0.1",
  port = Number(process.env.PITCHTRACE_PORT || 8766);
const legacyPage = path.join(
  path.dirname(path.resolve(process.argv[1])),
  "dist",
  "PitchTrace-Live-60s.html",
);
const viewerPage = path.resolve(
  path.dirname(legacyPage),
  "../../../live/web/dist/index.html",
);
const page = fs.existsSync(viewerPage) ? viewerPage : legacyPage;
const wss = new WebSocketServer({ noServer: true, maxPayload: 256000 });
let latest = null;
let receivedAt = 0;
function send(socket, frame) {
  if (socket.readyState === WebSocket.OPEN && socket.bufferedAmount < 256000)
    socket.send(frame);
}
const server = http.createServer(async (request, response) => {
  function reply(code, body) {
    response.writeHead(code, { "Content-Type": "text/plain; charset=utf-8" });
    response.end(body);
  }
  if (request.method === "GET" && request.url === "/") {
    if (!fs.existsSync(page))
      return reply(404, "Run npm run build to create the viewer.");
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    fs.createReadStream(page)
      .on("error", () => response.destroy())
      .pipe(response);
    return;
  }
  if (request.method === "POST" && request.url === "/frame") {
    try {
      let bytes = 0,
        chunks = [];
      for await (const chunk of request) {
        bytes += chunk.length;
        if (bytes > 256000) {
          return reply(413, "Frame too large");
        }
        chunks.push(chunk);
      }
      const f = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (!validFrame(f)) return reply(422, "Invalid PitchTrace frame");
      latest = JSON.stringify(f);
      receivedAt = Date.now();
      for (const s of wss.clients) if (s.feed === "/live") send(s, latest);
      return reply(202, "Accepted");
    } catch {
      return reply(400, "Invalid JSON");
    }
  }
  return reply(404, "Unknown endpoint");
});
server.on("upgrade", (req, socket, head) => {
  if (!["/demo", "/live"].includes(req.url)) {
    socket.destroy();
    return;
  }
  wss.handleUpgrade(req, socket, head, (s) => {
    s.feed = req.url;
    s.on("error", () => s.terminate());
    if (s.feed === "/live" && latest && Date.now() - receivedAt < 1500)
      send(s, latest);
  });
});
const beginning = performance.now();
const ticker = setInterval(() => {
  const viewers = Array.from(wss.clients).filter((s) => s.feed === "/demo");
  if (!viewers.length) return;
  const data = JSON.stringify(
    demoFrame(((performance.now() - beginning) / 1000) % 60),
  );
  viewers.forEach((s) => send(s, data));
}, 50);
server.on("close", () => clearInterval(ticker));
server.listen(port, host, () =>
  console.log("PitchTrace 3D   http://" + host + ":" + port),
);
