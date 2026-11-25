import { readScene } from "./live-frame.js";

const maxReplayFrames = 3600;
const maxReplayBytes = 32_000_000;

export async function fetchHistory(matchId, range, signal) {
  const path = "/v2/matches/" + encodeURIComponent(matchId) + "/frames";
  const params = new URLSearchParams(range ?? {});
  const frames = [];
  let first;
  let bytes = 0;
  for (;;) {
    const response = await fetch(path + "?" + params, { signal });
    if (!response.ok) throw new Error("归档查询失败");
    bytes += Number(response.headers.get("content-length") ?? 0);
    if (bytes > maxReplayBytes) throw new RangeError("归档片段过大");
    const page = await response.json();
    first ??= page;
    for (const snapshot of page.frames) {
      const frame = readScene(snapshot);
      if (frame?.v !== 2 || frame.matchId !== matchId)
        throw new Error("归档快照无效");
      frames.push(frame);
    }
    if (frames.length > maxReplayFrames) throw new RangeError("归档片段过大");
    if (!page.nextCursor)
      return { frames, bounds: first.bounds, range: first.range };
    params.set("cursor", page.nextCursor);
  }
}

export function archiveFrameAt(frames, seconds) {
  let lo = 0;
  let hi = frames.length;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (frames[mid].t <= seconds) lo = mid + 1;
    else hi = mid;
  }
  return frames[lo - 1];
}
