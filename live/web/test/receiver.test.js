import test from "node:test";
import assert from "node:assert/strict";
import clockReset from "../../../schemas/v2/examples/clock-reset.json" with { type: "json" };
import { connectFrames } from "../src/receiver.js";

test("时钟重置后接受新周期的零号帧并忽略迟到的旧帧", (t) => {
  const socket = { close() {} };
  const webSocket = Object.getOwnPropertyDescriptor(globalThis, "WebSocket");
  Object.defineProperty(globalThis, "WebSocket", {
    configurable: true,
    value: function () {
      return socket;
    },
  });
  t.after(() => {
    if (webSocket) Object.defineProperty(globalThis, "WebSocket", webSocket);
    else delete globalThis.WebSocket;
  });

  let now = 1000;
  t.mock.method(performance, "now", () => now);
  const [beforeReset, afterReset, lateOldFrame] = clockReset;
  const receiver = connectFrames(
    `ws://localhost/v2/matches/${beforeReset.matchId}/live`,
    () => {},
  );
  t.after(() => receiver.stop());
  socket.onopen();

  socket.onmessage({ data: JSON.stringify(beforeReset) });
  const before = receiver.read(now);
  assert.equal(before.epoch, beforeReset.streamEpoch);
  assert.equal(before.seq, beforeReset.seq);

  now += 50;
  socket.onmessage({ data: JSON.stringify(afterReset) });
  // 仍在插值缓冲内，读取时应立即得到新周期的画面。
  const after = receiver.read(now);
  assert.equal(after.epoch, afterReset.streamEpoch);
  assert.equal(after.seq, 0);
  assert.equal(after.t, 25.1);

  now += 50;
  socket.onmessage({ data: JSON.stringify(lateOldFrame) });
  assert.deepEqual(receiver.read(now + 100), after);
});
