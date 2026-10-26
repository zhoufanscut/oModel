// Run: bun run src/omodel/tools/snapshot_native_auth.ts <pi-ai dir> > src/omodel/data/native-auth.json
//
// The provider → API-key env-var map OmO Native's engine checks to decide a provider is logged in
// (pi-ai `env-api-keys.js`). oModel's native catalog (catalog.load_native) reads it at runtime so a
// key set in the environment counts as a login, as it does for `omo` itself. It is not in the omo
// repo (pi-ai is an npm dependency, `@code-yeongyu/senpi-ai`), so this is a separate, manual step:
// point it at the pi-ai that `omo-ai` pins, e.g.
//   ~/.bun/install/global/node_modules/@earendil-works/pi-ai
// The non-table rules (Bedrock, Vertex, the anthropic-subscription lane) are code in catalog.py.
import { readFileSync } from "node:fs";
import { join } from "node:path";

const piAi = process.argv[2];
if (!piAi) {
  console.error("usage: snapshot_native_auth.ts <pi-ai package dir>");
  process.exit(2);
}
const pkg = JSON.parse(readFileSync(join(piAi, "package.json"), "utf8"));
const { apiKeyEnvProviders, getApiKeyEnvVars } = await import(join(piAi, "dist", "env-api-keys.js"));

// Every listed name counts, ANTHROPIC_AUTH_TOKEN included: `getEnvApiKey` skips that one, but the
// engine's available-model list does not (measured 2026-10-26, engine 2026.10.25-2: setting only
// ANTHROPIC_AUTH_TOKEN made all 17 `anthropic` models available).
const envKeys: Record<string, string[]> = {};
for (const provider of apiKeyEnvProviders()) {
  const names = getApiKeyEnvVars(provider) ?? [];
  if (names.length > 0) envKeys[provider] = names;
}

console.log(JSON.stringify({
  meta: { package: pkg.name, version: pkg.version, generatedAt: new Date().toISOString() },
  envKeys,
}, null, 2));
