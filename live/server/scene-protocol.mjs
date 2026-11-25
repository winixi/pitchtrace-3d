import Ajv from "ajv";
import schema from "../../schemas/v2/scene-frame.schema.json" with { type: "json" };
import { validBones } from "../web/src/protocol.js";

const validate = new Ajv({ ownProperties: true }).compile(schema);
const id = new RegExp(schema.definitions.id.pattern);

export const validSceneId = (value) =>
  typeof value === "string" && id.test(value);

export function sceneRoute(pathname, endpoint) {
  const route = /^\/v2\/matches\/([^/]+)\/(frames|live)$/.exec(pathname);
  return route?.[2] === endpoint && validSceneId(route[1]) ? route[1] : null;
}

export function validSceneFrame(frame, matchId) {
  if (!validate(frame) || frame.matchId !== matchId) return false;
  const tracks = new Set(frame.players.map(({ trackId }) => trackId));
  return (
    tracks.size === frame.players.length &&
    frame.players.every(({ bones }) => validBones(bones))
  );
}

// 对象字段顺序与空白不影响重试，数组顺序仍属于快照内容。
export function serializeSceneFrame(frame) {
  return JSON.stringify(frame, (_, value) => {
    if (typeof value === "number" && !Number.isFinite(value))
      throw new TypeError("场景快照包含非有限数值");
    if (value === null || typeof value !== "object" || Array.isArray(value))
      return value;
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, value[key]]),
    );
  });
}
