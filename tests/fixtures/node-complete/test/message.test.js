import assert from "node:assert/strict";
import test from "node:test";

import { message } from "../index.js";

test("returns the fixture message", () => {
  assert.equal(message(), "complete node fixture");
});
