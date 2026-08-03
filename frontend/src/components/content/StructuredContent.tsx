import React from 'react';
import { ExternalLink, Image as ImageIcon, Play, Quote, Table2, Sparkles, BarChart3 } from 'lucide-react';
import { cn } from '../../lib/utils';
import type { StructuredPayload } from '../../lib/messageContent';
import { STRUCTURED_COMPONENTS } from '../../lib/messageContent';

export function StructuredContent({ payload }: { payload: StructuredPayload }) {
  const items = normalizePayload(payload);
  if (items.length === 0) return null;

  return (
    <div className="space-y-4 my-2">
      {items.map((item, index) => (
        <StructuredItemView key={index} item={item} />
      ))}
    </div>
  );
}

function StructuredItemView({ item }: { item: unknown }) {
  if (Array.isArray(item)) {
    return <StructuredContent payload={item} />;
  }
  if (!item || typeof item !== 'object') {
    return null;
  }

  const payload = item as Record<string, unknown>;
  const component = getComponentName(payload);
  const title = getText(payload, ['title', 'heading', 'name', 'label']);
  const description = getText(payload, ['description', 'subtitle', 'summary', 'content', 'text', 'body']);
  const imageUrl = getUrl(payload, ['image', 'imageUrl', 'src', 'url', 'thumbnail', 'thumbnailUrl', 'poster']);
  const items = getArray(payload, ['items', 'cards', 'slides', 'entries', 'rows', 'points']);
  const actions = getArray(payload, ['actions']);
  const stats = getArray(payload, ['stats', 'metrics']);
  const sources = getArray(payload, ['sources', 'references']);

  if (component === 'Image' || (imageUrl && !component)) {
    return (
      <MediaCard
        icon={<ImageIcon className="w-4 h-4" />}
        title={title || 'Image'}
        description={description}
        media={<img src={imageUrl || ''} alt={title || description || 'Image'} className="w-full max-h-96 object-cover rounded-xl border border-[var(--border)] bg-black/10" />}
        footer={actions}
      />
    );
  }

  if (component === 'Video') {
    return (
      <MediaCard
        icon={<Play className="w-4 h-4" />}
        title={title || 'Video'}
        description={description}
        media={imageUrl ? (
          <video controls className="w-full max-h-96 rounded-xl border border-[var(--border)] bg-black/10">
            <source src={imageUrl} />
          </video>
        ) : null}
        footer={actions}
      />
    );
  }

  if (component === 'Quote') {
    return (
      <blockquote className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] px-4 py-3 text-gray-300">
        <div className="flex items-start gap-2">
          <Quote className="mt-0.5 w-4 h-4 text-primary flex-shrink-0" />
          <div className="min-w-0">
            <div className="text-[var(--foreground)] whitespace-pre-wrap">{description || getText(payload, ['quote']) || title}</div>
            {title && <div className="mt-2 text-xs text-gray-500">{title}</div>}
          </div>
        </div>
      </blockquote>
    );
  }

  if (component === 'Table') {
    const table = renderTable(payload);
    if (table) return table;
  }

  if (component === 'Stat') {
    return <StatGrid title={title} description={description} stats={stats.length ? stats : items} />;
  }

  if (component === 'Timeline') {
    return <TimelineView title={title} description={description} entries={items} />;
  }

  if (component === 'Gallery') {
    return <GalleryGrid title={title} description={description} entries={items.length ? items : [payload]} />;
  }

  if (component === 'Carousel') {
    return <CarouselStrip title={title} description={description} entries={items.length ? items : [payload]} />;
  }

  if (component === 'Bento' || component === 'Hero' || component === 'Card' || component === 'Table' || component === 'Stat' || component === 'Quote') {
    return (
      <GenericCard
        icon={component ? <Sparkles className="w-4 h-4" /> : <BarChart3 className="w-4 h-4" />}
        title={title || component || 'Card'}
        description={description}
        items={items}
        actions={actions}
        sources={sources}
      />
    );
  }

  if (component && STRUCTURED_COMPONENTS.has(component)) {
    return (
      <GenericCard
        icon={<Sparkles className="w-4 h-4" />}
        title={title || component}
        description={description}
        items={items}
        actions={actions}
        sources={sources}
      />
    );
  }

  const fallbackText = extractDisplayText(payload);
  if (!fallbackText) return null;
  return <div className="whitespace-pre-wrap text-[var(--foreground)]">{fallbackText}</div>;
}

