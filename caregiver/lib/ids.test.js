import assert from "node:assert/strict";
import test from "node:test";
import { APPROVAL_ID, SESSION_ID, escapeHtml } from "./ids.js";

test("approval ids reject path traversal and markup", () => {
  assert.equal(APPROVAL_ID.test("a_4383f53f99a3"), true);
  for (const bad of ["../etc/passwd", "..%2F..%2F.env", "a_<script>", "a_4383f53f99a3/../mandate", ""]) {
    assert.equal(APPROVAL_ID.test(bad), false, bad);
  }
});

test("session ids reject traversal and script tags", () => {
  assert.equal(SESSION_ID.test("s_p3_approve"), true);
  for (const bad of ["../../etc/passwd", "<script>alert(1)</script>", "a.b", ""]) {
    assert.equal(SESSION_ID.test(bad), false, bad);
  }
});

test("session html escapes script tags", () => {
  const html = escapeHtml("<script>alert(1)</script>");
  assert.equal(html.includes("<script>"), false);
  assert.equal(html.includes("&lt;script&gt;"), true);
});
