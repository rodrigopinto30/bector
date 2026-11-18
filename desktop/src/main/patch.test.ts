import { describe, expect, it } from "vitest"

import { filesOf, isPatchId, patchId, readPatch } from "./patch"

const FIX = JSON.stringify({
  edits: [{ path: "app.py", old: "return a - b", new: "return a + b" }],
  description: "sumar must add",
})

describe("reading a saved fix", () => {
  it("gives its description and each change, as the system saves it", () => {
    expect(readPatch(FIX)).toEqual({
      description: "sumar must add",
      edits: [{ path: "app.py", old: "return a - b", new: "return a + b" }],
    })
  })

  it("does not need a description", () => {
    const text = JSON.stringify({ edits: [{ path: "a.py", old: "", new: "x = 1\n" }] })
    expect(readPatch(text)).toEqual({
      description: "",
      edits: [{ path: "a.py", old: "", new: "x = 1\n" }],
    })
  })

  it("ignores what it does not show", () => {
    const text = JSON.stringify({
      edits: [{ path: "a.py", old: "x", new: "y", extra: 1 }],
      more: 2,
    })
    expect(readPatch(text)?.edits).toEqual([{ path: "a.py", old: "x", new: "y" }])
  })

  it.each([
    ["not JSON", "{"],
    ["an array", "[]"],
    ["null", "null"],
    ["no edits", "{}"],
    ["edits that is not a list", '{"edits": "x"}'],
    ["an empty list of edits", '{"edits": []}'],
    ["an edit that is not an object", '{"edits": [1]}'],
    ["an edit without a path", '{"edits": [{"old": "a", "new": "b"}]}'],
    ["an empty path", '{"edits": [{"path": "", "old": "a", "new": "b"}]}'],
    [
      "a path with a null character",
      JSON.stringify({ edits: [{ path: "a\0b", old: "", new: "" }] }),
    ],
    ["an old text that is not text", '{"edits": [{"path": "a", "old": 1, "new": "b"}]}'],
    ["a new text that is not text", '{"edits": [{"path": "a", "old": "a", "new": null}]}'],
    [
      "a description that is not text",
      '{"description": 3, "edits": [{"path": "a", "old": "", "new": ""}]}',
    ],
  ])("refuses %s", (_name, text) => {
    expect(readPatch(text)).toBeNull()
  })

  it("refuses what is too big to show", () => {
    const edits = (count: number) =>
      JSON.stringify({
        edits: Array.from({ length: count }, () => ({ path: "a", old: "", new: "" })),
      })
    expect(readPatch(edits(100))).not.toBeNull()
    expect(readPatch(edits(101))).toBeNull()
    const side = (size: number) =>
      JSON.stringify({ edits: [{ path: "a", old: "x".repeat(size), new: "" }] })
    expect(readPatch(side(100_000))).not.toBeNull()
    expect(readPatch(side(100_001))).toBeNull()
    expect(readPatch("x".repeat(500_001))).toBeNull()
  })
})

describe("the id of a fix", () => {
  it("is the same for the same text and different for any other", () => {
    expect(patchId(FIX)).toBe(patchId(FIX))
    expect(patchId(FIX)).not.toBe(patchId(`${FIX}\n`))
    expect(patchId(FIX.replace("a + b", "a * b"))).not.toBe(patchId(FIX))
  })

  it("looks like what the page may give back", () => {
    expect(isPatchId(patchId(FIX))).toBe(true)
    expect(patchId("")).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
  })

  it("is only a lowercase hex of 64 characters", () => {
    expect(isPatchId("0".repeat(64))).toBe(true)
    for (const value of [
      "",
      "0".repeat(63),
      "0".repeat(65),
      "G".repeat(64),
      "A".repeat(64),
      1,
      null,
    ]) {
      expect(isPatchId(value)).toBe(false)
    }
  })
})

describe("the files of a fix", () => {
  it("names each once, in order", () => {
    const edit = (path: string) => ({ path, old: "", new: "" })
    expect(filesOf([edit("b.py"), edit("a.py"), edit("b.py")])).toEqual(["b.py", "a.py"])
  })
})
