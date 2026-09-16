import { EventEmitter } from "node:events";
import { afterEach, describe, expect, it, vi } from "vitest";
import { stopChild, trackChild, waitForChild } from "../scripts/process-lifecycle.mjs";

class ChildFixture extends EventEmitter {
  pid: number | undefined = 12345;
  exitCode: number | null = null;
  signalCode: string | null = null;
  kill = vi.fn((signal: string): boolean => { void signal; return true; });
  exit(code: number | null, signal: string | null = null) {
    this.exitCode = code;
    this.signalCode = signal;
    this.emit("exit", code, signal);
  }
}

afterEach(() => vi.useRealTimers());

describe("bounded smoke-owned child supervision", () => {
  it("preserves successful completion without signalling the child", async () => {
    const child = new ChildFixture();
    const process = trackChild(child);
    const result = waitForChild(process);
    child.exit(0);
    await expect(result).resolves.toBeUndefined();
    expect(await stopChild(process)).toBe(true);
    expect(child.kill).not.toHaveBeenCalled();
  });

  it("preserves the original nonzero-exit diagnostics", async () => {
    const child = new ChildFixture();
    const process = trackChild(child, { label: "database migration", diagnostics: () => "safe bounded diagnostic" });
    const result = waitForChild(process);
    child.exit(2);
    await expect(result).rejects.toThrow("database migration exited 2: safe bounded diagnostic");
    expect(await stopChild(process)).toBe(true);
  });

  it("bounds timeout and confirms graceful termination", async () => {
    vi.useFakeTimers();
    const child = new ChildFixture();
    child.kill.mockImplementation((signal) => { child.exit(0, signal); return true; });
    const process = trackChild(child, { label: "worker", diagnostics: () => "last safe stage" });
    const result = expect(waitForChild(process, { timeoutMs: 100, graceMs: 10, killWaitMs: 10 })).rejects.toThrow("worker exceeded 100 ms; exit confirmed: last safe stage");
    await vi.advanceTimersByTimeAsync(100);
    await result;
    expect(child.kill.mock.calls).toEqual([["SIGTERM"]]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("escalates only the tracked child after bounded graceful shutdown", async () => {
    vi.useFakeTimers();
    const child = new ChildFixture();
    const unrelated = new ChildFixture();
    child.kill.mockImplementation((signal) => {
      if (signal === "SIGKILL") child.exit(null, signal);
      return true;
    });
    const process = trackChild(child);
    const result = stopChild(process, { graceMs: 10, killWaitMs: 10 });
    await vi.advanceTimersByTimeAsync(10);
    expect(await result).toBe(true);
    expect(child.kill.mock.calls).toEqual([["SIGTERM"], ["SIGKILL"]]);
    expect(unrelated.kill).not.toHaveBeenCalled();
  });

  it("retains owned data when neither signal establishes process exit", async () => {
    vi.useFakeTimers();
    const child = new ChildFixture();
    child.kill.mockImplementation(() => { throw new Error("permission denied"); });
    const result = stopChild(trackChild(child), { graceMs: 10, killWaitMs: 10 });
    await vi.advanceTimersByTimeAsync(20);
    expect(await result).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("never calls an exited worker safe when its isolated descendants are unconfirmed", async () => {
    vi.useFakeTimers();
    const child = new ChildFixture();
    child.kill.mockImplementation((signal) => {
      if (signal === "SIGKILL") child.exit(null, signal);
      return true;
    });
    const process = trackChild(child, { requireCleanExit: true });
    const result = stopChild(process, { graceMs: 10, killWaitMs: 10 });
    await vi.advanceTimersByTimeAsync(10);
    expect(await result).toBe(false);
    expect(await stopChild(process)).toBe(false);
  });

  it("treats a failed spawn as absent but does not expose its raw error text", async () => {
    const child = new ChildFixture();
    child.pid = undefined;
    const process = trackChild(child);
    child.emit("error", Object.assign(new Error("secret-containing command"), { code: "ENOENT" }));
    await expect(process.done).rejects.toThrow("could not run (ENOENT)");
    expect(await stopChild(process)).toBe(true);
    expect(child.kill).not.toHaveBeenCalled();
  });

  it("rejects invalid deadlines before signalling any child", async () => {
    const child = new ChildFixture();
    const process = trackChild(child);
    await expect(waitForChild(process, { timeoutMs: 0 })).rejects.toThrow("Invalid child deadline");
    await expect(stopChild(process, { graceMs: Infinity })).rejects.toThrow("Invalid child deadline");
    expect(child.kill).not.toHaveBeenCalled();
  });
});
