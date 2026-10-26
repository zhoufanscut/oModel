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

// OmO Native (`omo-ai`) keeps its own chains in packages/senpi-task, with its own provider ids
// (chatgpt-subscription, anthropic-subscription, zai, kimi-coding). Those files import
// `@oh-my-opencode/model-core` by package name, which only a `bun install` would resolve; rewrite
// the two specifiers to source paths as senpi-task's files load, so a sparse checkout of
// model-core + senpi-task is enough. (A runtime `onResolve` does not fire for bare specifiers
// under bun 1.4; `onLoad` does.) Every other `@oh-my-opencode/*` import there is `import type`,
// erased by bun. An omo without senpi-task (pre-5.0) gets no `native` section; oModel then has no
// native harness.
const native = await (async () => {
  const task = join(omo, "packages/senpi-task/src");
  try {
    Bun.plugin({
      name: "omo-model-core",
      setup(build: any) {
        build.onLoad({ filter: /[\\/]senpi-task[\\/]src[\\/].*\.ts$/ }, (args: any) => ({
          contents: readFileSync(args.path, "utf8")
            .replaceAll('"@oh-my-opencode/model-core/latest-models"', JSON.stringify(join(core, "latest-models.ts")))
            .replaceAll('"@oh-my-opencode/model-core"', JSON.stringify(join(core, "index.ts"))),
          loader: "ts",
        }));
      },
    });
    const { CATEGORY_FALLBACK_CHAINS } = await import(join(task, "category/fallback-chains"));
    const { AGENT_FALLBACK_CHAINS, BUILTIN_AGENT_DEFAULTS } = await import(join(task, "agents/builtin/index"));
    const chainOut = (chain: any[]) => chain.map((e: any) => ({
      providers: e.providers ?? [],
      model: e.model,
      ...(e.variant ? { variant: e.variant } : {}),
    }));
    const categories = Object.fromEntries(
      Object.entries(CATEGORY_FALLBACK_CHAINS).map(([k, v]) => [k, { fallbackChain: chainOut(v as any[]) }]),
    );
    // The ulw reviewer agents carry no chain of their own: they resolve through the categories on
    // their definition, in order (senpi-task resolve-agent-categories.ts). Their suggestion is
    // those categories' chains, concatenated.
    const agents = Object.fromEntries(BUILTIN_AGENT_DEFAULTS.map((def: any) => {
      if (AGENT_FALLBACK_CHAINS[def.name]) return [def.name, { fallbackChain: chainOut(AGENT_FALLBACK_CHAINS[def.name]) }];
      const via: string[] = def.categories ?? [];
      return [def.name, {
        fallbackChain: via.flatMap((c) => (categories[c] as any)?.fallbackChain ?? []),
        viaCategories: via,
      }];
    }));
    return { agents, categories };
  } catch (err) {
    console.error(`snapshot_omo: no native chains (${(err as Error).message})`);
    return undefined;
  }
})();

let omoVersion = "", omoCommit = "";
try { omoVersion = JSON.parse(readFileSync(join(omo, "package.json"), "utf8")).version ?? ""; } catch {}
try { omoCommit = execSync(`git -C "${omo}" rev-parse HEAD`, { encoding: "utf8" }).trim(); } catch {}

console.log(JSON.stringify({
  meta: { omoVersion, omoCommit, generatedAt: new Date().toISOString() },
  agents: mapReqs(AGENT_MODEL_REQUIREMENTS),
  categories: mapReqs(CATEGORY_MODEL_REQUIREMENTS),
  families,
  knownVariants: [...REASONING_LEVELS, REASONING_AUTO],   // readonly tuple + sentinel → array
  ...(native ? { native } : {}),
}, null, 2));
