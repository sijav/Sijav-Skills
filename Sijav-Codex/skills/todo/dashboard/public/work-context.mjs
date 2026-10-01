const facets = ['areas', 'types', 'topics'];
// Only these stored columns classify work. The generic dashboard ships no
// derivation rules, so a board without one of them shows "not recorded".
export const recordedFields = {
  areas: ['area', 'work_area'],
  types: ['type', 'work_type', 'category'],
  topics: ['tags', 'domains'],
};
/** Which facets this board can record at all, from its task table's columns. */
export function facetSupport(columns = []) {
  return Object.fromEntries(facets.map(facet => [facet, recordedFields[facet].filter(field => columns.includes(field))]));
}
const textFields = new Set(['title', 'story', 'descr', 'why', 'exit_cmd', 'exit_cond']);

function storedValues(value) {
  if (Array.isArray(value)) return value.flatMap(storedValues);
  if (typeof value === 'number') return Number.isFinite(value) ? [String(value)] : [];
  if (typeof value !== 'string' || !value.trim()) return [];
  const trimmed = value.trim();
  // Some boards serialize a list in a TEXT column. Parse only an explicit
  // JSON list; ordinary text stays one recorded value, without guessing its
  // delimiter or rewriting the board's vocabulary.
  if (trimmed.startsWith('[')) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) return parsed.flatMap(storedValues);
    } catch { /* It is still the original recorded text. */ }
  }
  return [trimmed];
}

function addLabel(labels, label, origin, field, text) {
  const key = label.toLowerCase();
  let entry = labels.get(key);
  if (!entry) {
    entry = { label, origin, evidence: [] };
    labels.set(key, entry);
  }
  if (origin === 'recorded') entry.origin = 'recorded';
  if (!entry.evidence.some(item => item.field === field && item.text === text)) {
    entry.evidence.push({ field, text });
  }
}

function compilePattern(pattern) {
  if (pattern instanceof RegExp) {
    // Every rule is case insensitive and global so each exact match can be
    // shown as evidence. Other flags retain the caller's intended matching.
    return new RegExp(pattern.source, [...new Set(pattern.flags + 'ig')].join(''));
  }
  if (typeof pattern !== 'string') throw new TypeError('Pattern must be a string or RegExp.');
  return new RegExp(pattern, 'ig');
}

/**
 * Read-only classification. Recorded values win independently for each facet;
 * otherwise only configured text rules can provide a derived label. Evidence
 * contains the exact recorded value or regex match, never a guessed context.
 */
export function classifyWork(task, definitions = {}) {
  const raw = task?.raw && typeof task.raw === 'object' ? task.raw : {};
  const result = { areas: [], types: [], topics: [] };
  const errors = [];

  for (const facet of facets) {
    const labels = new Map();
    for (const field of recordedFields[facet]) {
      const value = raw[field];
      for (const label of storedValues(value)) {
        const text = typeof value === 'string' ? value : Array.isArray(value) ? JSON.stringify(value) : String(value);
        addLabel(labels, label, 'recorded', field, text);
      }
    }

    if (!labels.size) {
      const rules = definitions?.[facet];
      if (rules != null && !Array.isArray(rules)) {
        errors.push({ facet, reason: 'Facet rules must be an array.' });
      }
      for (const [ruleIndex, rule] of (Array.isArray(rules) ? rules : []).entries()) {
        if (!rule || typeof rule.label !== 'string' || !rule.label.trim()) {
          errors.push({ facet, ruleIndex, reason: 'Rule needs a nonempty text label.' });
          continue;
        }
        if (!Array.isArray(rule.fields) || !Array.isArray(rule.patterns)) {
          errors.push({ facet, ruleIndex, reason: 'Rule fields and patterns must be arrays.' });
          continue;
        }
        const fields = rule.fields.filter(field => textFields.has(field));
        for (const field of rule.fields.filter(field => !textFields.has(field))) {
          errors.push({ facet, ruleIndex, field, reason: 'Field is not a supported source text field.' });
        }
        const patterns = [];
        for (const [patternIndex, pattern] of rule.patterns.entries()) {
          try { patterns.push(compilePattern(pattern)); }
          catch (error) {
            errors.push({ facet, ruleIndex, patternIndex, reason: error.message });
          }
        }
        for (const field of fields) {
          // raw wins even when null: never replace a recorded null with a
          // normalized display field. Direct task fields support plain rows.
          const value = Object.hasOwn(raw, field) ? raw[field] : task?.[field];
          if (typeof value !== 'string' || !value) continue;
          for (const pattern of patterns) {
            pattern.lastIndex = 0;
            for (const match of value.matchAll(pattern)) {
              if (match[0]) addLabel(labels, rule.label.trim(), 'derived', field, match[0]);
            }
          }
        }
      }
    }
    result[facet] = [...labels.values()];
  }

  if (errors.length) result.errors = errors;
  return result;
}
