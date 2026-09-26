import assert from "node:assert/strict";
import test from "node:test";
import { setupCodeStatus } from "./passkeys.js";

test("an expired setup code is closed", () => {
  assert.equal(setupCodeStatus({ used: false, attempts: 0, expires_at: 1_000 }, 1_001), "expired");
});

test("a used setup code cannot be reused", () => {
  assert.equal(setupCodeStatus({ used: true, attempts: 0, expires_at: 9_000 }, 1_000), "used");
});

test("five failed attempts lock the setup code", () => {
  assert.equal(setupCodeStatus({ used: false, attempts: 5, expires_at: 9_000 }, 1_000), "locked");
});

test("a fresh setup code is open", () => {
  assert.equal(setupCodeStatus({ used: false, attempts: 0, expires_at: 9_000 }, 1_000), "open");
});