function GenericCard({
  icon,
  title,
  description,
  items,
  actions,
  sources,
}: {
  icon?: React.ReactNode;
  title?: string;
  description?: string;
  items?: unknown[];
  actions?: unknown[];
  sources?: unknown[];
}) {
  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-4 shadow-sm space-y-3">
      <div className="flex items-start gap-3">
        {icon && <div className="mt-0.5 text-primary">{icon}</div>}
        <div className="min-w-0 flex-1">
          {title && <div className="text-[var(--foreground)] font-semibold">{title}</div>}
          {description && <div className="mt-1 text-sm text-gray-400 whitespace-pre-wrap">{description}</div>}
        </div>
      </div>
      {items && items.length > 0 && <ObjectList items={items} />}
      {actions && actions.length > 0 && <ObjectList items={actions} label="Actions" />}
      {sources && sources.length > 0 && <SourceList sources={sources} />}
    </div>
  );
}

function MediaCard({
  icon,
  title,
  description,
  media,
  footer,
}: {
  icon: React.ReactNode;
  title: string;
  description?: string;
  media?: React.ReactNode;
  footer?: unknown[];
}) {
  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-4 space-y-3">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 text-primary">{icon}</div>
        <div className="min-w-0 flex-1">
          <div className="font-semibold text-[var(--foreground)]">{title}</div>
          {description && <div className="mt-1 text-sm text-gray-400 whitespace-pre-wrap">{description}</div>}
        </div>
      </div>
      {media}
      {footer && footer.length > 0 && <ObjectList items={footer} label="Details" />}
    </div>
  );
}

function StatGrid({ title, description, stats }: { title?: string; description?: string; stats?: unknown[] }) {
  const normalized = (stats || []).map(item => normalizeObject(item)).filter(Boolean) as Array<Record<string, unknown>>;
  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-4 space-y-3">
      {(title || description) && (
        <div>
          {title && <div className="font-semibold text-[var(--foreground)]">{title}</div>}
          {description && <div className="mt-1 text-sm text-gray-400">{description}</div>}
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        {normalized.length > 0 ? normalized.map((stat, index) => (
          <div key={index} className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-3">
            <div className="text-xs text-gray-500 uppercase tracking-wider">{getText(stat, ['label', 'title', 'name']) || `Stat ${index + 1}`}</div>
            <div className="mt-1 text-lg font-semibold text-[var(--foreground)]">{getText(stat, ['value', 'amount', 'count']) || '—'}</div>
            {getText(stat, ['description', 'subtitle']) && <div className="mt-1 text-xs text-gray-500">{getText(stat, ['description', 'subtitle'])}</div>}
          </div>
        )) : (
          <div className="text-sm text-gray-500">No stats available</div>
        )}
      </div>
    </div>
  );
}

function TimelineView({ title, description, entries }: { title?: string; description?: string; entries?: unknown[] }) {
  const normalized = (entries || []).map(item => normalizeObject(item)).filter(Boolean) as Array<Record<string, unknown>>;
  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-4 space-y-3">
      {(title || description) && (
        <div>
          {title && <div className="font-semibold text-[var(--foreground)]">{title}</div>}
          {description && <div className="mt-1 text-sm text-gray-400">{description}</div>}
        </div>
      )}
      <div className="space-y-3">
        {normalized.length > 0 ? normalized.map((entry, index) => (
          <div key={index} className="flex gap-3">
            <div className="mt-1 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary text-xs font-semibold">
              {index + 1}
            </div>
            <div className="min-w-0 flex-1">
              <div className="font-medium text-[var(--foreground)]">{getText(entry, ['title', 'heading', 'name']) || `Step ${index + 1}`}</div>
              <div className="mt-1 text-sm text-gray-400 whitespace-pre-wrap">{getText(entry, ['description', 'summary', 'text', 'content'])}</div>
            </div>
          </div>
        )) : (
          <div className="text-sm text-gray-500">No timeline entries available</div>
        )}
      </div>
    </div>
  );
}

