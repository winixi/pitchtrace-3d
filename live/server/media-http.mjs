import { refuseWrite } from "./device-auth.mjs";
import { createHash, randomUUID } from "node:crypto";
import { createReadStream, mkdtempSync, rmSync } from "node:fs";
import { open, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { openMediaArchive } from "./media-archive.mjs";
import {
  mediaMaxBytes,
  parseExposure,
  readMediaIndex,
  validSha256,
} from "./media-index.mjs";
import { validSceneId } from "./scene-protocol.mjs";

const indexMaxBytes = 1_000_000;
const cameraPath = (match, camera) => `/v2/matches/${match}/cameras/${camera}`;
const epochPath = (match, camera, epoch) =>
  `${cameraPath(match, camera)}/epochs/${epoch}`;
const hlsType = "application/vnd.apple.mpegurl";

class UploadError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function mediaRoute(pathname) {
  const [
    ,
    version,
    resource,
    match,
    cameras,
    camera,
    epochs,
    epoch,
    item,
    segment,
    extra,
    overflow,
  ] = pathname.split("/");
  if (
    version !== "v2" ||
    resource !== "matches" ||
    cameras !== "cameras" ||
    !validSceneId(match)
  )
    return null;
  if (camera === undefined) return { kind: "cameras", key: [match] };
  if (!validSceneId(camera)) return null;
  if (epoch === undefined && (epochs === "locate" || epochs === "index.m3u8"))
    return { kind: epochs, key: [match, camera] };
  if (epochs !== "epochs" || !validSceneId(epoch)) return null;
  const key = [match, camera, epoch];
  if (item === "init.mp4" && segment === undefined)
    return { kind: "init", key };
  if (item !== "segments" || segment === undefined || overflow !== undefined)
    return null;
  const numbered = /^(0|[1-9]\d*)(\.m4s|\.m3u8)?$/.exec(segment);
  if (!numbered || !Number.isSafeInteger(Number(numbered[1]))) return null;
  key.push(Number(numbered[1]));
  if (numbered[2] === ".m4s" && extra === undefined)
    return { kind: "segment", key };
  if (numbered[2] === ".m3u8" && extra === undefined)
    return { kind: "clip", key };
  if (!numbered[2] && extra === "index") return { kind: "index", key };
  return null;
}

async function readUpload(request, limit, consume) {
  let count = 0;
  for await (const chunk of request.iterator({ destroyOnReturn: false })) {
    count += chunk.length;
    if (count > limit) break;
    await consume(chunk);
  }
  if (count > limit) {
    request.resume();
    throw new UploadError(413, "Media upload too large");
  }
  return count;
}

async function writeBinary(request, store, key, kind) {
  const digest = request.headers["x-content-sha256"];
  if (!validSha256(digest)) {
    request.resume();
    throw new UploadError(422, "A valid X-Content-SHA256 header is required");
  }
  const filename = join(store.uploads, randomUUID());
  const file = await open(filename, "wx");
  const hash = createHash("sha256");
  try {
    const bytes = await readUpload(request, mediaMaxBytes, async (chunk) => {
      hash.update(chunk);
      await file.writeFile(chunk);
    });
    if (!bytes || hash.digest("hex") !== digest)
      throw new UploadError(422, "Media length or digest is invalid");
    await file.sync();
    const write = kind === "init" ? store.writeInit : store.writeSegment;
    return write(key, { filename, digest, bytes });
  } finally {
    await file.close();
    await unlink(filename);
  }
}

async function writeIndex(request, store, key) {
  const chunks = [];
  await readUpload(request, indexMaxBytes, (chunk) => chunks.push(chunk));
  let data;
  try {
    data = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new UploadError(400, "Invalid media index JSON");
  }
  const index = readMediaIndex(data);
  if (!index) throw new UploadError(422, "Invalid media index");
  return store.writeIndex(key, index);
}

function playlistText(match, camera, parts, clip) {
  const target = parts.reduce(
    (max, part) => Math.max(max, Math.ceil(part.duration)),
    1,
  );
  const lines = [
    "#EXTM3U",
    "#EXT-X-VERSION:7",
    `#EXT-X-TARGETDURATION:${target}`,
    "#EXT-X-MEDIA-SEQUENCE:0",
    "#EXT-X-INDEPENDENT-SEGMENTS",
  ];
  if (clip) lines.push("#EXT-X-PLAYLIST-TYPE:VOD");
  let last;
  for (const part of parts) {
    const base = epochPath(match, camera, part.epoch);
    if (
      last &&
      (last.epoch !== part.epoch ||
        last.segment + 1 !== part.segment ||
        last.numerator !== part.numerator ||
        last.denominator !== part.denominator ||
        Math.abs(
          ((part.first_pts - last.first_pts) * last.numerator) /
            last.denominator -
            last.duration,
        ) > 0.000001)
    )
      lines.push("#EXT-X-DISCONTINUITY");
    if (last?.epoch !== part.epoch)
      lines.push(`#EXT-X-MAP:URI="${base}/init.mp4"`);
    lines.push(
      `#EXTINF:${part.duration.toFixed(6)},`,
      `${base}/segments/${part.segment}.m4s`,
    );
    last = part;
  }
  return lines.concat("#EXT-X-ENDLIST", "").join("\n");
}

function locate(store, key, params) {
  if (params.size !== 1) throw new UploadError(400, "Invalid exposure query");
  const captureUtcUs = params.get("captureUtcUs");
  const utc = parseExposure(captureUtcUs);
  if (utc === null) throw new UploadError(400, "Invalid exposure time");
  const frame = store.locate(...key, utc);
  if (!frame) return null;
  const [match, camera] = key;
  const secondsPerTick = frame.numerator / frame.denominator;
  const offsetSeconds = (frame.pts - frame.first_pts) * secondsPerTick;
  return {
    cameraId: camera,
    captureUtcUs,
    frameCaptureUtcUs: frame.exposure,
    skewUs: frame.skew,
    mediaEpoch: frame.epoch,
    segment: frame.segment,
    pts: frame.pts,
    timeBase: { numerator: frame.numerator, denominator: frame.denominator },
    offsetSeconds,
    seekSeconds: offsetSeconds + (frame.duration_pts * secondsPerTick) / 2,
    playlistUrl: `${epochPath(match, camera, frame.epoch)}/segments/${frame.segment}.m3u8`,
  };
}

function send(response, status, data, type = "text/plain; charset=utf-8") {
  response.writeHead(status, {
    "Content-Type": type,
    "Cache-Control": "no-store",
  });
  response.end(data);
}

export function createMediaHandler(filename, configuredDirectory, { authorize } = {}) {
  let store;
  let temporary;
  return {
    async handle(request, response, pathname, query) {
      const route = mediaRoute(pathname);
      if (!route) return false;
      const { kind, key } = route;
      const put =
        request.method === "PUT" && ["init", "segment", "index"].includes(kind);
      const get = request.method === "GET" && kind !== "index";
      if (!put && !get) return false;
      if (put && authorize) {
        const denied = authorize(request, key[0]);
        if (denied) {
          refuseWrite(request, response, denied);
          return true;
        }
      }
      try {
        if (store === undefined) {
          const directory =
            configuredDirectory ||
            (filename === ":memory:"
              ? (temporary ??= mkdtempSync(join(tmpdir(), "pitchtrace-media-")))
              : join(dirname(filename), "media"));
          store = openMediaArchive(filename, directory);
        }
        if (!store)
          throw new UploadError(503, "v2 requires Node.js with SQLite support");
        if (put) {
          const accepted =
            kind === "index"
              ? await writeIndex(request, store, key)
              : await writeBinary(request, store, key, kind);
          if (!accepted) throw new UploadError(409, "Conflicting media part");
          send(response, 202, "Accepted");
        } else if (kind === "cameras" || kind === "locate") {
          const data =
            kind === "cameras"
              ? { cameraIds: store.cameraIds(...key) }
              : locate(store, key, new URLSearchParams(query));
          if (!data) throw new UploadError(404, "No footage at this exposure");
          send(
            response,
            200,
            JSON.stringify(data),
            "application/json; charset=utf-8",
          );
        } else if (kind === "index.m3u8" || kind === "clip") {
          const parts =
            kind === "clip"
              ? [store.readySegment(key)].filter(Boolean)
              : store.playlist(...key);
          if (!parts.length)
            throw new UploadError(404, "No complete media segments");
          send(
            response,
            200,
            playlistText(key[0], key[1], parts, kind === "clip"),
            hlsType,
          );
        } else {
          const part =
            kind === "init" ? store.readyInit(key) : store.readySegment(key);
          if (!part) throw new UploadError(404, "Media part is unavailable");
          const stream = createReadStream(
            store.part(part.digest ?? part.segment_digest),
          );
          stream.once("error", () => {
            if (response.headersSent) response.destroy();
            else send(response, 404, "Media file is unavailable");
          });
          stream.once("open", () => {
            response.writeHead(200, {
              "Content-Type": "video/mp4",
              "Content-Length": part.bytes,
            });
            stream.pipe(response);
          });
          response.once("close", () => stream.destroy());
        }
      } catch (error) {
        if (error instanceof UploadError)
          send(response, error.status, error.message);
        else if (!request.aborted) {
          console.error("v2 媒体归档请求失败", error);
          send(response, 500, "Media archive request failed");
        }
      }
      return true;
    },
    close() {
      store?.close();
      if (temporary) rmSync(temporary, { recursive: true, force: true });
    },
  };
}
