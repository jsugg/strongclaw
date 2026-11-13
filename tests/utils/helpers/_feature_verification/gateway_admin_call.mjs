// Call one Gateway method as an operator.admin client and print the JSON result.
//
// Usage: node gateway_admin_call.mjs <openclaw-package-dir> <method> <params-json> [timeout-ms]
//
// `openclaw gateway call` always requests least-privilege scopes, and gateway commands such as
// `/acp spawn` require operator.admin on the calling client. The caller points
// OPENCLAW_STATE_DIR at a dedicated identity directory, so this client pairs as its own
// operator device on its first loopback connect instead of upgrading the CLI device.
import path from "node:path";
import { pathToFileURL } from "node:url";

const [packageDir, method, paramsJson, timeoutMs = "60000"] = process.argv.slice(2);
if (!packageDir || !method || paramsJson === undefined) {
  console.error("usage: gateway_admin_call.mjs <openclaw-package-dir> <method> <params-json> [timeout-ms]");
  process.exit(2);
}
const runtime = await import(
  pathToFileURL(path.join(packageDir, "dist", "plugin-sdk", "gateway-runtime.js")).href
);
try {
  const result = await runtime.callGatewayFromCli(
    method,
    { json: true, timeout: timeoutMs },
    JSON.parse(paramsJson),
    { scopes: ["operator.admin", "operator.write", "operator.read"], progress: false },
  );
  process.stdout.write(`${JSON.stringify(result)}\n`);
  process.exit(0);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