function GalleryGrid({ title, description, entries }: { title?: string; description?: string; entries?: unknown[] }) {
  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-4 space-y-3">
      {(title || description) && (
        <div>
          {title && <div className="font-semibold text-[var(--foreground)]">{title}</div>}
          {description && <div className="mt-1 text-sm text-gray-400">{description}</div>}
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        {entries?.length ? entries.map((entry, index) => {
          const item = normalizeObject(entry);
          const imageUrl = item ? getUrl(item, ['image', 'imageUrl', 'src', 'url', 'thumbnail', 'thumbnailUrl']) : '';
          const label = item ? getText(item, ['title', 'name', 'label']) : '';
          return (
            <div key={index} className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)]">
              {imageUrl ? (
                <img src={imageUrl} alt={label || `Gallery item ${index + 1}`} className="h-36 w-full object-cover" />
              ) : (
                <div className="flex h-36 items-center justify-center bg-black/10 text-gray-500">
                  <ImageIcon className="h-5 w-5" />
                </div>
              )}
              {(label || item) && (
                <div className="p-3 text-sm text-[var(--foreground)]">
                  {label || extractDisplayText(item)}
                </div>
              )}
            </div>
          );
        }) : <div className="text-sm text-gray-500">No gallery items available</div>}
      </div>
    </div>
  );
}

function CarouselStrip({ title, description, entries }: { title?: string; description?: string; entries?: unknown[] }) {
  return (
    <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface)] p-4 space-y-3">
      {(title || description) && (
        <div>
          {title && <div className="font-semibold text-[var(--foreground)]">{title}</div>}
          {description && <div className="mt-1 text-sm text-gray-400">{description}</div>}
        </div>
      )}
      <div className="flex gap-3 overflow-x-auto pb-1">
        {entries?.length ? entries.map((entry, index) => {
          const item = normalizeObject(entry);
          const imageUrl = item ? getUrl(item, ['image', 'imageUrl', 'src', 'url', 'thumbnail', 'thumbnailUrl']) : '';
          const label = item ? getText(item, ['title', 'name', 'label']) : '';
          const text = item ? getText(item, ['description', 'summary', 'text', 'content']) : '';
          return (
            <div key={index} className="min-w-[240px] max-w-[280px] rounded-xl border border-[var(--border)] bg-[var(--card)] p-3 shadow-sm">
              {imageUrl && <img src={imageUrl} alt={label || `Carousel item ${index + 1}`} className="mb-3 h-32 w-full rounded-lg object-cover" />}
              {label && <div className="font-medium text-[var(--foreground)]">{label}</div>}
              {text && <div className="mt-1 text-sm text-gray-400 whitespace-pre-wrap">{text}</div>}
              {!label && !text && <div className="text-sm text-gray-500">{extractDisplayText(item) || `Item ${index + 1}`}</div>}
            </div>
          );
        }) : <div className="text-sm text-gray-500">No carousel items available</div>}
      </div>
    </div>
  );
}

