import Ajv from "ajv";
import schema from "../../schemas/v2/media-index.schema.json" with { type: "json" };
import { serializeSceneFrame } from "./scene-protocol.mjs";

const validate = new Ajv({ ownProperties: true }).compile(schema);
const utcPattern = new RegExp(
  schema.properties.frames.items.properties.captureUtcUs.pattern,
);
const sha256Pattern = new RegExp(schema.definitions.sha256.pattern);
export const mediaMaxBytes = schema.properties.segmentBytes.maximum;
export const maxFrameDurationUs = 1_000_000;
export const maxUtcUs = 9_223_372_036_854_775_807n;

export const validSha256 = (value) =>
  typeof value === "string" && sha256Pattern.test(value);

export function parseExposure(value) {
  if (typeof value !== "string" || !utcPattern.test(value)) return null;
  const utc = BigInt(value);
  return utc <= maxUtcUs ? utc : null;
}

export function readMediaIndex(input) {
  if (!validate(input)) return null;
  const { numerator, denominator } = input.timeBase;
  const firstPts = input.frames[0].pts;
  let previousUtc = -1n;
  let previousEnd = firstPts;
  const frames = [];
  for (const { captureUtcUs, pts, durationPts } of input.frames) {
    const utc = parseExposure(captureUtcUs);
    const endPts = pts + durationPts;
    const durationUs = ((durationPts * numerator) / denominator) * 1_000_000;
    if (
      utc === null ||
      utc <= previousUtc ||
      pts < previousEnd ||
      !Number.isSafeInteger(endPts) ||
      durationUs < 1 ||
      durationUs > maxFrameDurationUs
    )
      return null;
    frames.push({
      utc,
      pts,
      durationPts,
      toleranceUs: Math.ceil(durationUs / 2),
    });
    previousUtc = utc;
    previousEnd = endPts;
  }
  const ticks = previousEnd - firstPts;
  const duration = (ticks * numerator) / denominator;
  if (!Number.isSafeInteger(ticks) || duration > 120) return null;
  return {
    frames,
    firstPts,
    duration,
    numerator,
    denominator,
    initSha256: input.initSha256,
    segmentSha256: input.segmentSha256,
    segmentBytes: input.segmentBytes,
    payload: serializeSceneFrame(input),
  };
}
