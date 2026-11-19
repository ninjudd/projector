import assert from "node:assert/strict";
import { test } from "node:test";
import { RateLimit } from "../src/limit.js";

const HOUR = 60 * 60 * 1000;

test("a device silent for an hour is forgotten, so a long-lived instance's memory tracks only recent senders", () => {
  let now = 0;
  const limit = new RateLimit(60, HOUR, () => now);
  for (let device = 0; device < 100; device++) limit.take(`device-${device}`);
  assert.equal(limit["sent"].size, 100);
  now = HOUR;
  limit.take("recent");
  assert.deepEqual([...limit["sent"].keys()], ["recent"]);
});
