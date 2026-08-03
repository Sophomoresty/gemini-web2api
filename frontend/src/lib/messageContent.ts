export const STRUCTURED_COMPONENTS = new Set([
  'Bento',
  'Carousel',
  'Image',
  'Video',
  'Card',
  'Gallery',
  'Table',
  'Timeline',
  'Hero',
  'Stat',
  'Quote',
]);

export const INTERNAL_PAYLOAD_KEYS = new Set([
  'component',
  'props',
  'children',
  'actions',
  'render_json',
  'tool_output',
  'metadata',
  'sources_json',
  'internal_objects',
]);

const REAL_CODE_LANGUAGES = new Set([
  'python',
  'py',
  'javascript',
  'js',
  'typescript',
  'ts',
  'tsx',
  'jsx',
  'react',
  'html',
  'css',
  'sql',
  'bash',
  'sh',
  'zsh',
  'json',
  'yaml',
  'yml',
]);

export type StructuredPayload = unknown;

export type ContentSegment =
  | { type: 'text'; text: string }
  | { type: 'structured'; payload: StructuredPayload; raw: string };

const JSON_REQUEST_PATTERNS = [
  /\bjson\b/i,
  /\b(yaml|xml|csv|tsv|markdown)\b/i,
  /\b(return|output|generate|respond with|format as|provide|give me)\b[^\n]{0,40}\bjson\b/i,
  /\bjson\s+only\b/i,
  /\bvalid\s+json\b/i,
  /\bstructured\s+json\b/i,
  /\bapi\s+response\b/i,
  /\bschema\b/i,
];

export function shouldAllowStructuredJson(text: string): boolean {
  const normalized = text.replace(/\s+/g, ' ').trim();
  if (!normalized) return false;
  return JSON_REQUEST_PATTERNS.some(pattern => pattern.test(normalized));
}

export function sanitizeAssistantContent(raw: string, allowJson = false): string {
  if (allowJson || !raw) return raw;
  const segments = parseAssistantContent(raw, false);
  return segments
    .map(segment => (segment.type === 'text' ? segment.text : ''))
    .filter(Boolean)
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export function parseAssistantContent(raw: string, allowJson = false): ContentSegment[] {
  const normalized = (raw || '').replace(/\r\n/g, '\n');
  if (!normalized.trim()) return [{ type: 'text', text: '' }];
  if (allowJson) return [{ type: 'text', text: normalized }];

  const segments: ContentSegment[] = [];
  const lines = normalized.split('\n');
  let textBuffer: string[] = [];
  let discardedStructured = false;

  const flushText = () => {
    if (!textBuffer.length) return;
    const text = textBuffer.join('\n').replace(/\n{3,}/g, '\n\n').trim();
    if (text) segments.push({ type: 'text', text });
    textBuffer = [];
  };

  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const fenceMatch = line.match(/^\s*```([^`]*)\s*$/);
    if (fenceMatch) {
      const fenceLang = fenceMatch[1].trim().toLowerCase();
      const fenceLines: string[] = [];
      index += 1;
      while (index < lines.length) {
        const fenceLine = lines[index];
        if (/^\s*```\s*$/.test(fenceLine)) {
          index += 1;
          break;
        }
        fenceLines.push(fenceLine);
        index += 1;
      }

      const fenceText = fenceLines.join('\n').trim();
      const parsed = tryParseJson(fenceText);
      if (parsed && isStructuredPayload(parsed)) {
        flushText();
        discardedStructured = true;
        continue;
      }

      if (parsed) {
        const extracted = extractTextFromValue(parsed);
        if (extracted) {
          flushText();
          segments.push({ type: 'text', text: extracted });
        } else {
          discardedStructured = true;
        }
        continue;
      }

      if (REAL_CODE_LANGUAGES.has(fenceLang)) {
        textBuffer.push(line, ...fenceLines, '```');
      } else {
        const extracted = fenceText.replace(/^#+\s*/gm, '').trim();
        if (extracted) {
          flushText();
          textBuffer.push(extracted);
        }
      }
      continue;
    }

    if (shouldStartJsonBlock(line)) {
      const block = collectJsonBlock(lines, index);
      if (block) {
        const parsed = tryParseJson(block.text);
        if (parsed && isStructuredPayload(parsed)) {
          flushText();
          discardedStructured = true;
          index += block.linesConsumed;
          continue;
        }
        if (parsed) {
          flushText();
          const extracted = extractTextFromValue(parsed);
          if (extracted) segments.push({ type: 'text', text: extracted });
          else discardedStructured = true;
          index += block.linesConsumed;
          continue;
        }
      }
    }

    textBuffer.push(line);
    index += 1;
  }

  flushText();

  if (segments.length) return segments;
  if (discardedStructured) return [{ type: 'text', text: '' }];
  return [{ type: 'text', text: normalized }];
}