function ObjectList({ items, label }: { items: unknown[]; label?: string }) {
  const normalized = items.map(item => normalizeObject(item)).filter(Boolean) as Array<Record<string, unknown>>;
  return (
    <div className="space-y-2">
      {label && <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">{label}</div>}
      <div className="space-y-2">
        {normalized.map((item, index) => {
          const title = getText(item, ['title', 'name', 'label']) || getText(item, ['url', 'href']);
          const text = getText(item, ['description', 'summary', 'text', 'content', 'value']);
          const url = getUrl(item, ['url', 'href', 'link']);
          if (url || title || text) {
            return (
              <div key={index} className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-3 text-sm">
                {title && <div className="font-medium text-[var(--foreground)]">{title}</div>}
                {text && <div className="mt-1 text-gray-400 whitespace-pre-wrap">{text}</div>}
                {url && (
                  <a className="mt-2 inline-flex items-center gap-1.5 text-primary text-xs underline-offset-2 hover:underline" href={url} target="_blank" rel="noreferrer">
                    Open link <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>
            );
          }
          return <div key={index} className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-3 text-sm text-gray-400">{extractDisplayText(item) || `Item ${index + 1}`}</div>;
        })}
      </div>
    </div>
  );
}

function SourceList({ sources }: { sources: unknown[] }) {
  const normalized = sources.map(item => normalizeObject(item)).filter(Boolean) as Array<Record<string, unknown>>;
  return (
    <div className="space-y-2">
      <div className="text-xs font-semibold uppercase tracking-wider text-gray-500">Sources</div>
      <div className="space-y-2">
        {normalized.map((source, index) => {
          const title = getText(source, ['title', 'name', 'label']) || `Source ${index + 1}`;
          const url = getUrl(source, ['url', 'href', 'link']);
          const text = getText(source, ['description', 'summary', 'text']);
          return (
            <a
              key={index}
              className={cn('block rounded-xl border border-[var(--border)] bg-[var(--card)] p-3 text-sm transition-colors hover:bg-white/5', !url && 'pointer-events-none')}
              href={url || undefined}
              target={url ? '_blank' : undefined}
              rel={url ? 'noreferrer' : undefined}
            >
              <div className="font-medium text-[var(--foreground)]">{title}</div>
              {text && <div className="mt-1 text-gray-400 whitespace-pre-wrap">{text}</div>}
              {url && <div className="mt-2 text-xs text-primary break-all">{url}</div>}
            </a>
          );
        })}
      </div>
    </div>
  );
}

function renderTable(payload: Record<string, unknown>) {
  const headers = getArrayOfStrings(payload, ['headers', 'columns']);
  const rows = getArray(payload, ['rows', 'data']);
  if (!rows || rows.length === 0) return null;

  const normalizedRows = rows.map(row => normalizeRow(row)).filter(row => row.length > 0);
  if (normalizedRows.length === 0) return null;

  const effectiveHeaders = headers.length ? headers : normalizedRows[0].map((_, index) => `Column ${index + 1}`);

  return (
    <div className="overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--surface)]">
      <div className="flex items-center gap-2 border-b border-[var(--border)] px-4 py-3 text-sm font-medium text-[var(--foreground)]">
        <Table2 className="h-4 w-4 text-primary" />
        {getText(payload, ['title', 'heading', 'name']) || 'Table'}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr>
              {effectiveHeaders.map((header, index) => (
                <th key={index} className="border-b border-[var(--border)] bg-[var(--card)] px-3 py-2 font-semibold text-[var(--foreground)]">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {normalizedRows.map((row, rowIndex) => (
              <tr key={rowIndex} className="odd:bg-white/[0.02]">
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} className="border-b border-[var(--border)] px-3 py-2 align-top text-gray-300 whitespace-pre-wrap">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function getComponentName(payload: Record<string, unknown>) {
  const component = payload.component;
  return typeof component === 'string' ? component : '';
}

function getText(payload: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  }
  return '';
}

function getUrl(payload: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === 'string' && /^https?:\/\//i.test(value.trim())) return value.trim();
    if (value && typeof value === 'object') {
      const nested = normalizeObject(value);
      if (nested) {
        const nestedUrl: string = getUrl(nested, ['url', 'href', 'link', 'src']);
        if (nestedUrl) return nestedUrl;
      }
    }
  }
  return '';
}

function getArray(payload: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = payload[key];
    if (Array.isArray(value)) return value;
  }
  return [] as unknown[];
}

function getArrayOfStrings(payload: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = payload[key];
    if (Array.isArray(value)) {
      return value.filter((item): item is string => typeof item === 'string');
    }
  }
  return [] as string[];
}

function normalizePayload(payload: StructuredPayload): unknown[] {
  if (Array.isArray(payload)) return payload;
  if (!payload || typeof payload !== 'object') return [];
  const record = payload as Record<string, unknown>;
  const items = getArray(record, ['items', 'cards', 'slides', 'entries', 'rows', 'points', 'sources', 'references']);
  if (items.length > 0) return items;
  return [record];
}

function normalizeObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function normalizeRow(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(cell => extractDisplayText(cell));
  }
  const record = normalizeObject(value);
  if (!record) return [];
  const keys = ['title', 'name', 'label', 'value', 'description', 'summary', 'text', 'content'];
  const cells = keys.map(key => extractDisplayText(record[key])).filter(Boolean);
  return cells.length > 0 ? cells : Object.values(record).map(cell => extractDisplayText(cell)).filter(Boolean);
}

function extractDisplayText(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    return value.map(extractDisplayText).filter(Boolean).join(' · ').trim();
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const fields = ['title', 'heading', 'name', 'label', 'description', 'subtitle', 'summary', 'text', 'content', 'value'];
    for (const field of fields) {
      const text = extractDisplayText(record[field]);
      if (text) return text;
    }
    const nested = Object.entries(record)
      .filter(([key]) => !/^(component|props|children|actions|render_json|tool_output|metadata|sources_json|internal_objects)$/i.test(key))
      .map(([, child]) => extractDisplayText(child))
      .filter(Boolean);
    return nested.join(' · ').trim();
  }
  return String(value).trim();
}
