import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { WebSocketServer, WebSocket } from "ws";
import { demoFrame } from "../web/src/match.js";
import { validFrame } from "../web/src/protocol.js";
import { openSceneArchive } from "./scene-archive.mjs";
import {
  sceneRoute,
  serializeSceneFrame,
  validSceneFrame,
} from "./scene-protocol.mjs";

const host = process.env.PITCHTRACE_HOST || "127.0.0.1",
  port = Number(process.env.PITCHTRACE_PORT || 8766);
const serverDir = path.dirname(process.argv[1]);
const page = path.resolve(serverDir, "../web/dist/index.html");
const archivePath =
  process.env.PITCHTRACE_DB || path.join(serverDir, "data", "scenes.sqlite");
const maxFrameBytes = 256000;
const wss = new WebSocketServer({ noServer: true, maxPayload: maxFrameBytes });
const sceneViewers = new Map();
let archive;
let latest = null;
let receivedAt = 0;

function getArchive() {
  if (archive === undefined) archive = openSceneArchive(archivePath);
  return archive;
}

function send(socket, frame) {
  if (
    socket.readyState === WebSocket.OPEN &&
    socket.bufferedAmount < maxFrameBytes
  )
    socket.send(frame);
}

function sendScene(socket, payload) {
  if (socket.readyState !== WebSocket.OPEN) return;
  if (socket.sendingScene) {
    socket.pendingScene = payload;
    return;
  }
  socket.sendingScene = true;
  socket.send(payload, (error) => {
    if (error) {
      socket.terminate();
      return;
    }
    socket.sendingScene = false;
    const next = socket.pendingScene;
    socket.pendingScene = undefined;
    if (next) sendScene(socket, next);
  });
}

async function readFrame(request) {
  let bytes = 0;
  const chunks = [];
  try {
    for await (const chunk of request.iterator({ destroyOnReturn: false })) {
      bytes += chunk.length;
      if (bytes > maxFrameBytes) break;
      chunks.push(chunk);
    }
    if (bytes > maxFrameBytes) {
      request.resume();
      return { code: 413, message: "Frame too large" };
    }
    return { frame: JSON.parse(Buffer.concat(chunks).toString("utf8")) };
  } catch {
    return { code: 400, message: "Invalid JSON" };
  }
}

const server = http.createServer(async (request, response) => {
  function reply(code, body) {
    response.writeHead(code, { "Content-Type": "text/plain; charset=utf-8" });
    response.end(body);
  }
  const pathname = request.url.split("?", 1)[0];
  if (request.method === "GET" && pathname === "/") {
    if (!fs.existsSync(page))
      return reply(404, "Build the viewer with npm run build in live/web.");
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    fs.createReadStream(page)
      .on("error", () => response.destroy())
      .pipe(response);
    return;
  }
  if (request.method !== "POST") return reply(404, "Unknown endpoint");

  const v1 = request.url === "/frame";
  const matchId = sceneRoute(pathname, "frames");
  if (!v1 && !matchId) return reply(404, "Unknown endpoint");
  const { frame, code, message } = await readFrame(request);
  if (code) return reply(code, message);

  if (v1) {
    if (!validFrame(frame)) return reply(422, "Invalid PitchTrace frame");
    try {
      latest = JSON.stringify(frame);
    } catch {
      return reply(400, "Invalid JSON");
    }
    receivedAt = Date.now();
    for (const s of wss.clients) if (s.feed === "/live") send(s, latest);
    return reply(202, "Accepted");
  }

  if (!validSceneFrame(frame, matchId))
    return reply(422, "Invalid PitchTrace v2 frame");
  let payload;
  try {
    payload = serializeSceneFrame(frame);
    if (Buffer.byteLength(payload) > maxFrameBytes)
      return reply(413, "Frame too large");
  } catch {
    return reply(422, "Invalid PitchTrace v2 frame");
  }

  let result;
  try {
    const store = getArchive();
    if (!store) return reply(503, "v2 requires Node.js with SQLite support");
    result = store.write(frame, payload);
  } catch (error) {
    console.error("v2 场景归档写入失败", error);
    return reply(500, "Scene archive write failed");
  }
  if (result === "conflict") {
    console.warn("v2 场景帧键冲突", matchId, frame.streamEpoch, frame.seq);
    return reply(409, "Conflicting scene frame");
  }
  if (result === "live") {
    for (const socket of sceneViewers.get(matchId) ?? [])
      sendScene(socket, payload);
  }
  return reply(202, "Accepted");
});

server.on("upgrade", (req, socket, head) => {
  const matchId = sceneRoute(req.url.split("?", 1)[0], "live");
  if (matchId) {
    let current;
    try {
      const store = getArchive();
      if (!store) return rejectSceneUpgrade(socket);
      current = store.latest(matchId);
    } catch (error) {
      console.error("v2 场景归档读取失败", error);
      return rejectSceneUpgrade(socket);
    }
    wss.handleUpgrade(req, socket, head, (viewer) => {
      const viewers = sceneViewers.get(matchId) ?? new Set();
      viewers.add(viewer);
      sceneViewers.set(matchId, viewers);
      viewer.on("error", () => viewer.terminate());
      viewer.once("close", () => {
        viewers.delete(viewer);
        if (!viewers.size) sceneViewers.delete(matchId);
      });
      if (current) sendScene(viewer, current);
    });
    return;
  }

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

function rejectSceneUpgrade(socket) {
  socket.end(
    "HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n",
  );
}
const beginning = performance.now();
const ticker = setInterval(() => {
  const viewers = Array.from(wss.clients).filter((s) => s.feed === "/demo");
  if (!viewers.length) return;
  const data = JSON.stringify(
    demoFrame(((performance.now() - beginning) / 1000) % 60),
  );
  viewers.forEach((s) => send(s, data));
}, 50);
server.on("close", () => {
  clearInterval(ticker);
  archive?.close();
});
server.listen(port, host, () =>
  console.log("PitchTrace 3D   http://" + host + ":" + server.address().port),
);
