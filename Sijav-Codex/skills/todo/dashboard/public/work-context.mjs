const facets = ['areas', 'types', 'topics'];
// Only these stored columns classify work. Nothing is derived from a task's
// text, so a board without one of them shows "not recorded".
export const recordedFields = {
  areas: ['area', 'work_area'],
  types: ['type', 'work_type', 'category'],
  topics: ['tags', 'domains'],
};
/** Which facets this board can record at all, from its task table's columns. */
export function facetSupport(columns = []) {
  return Object.fromEntries(facets.map(facet => [facet, recordedFields[facet].filter(field => columns.includes(field))]));
}

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

/**
 * Read-only classification from the task's recorded fields alone. Each label
 * carries its evidence: the field and the exact value recorded there.
 */
export function classifyWork(task) {
  const raw = task?.raw && typeof task.raw === 'object' ? task.raw : {};
  const result = { areas: [], types: [], topics: [] };
  for (const facet of facets) {
    const labels = new Map();
    for (const field of recordedFields[facet]) {
      const value = raw[field];
      const text = typeof value === 'string' ? value : Array.isArray(value) ? JSON.stringify(value) : String(value);
      for (const label of storedValues(value)) {
        const key = label.toLowerCase();
        if (!labels.has(key)) labels.set(key, { label, origin: 'recorded', evidence: [] });
        const entry = labels.get(key);
        if (!entry.evidence.some(item => item.field === field && item.text === text)) entry.evidence.push({ field, text });
      }
    }
    result[facet] = [...labels.values()];
  }
  return result;
}