function shouldStartJsonBlock(line: string): boolean {
  const trimmed = line.trimStart();
  if (!trimmed) return false;
  return trimmed.startsWith('{') || trimmed.startsWith('[');
}

function collectJsonBlock(lines: string[], startIndex: number): { text: string; linesConsumed: number } | null {
  let candidate = '';
  let depth = 0;
  let inString = false;
  let escaped = false;
  let started = false;

  for (let index = startIndex; index < lines.length; index++) {
    const line = lines[index];
    candidate += (candidate ? '\n' : '') + line;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (!started) {
        if (ch === '{' || ch === '[') {
          started = true;
          depth = 1;
        }
        continue;
      }
      if (inString) {
        if (escaped) {
          escaped = false;
          continue;
        }
        if (ch === '\\') {
          escaped = true;
          continue;
        }
        if (ch === '"') inString = false;
        continue;
      }
      if (ch === '"') {
        inString = true;
      } else if (ch === '{' || ch === '[') {
        depth++;
      } else if (ch === '}' || ch === ']') {
        depth--;
        if (started && depth === 0) {
          return { text: candidate, linesConsumed: index - startIndex + 1 };
        }
      }
    }
  }

  return null;
}

function tryParseJson(text: string): unknown | null {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function extractTextFromValue(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (Array.isArray(value)) {
    return value
      .flatMap(item => extractTextSegments(item))
      .map(part => part.trim())
      .filter(Boolean)
      .join('\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }
  if (value && typeof value === 'object') {
    return extractTextFromPayload(value);
  }
  return String(value ?? '').trim();
}

function isStructuredPayload(value: unknown): value is StructuredPayload {
  if (Array.isArray(value)) {
    return value.some(item => isStructuredPayload(item));
  }
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  if (typeof record.component === 'string' && STRUCTURED_COMPONENTS.has(record.component)) return true;
  return Object.keys(record).some(key => INTERNAL_PAYLOAD_KEYS.has(key));
}

function extractTextFromPayload(payload: unknown): string {
  const pieces = extractTextSegments(payload).map(part => part.trim()).filter(Boolean);
  return pieces.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function extractTextSegments(value: unknown, depth = 0): string[] {
  if (depth > 6 || value == null) return [];
  if (typeof value === 'string') return [value];
  if (typeof value === 'number' || typeof value === 'boolean') return [String(value)];
  if (Array.isArray(value)) {
    return value.flatMap(item => extractTextSegments(item, depth + 1));
  }
  if (typeof value !== 'object') return [];

  const record = value as Record<string, unknown>;
  const parts: string[] = [];
  for (const [key, child] of Object.entries(record)) {
    if (INTERNAL_PAYLOAD_KEYS.has(key)) continue;
    if (matchesResourceKey(key)) continue;
    parts.push(...extractTextSegments(child, depth + 1));
  }
  return parts;
}

function matchesResourceKey(key: string): boolean {
  return /^(url|src|href|image|imageUrl|video|videoUrl|thumbnail|thumbnailUrl|poster|link)$/i.test(key);
}
