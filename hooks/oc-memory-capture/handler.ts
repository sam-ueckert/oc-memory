import { execSync } from "child_process";

// Path to your oc-memory CLI wrapper. Adjust if installed differently.
const MEM_CLI = process.env.OC_MEMORY_CLI || "oc-memory";

const handler = async (event: any) => {
  if (event.type !== "message" || event.action !== "sent") return;
  if (!event.context?.success) return;

  const outbound = event.context?.content;
  if (!outbound || outbound.length < 20) return;

  // Skip non-substantive responses
  const skipPatterns = ["NO_REPLY", "HEARTBEAT_OK", "HEARTBEAT_NOACTION"];
  const outTrimmed = outbound.trim();
  if (skipPatterns.some((p) => outTrimmed === p || outTrimmed.startsWith(p + "\n"))) return;

  const channel = event.context?.channelId || "unknown";
  const now = new Date();
  const dateStr = now.toISOString().split("T")[0];
  const scene = `conv-${dateStr}`;

  // Truncate very long responses
  const maxLen = 2000;
  const stored = outbound.length > maxLen
    ? outbound.substring(0, maxLen) + " [...truncated]"
    : outbound;

  const cell = `[${channel}] ${stored}`;

  try {
    const escaped = cell
      .replace(/\\/g, "\\\\")
      .replace(/"/g, '\\"')
      .replace(/\n/g, "\\n")
      .replace(/\r/g, "");

    const storeJson = JSON.stringify([{
      scene,
      cell_type: "exchange",
      salience: 0.5,
      content: cell.replace(/\n/g, " ").substring(0, 2000),
    }]);

    execSync(
      `echo '${storeJson.replace(/'/g, "'\\''")}' | ${MEM_CLI} store-stdin 2>/dev/null`,
      { timeout: 3000, encoding: "utf-8" }
    );
  } catch {
    // Silent failure
  }
};

export default handler;
