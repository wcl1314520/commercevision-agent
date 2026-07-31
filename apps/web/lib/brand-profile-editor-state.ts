type RuleCodeSource = {
  code: string;
};

type ColorNameSource = {
  name: string;
};

type ProfileKeySource = {
  profile_key: string;
};

export type BrandProfileIdentityChangeGuard =
  | "clear"
  | "discard-required"
  | "frozen";

export function brandProfileIdentityChangeGuard({
  creatingAnother,
  dirty,
  hasConflict,
  pendingCommand,
}: {
  creatingAnother: boolean;
  dirty: boolean;
  hasConflict: boolean;
  pendingCommand: boolean;
}): BrandProfileIdentityChangeGuard {
  if (pendingCommand) return "frozen";
  if (creatingAnother || dirty || hasConflict) {
    return "discard-required";
  }
  return "clear";
}

export function normalizeLineList(value: string): string[] {
  const seen = new Set<string>();
  const normalized: string[] = [];
  for (const rawLine of value.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || seen.has(line)) continue;
    seen.add(line);
    normalized.push(line);
  }
  return normalized;
}

export function splitEditableLineList(value: string): string[] {
  return value.replaceAll("\r\n", "\n").split("\n");
}

function firstUnusedLabel(
  usedValues: ReadonlySet<string>,
  labelFor: (suffix: number) => string,
): string {
  for (let suffix = 1; suffix <= usedValues.size + 1; suffix += 1) {
    const candidate = labelFor(suffix);
    if (!usedValues.has(candidate)) return candidate;
  }
  throw new Error("Unable to allocate a unique Brand Profile label");
}

export function nextUniqueRuleCode(
  rules: readonly RuleCodeSource[],
): string {
  return firstUnusedLabel(
    new Set(rules.map((rule) => rule.code)),
    (suffix) => `rule-${suffix}`,
  );
}

export function nextUniqueColorName(
  colors: readonly ColorNameSource[],
): string {
  return firstUnusedLabel(
    new Set(colors.map((color) => color.name)),
    (suffix) => `Color ${suffix}`,
  );
}

export function nextUniqueProfileKey(
  profiles: readonly ProfileKeySource[],
): string {
  const usedKeys = new Set(profiles.map((profile) => profile.profile_key));
  if (!usedKeys.has("primary")) return "primary";
  for (let suffix = 2; suffix <= usedKeys.size + 2; suffix += 1) {
    const candidate = `profile-${suffix}`;
    if (!usedKeys.has(candidate)) return candidate;
  }
  throw new Error("Unable to allocate a unique Brand Profile key");
}
