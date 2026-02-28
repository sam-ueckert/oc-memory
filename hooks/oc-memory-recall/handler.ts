import { execSync } from "child_process";

// Path to your oc-memory CLI wrapper. Adjust if installed differently.
const MEM_CLI = process.env.OC_MEMORY_CLI || "oc-memory";

const handler = async (event: any) => {
  if (event.type !== "message" || event.action !== "received") return;

  const content = event.context?.content;
  if (!content || content.length < 10) return;

  // Skip heartbeat polls
  if (content.includes("HEARTBEAT") || content.includes("Read HEARTBEAT.md")) return;

  try {
    const query = content
      .replace(/[^\w\s]/g, " ")
      .substring(0, 200)
      .trim();

    if (!query || query.split(/\s+/).length < 2) return;

    const result = execSync(
      `${MEM_CLI} search "${query.replace(/"/g, '\\"')}" 2>/dev/null`,
      { timeout: 3000, encoding: "utf-8" }
    ).trim();

    if (!result || result.includes("No results") || result.length < 20) return;

    const truncated = result.length > 1500
      ? result.substring(0, 1500) + "\n... (truncated)"
      : result;

    event.messages.push(
      `[oc-memory Recall]\n${truncated}`
    );
  } catch {
    // Silent failure — don't break the conversation
  }
};

export default handler;
