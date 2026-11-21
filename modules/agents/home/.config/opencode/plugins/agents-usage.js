import { execFile } from "node:child_process";
import { homedir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const MAX_SESSIONS = 64;

/** Inject the shared usage/free-model snapshot once per OpenCode session. */
export const AgentsUsagePlugin = async ({ directory }) => {
  const snapshots = new Map();

  const remember = (key, value) => {
    if (snapshots.size >= MAX_SESSIONS) {
      const oldest = snapshots.keys().next().value;
      if (oldest !== undefined) snapshots.delete(oldest);
    }
    snapshots.set(key, value);
  };

  return {
    event: async ({ event }) => {
      if (event?.type === "session.deleted") {
        const id = event.properties?.info?.id ?? event.properties?.sessionID;
        if (id) snapshots.delete(id);
      }
    },
    "experimental.chat.system.transform": async (input, output) => {
      if (process.env.WPO_SESSION) return;
      const key = input?.sessionID || "__default__";
      let snapshot = snapshots.get(key);
      if (snapshot === undefined) {
        const script = join(homedir(), ".agents", "usage", "budget.py");
        try {
          const { stdout } = await execFileAsync(
            "python3",
            ["-I", "-B", script, "--snapshot"],
            { cwd: directory || homedir(), timeout: 15000, maxBuffer: 65536 },
          );
          snapshot = stdout.trim();
          if (snapshot) remember(key, snapshot);
        } catch {
          snapshot = "";
        }
      }
      if (snapshot && Array.isArray(output?.system)) {
        output.system.push(snapshot);
      }
    },
  };
};
