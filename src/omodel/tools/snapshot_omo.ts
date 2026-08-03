// Run: bun run src/omodel/tools/snapshot_omo.ts <omo-src> > src/omodel/data/omo-suggestions.json
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const omo = process.argv[2] ?? process.env.OMO_SRC ?? `${process.env.HOME}/source/oh-my-openagent`;
const core = join(omo, "packages/model-core/src");

const { HEURISTIC_MODEL_FAMILY_REGISTRY } = await import(join(core, "model-capability-heuristics"));
const { AGENT_MODEL_REQUIREMENTS }        = await import(join(core, "agent-model-requirements"));
const { CATEGORY_MODEL_REQUIREMENTS }     = await import(join(core, "category-model-requirements"));
// omo's `2026-08-reasoning-unification` (commit 83009aed) deleted known-variants.ts and folded
// both KNOWN_VARIANTS copies into one 7-rung ladder plus an `auto` sentinel. Mirroring the
// current vocabulary drops the legacy `none` (omo now normalizes it to `off`) and `thinking`,
// and gains `off` — 8 tokens where there were 9.
const { REASONING_LEVELS, REASONING_AUTO } = await import(join(core, "reasoning-level"));

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
  knownVariants: [...REASONING_LEVELS, REASONING_AUTO],   // readonly tuple + sentinel → array
}, null, 2));
