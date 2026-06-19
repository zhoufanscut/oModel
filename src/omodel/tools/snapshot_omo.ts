// Run: bun run src/omodel/tools/snapshot_omo.ts <omo-src> > src/omodel/data/omo-suggestions.json
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const omo = process.argv[2] ?? process.env.OMO_SRC ?? `${process.env.HOME}/source/oh-my-openagent`;
const core = join(omo, "packages/model-core/src");

const { HEURISTIC_MODEL_FAMILY_REGISTRY } = await import(join(core, "model-capability-heuristics"));
const { AGENT_MODEL_REQUIREMENTS }        = await import(join(core, "agent-model-requirements"));
const { CATEGORY_MODEL_REQUIREMENTS }     = await import(join(core, "category-model-requirements"));
const { KNOWN_VARIANTS }                  = await import(join(core, "known-variants"));

const reqOut = (r: any) => ({
  fallbackChain: r.fallbackChain.map((e: any) => ({
    providers: e.providers ?? [],
    model: e.model,
    ...(e.variant ? { variant: e.variant } : {}),
  })),
  ...(r.variant ? { variant: r.variant } : {}),
  requiresProvider: r.requiresProvider ?? [],
  requiresModel: r.requiresModel ?? "",
  requiresAnyModel: r.requiresAnyModel ?? false,
});
const mapReqs = (o: Record<string, any>) =>
  Object.fromEntries(Object.entries(o).map(([k, v]) => [k, reqOut(v)]));

const families = HEURISTIC_MODEL_FAMILY_REGISTRY.map((d: any) => ({
  family: d.family,
  pattern: d.pattern ? d.pattern.source : null,   // RegExp → string (re.compile at load)
  includes: d.includes ?? [],
  variants: d.variants ?? [],
  reasoningEfforts: d.reasoningEfforts ?? [],
  reasoningEffortAliases: d.reasoningEffortAliases ?? {},
  supportsThinking: d.supportsThinking ?? false,
}));

let omoVersion = "", omoCommit = "";
try { omoVersion = JSON.parse(readFileSync(join(omo, "package.json"), "utf8")).version ?? ""; } catch {}
try { omoCommit = execSync(`git -C "${omo}" rev-parse HEAD`, { encoding: "utf8" }).trim(); } catch {}

console.log(JSON.stringify({
  meta: { omoVersion, omoCommit, generatedAt: new Date().toISOString() },
  agents: mapReqs(AGENT_MODEL_REQUIREMENTS),
  categories: mapReqs(CATEGORY_MODEL_REQUIREMENTS),
  families,
  knownVariants: [...KNOWN_VARIANTS],   // Set|array → array
}, null, 2));
