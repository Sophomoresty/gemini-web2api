import React, { useEffect, useRef, useState, useCallback, useMemo, memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';
import {
  Copy, Check, RefreshCw, Edit2, Trash2, Bookmark, Share2, ThumbsUp, ThumbsDown,
  Pin, Brain, ChevronDown, ChevronRight
} from 'lucide-react';
import { clsx } from 'clsx';
import type { Message } from '../types';
import { copyToClipboard, formatTimestamp, formatTime } from '../lib/utils';
import { parseAssistantContent } from '../lib/messageContent';
import { useApp } from '../store/AppContext';

interface ChatWindowProps {
  messages: Message[];
  isGenerating: boolean;
  onRegenerate: (messageId: string) => void;
  onRetry: (messageId: string) => void;
  onEditSubmit: (messageId: string, newContent: string) => void;
  onDelete: (messageId: string) => void;
  chatId: string;
}

export const ChatWindow = memo(function ChatWindow({
  messages,
  isGenerating,
  onRegenerate,
  onRetry,
  onEditSubmit,
  onDelete,
  chatId,
}: ChatWindowProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);

  const onScroll = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    autoScrollRef.current = scrollHeight - scrollTop - clientHeight < 150;
  }, []);

  useEffect(() => {
    if (autoScrollRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const scrollToBottom = () => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  };

  return (
    <div ref={scrollRef} onScroll={onScroll} className="relative flex-1 overflow-y-auto px-4 py-6 scrollbar-hide">
      <div className="max-w-4xl mx-auto space-y-6 pb-4">
        {messages.length === 0 && <EmptyState />}
        {messages.map((msg, idx) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            isLast={idx === messages.length - 1}
            isGenerating={isGenerating && idx === messages.length - 1 && msg.role === 'assistant'}
            onRegenerate={() => onRegenerate(msg.id)}
            onRetry={() => onRetry(msg.id)}
            onEditSubmit={(newContent) => onEditSubmit(msg.id, newContent)}
            onDelete={() => onDelete(msg.id)}
            chatId={chatId}
          />
        ))}
        <div ref={bottomRef} />
      </div>

      {!autoScrollRef.current && messages.length > 0 && (
        <button
          onClick={scrollToBottom}
          className="fixed bottom-28 left-1/2 -translate-x-1/2 z-10 px-3 py-2 bg-[var(--card)] border border-[var(--border)] rounded-full shadow-lg hover:bg-white/5 transition-colors text-xs text-gray-300 flex items-center gap-1.5"
        >
          <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M19 12l-7 7-7-7" /></svg>
          Scroll to bottom
        </button>
      )}
    </div>
  );
});

function EmptyState() {
  return (
    <div className="h-60 flex flex-col items-center justify-center text-center opacity-50">
      <div className="w-12 h-12 rounded-xl bg-primary/20 flex items-center justify-center mb-3 ring-1 ring-primary/30">
        <span className="text-xl font-bold text-primary">O</span>
      </div>
      <p className="text-sm text-gray-400">No messages yet. Start a conversation!</p>
    </div>
  );
}

interface MessageBubbleProps {
  message: Message;
  isLast: boolean;
  isGenerating: boolean;
  onRegenerate: () => void;
  onRetry: () => void;
  onEditSubmit: (content: string) => void;
  onDelete: () => void;
  chatId: string;
}

function MessageBubble({ message, isLast, isGenerating, onRegenerate, onRetry, onEditSubmit, onDelete, chatId }: MessageBubbleProps) {
  const { notify, pinChat } = useApp();
  const [showActions, setShowActions] = useState(false);
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const isExplicitJson = message.role === 'assistant' && message.renderMode === 'json';
  const contentSegments = useMemo(() => {
    if (message.role !== 'assistant' || isExplicitJson) {
      return [{ type: 'text' as const, text: message.content }];
    }
    return parseAssistantContent(message.content);
  }, [message.content, message.role, isExplicitJson]);
  const visibleContent = useMemo(() => {
    if (message.role !== 'assistant' || isExplicitJson) return message.content;
    return contentSegments
      .filter((segment): segment is Extract<typeof segment, { type: 'text' }> => segment.type === 'text' && Boolean(segment.text.trim()))
      .map(segment => segment.text.trim())
      .join('\n\n');
  }, [contentSegments, message.content, message.role, isExplicitJson]);
  const [editInput, setEditInput] = useState(visibleContent);
  const [showReasoning, setShowReasoning] = useState(false);
  const [bookmarked, setBookmarked] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);

  const onCopy = async () => {
    const ok = await copyToClipboard(visibleContent);
    if (ok) {
      setCopied(true);
      notify('success', 'Copied to clipboard');
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const onContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY });
  };

  const onShare = async () => {
    try {
      if (navigator.share) {
        await navigator.share({ title: 'OmniAI Chat', text: visibleContent });
      } else {
        await copyToClipboard(visibleContent);
        notify('info', 'Link copied to clipboard');
      }
    } catch { /* ignore */ }
  };

  const reasoningContent = (message as any).reasoning || '';

  return (
    <div
      className={clsx('flex gap-4 w-full group/msg relative', message.role === 'user' ? 'justify-end' : 'justify-start')}
      onMouseEnter={() => setShowActions(true)}
      onMouseLeave={() => setShowActions(false)}
      onContextMenu={onContextMenu}
    >
      {message.role === 'assistant' && (
        <div className="w-8 h-8 rounded-lg bg-primary flex-shrink-0 flex items-center justify-center text-white font-bold text-sm shadow-md">
          O
        </div>
      )}

      <div className="max-w-[85%] min-w-0 flex flex-col gap-1">
        <div className={clsx(
          'flex items-center gap-2 text-xs text-gray-500 px-1',
          message.role === 'user' ? 'justify-end' : 'justify-start'
        )}>
          <span>{message.role === 'assistant' ? 'OmniAI' : 'You'}</span>
          <span>·</span>
          <span title={new Date(message.timestamp).toLocaleString()}>{formatTimestamp(message.timestamp)} · {formatTime(message.timestamp)}</span>
          {message.edited && <span className="text-gray-600">(edited)</span>}
        </div>

        <div className={clsx(
          'rounded-2xl px-5 py-4 shadow-sm',
          message.role === 'user'
            ? 'bg-primary/10 border border-primary/20 text-[var(--foreground)]'
            : clsx('text-[var(--foreground)]', message.error && 'bg-danger/5 border border-danger/20 rounded-2xl')
        )}>
          {reasoningContent && (
            <div className="mb-3">
              <button
                onClick={() => setShowReasoning(s => !s)}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[var(--surface)] border border-[var(--border)] text-xs font-medium text-gray-400 hover:text-gray-200 transition-colors"
              >
                <Brain className="w-3 h-3 text-accent" />
                <span>Reasoning</span>
                {showReasoning ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
              </button>
              {showReasoning && (
                <div className="mt-2 p-3 rounded-xl bg-[var(--surface)] border border-[var(--border)] text-sm text-gray-400 whitespace-pre-wrap animate-in fade-in slide-in-from-top-1 duration-200">
                  {reasoningContent}
                </div>
              )}
            </div>
          )}

          {editing ? (
            <div className="space-y-3">
              <textarea
                value={editInput}
                onChange={e => setEditInput(e.target.value)}
                autoFocus
                className="w-full min-h-[100px] p-3 bg-[var(--surface)] border border-[var(--border)] rounded-xl text-[var(--foreground)] text-sm font-mono outline-none focus:ring-2 focus:ring-primary/30"
              />
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => { setEditing(false); setEditInput(visibleContent); }}
                  className="px-3 py-1.5 text-xs rounded-lg text-gray-400 hover:text-white bg-white/5 hover:bg-white/10 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={() => {
                    if (editInput.trim()) {
                      onEditSubmit(editInput.trim());
                      setEditing(false);
                    }
                  }}
                  className="px-3 py-1.5 text-xs rounded-lg bg-primary text-white hover:bg-primary/90 transition-colors"
                >
                  Save
                </button>
              </div>
            </div>
          ) : (
            <div className="prose prose-invert max-w-none [&_table]:border-collapse [&_th]:bg-[var(--surface)] [&_th]:border [&_th]:border-[var(--border)] [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_td]:border [&_td]:border-[var(--border)] [&_td]:px-3 [&_td]:py-2 [&_a]:text-primary [&_a]:underline-offset-2 [&_blockquote]:border-l-primary [&_blockquote]:pl-4 [&_blockquote]:text-gray-400 [&_ul]:list-disc [&_ol]:list-decimal [&_li]:ml-4 [&_li]:pl-1 [&_h1]:text-2xl [&_h1]:font-bold [&_h2]:text-xl [&_h2]:font-semibold [&_h3]:text-lg [&_h3]:font-semibold [&_hr]:border-[var(--border)]">
              {isExplicitJson ? (
                <pre className="overflow-x-auto rounded-xl border border-[var(--border)] bg-[#0d0d12] p-4 text-sm text-gray-200 whitespace-pre-wrap break-words">
                  {message.content}
                </pre>
              ) : (
                visibleContent ? (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm, remarkMath]}
                    rehypePlugins={[rehypeKatex]}
                    components={{
                      code({ inline, className, children, ...props }: any) {
                        const match = /language-(\w+)/.exec(className || '');
                        return !inline && match ? (
                          <div className="relative group/code my-4">
                            <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover/code:opacity-100 transition-opacity">
                              <button
                                onClick={() => copyToClipboard(String(children).replace(/\n$/, '')).then(() => notify('success', 'Code copied'))}
                                className="p-1.5 rounded-md bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white transition-colors"
                                aria-label="Copy code"
                              >
                                <Copy className="w-3.5 h-3.5" />
                              </button>
                            </div>
                            <SyntaxHighlighter
                              {...props}
                              children={String(children).replace(/\n$/, '')}
                              style={vscDarkPlus as any}
                              language={match[1]}
                              PreTag="div"
                              showLineNumbers
                              wrapLines
                              customStyle={{
                                margin: 0,
                                padding: '1.25rem 1rem 1rem',
                                background: '#0d0d12',
                                borderRadius: '0.75rem',
                                border: '1px solid rgba(255,255,255,0.08)',
                                fontSize: '0.85rem',
                              }}
                              lineNumberStyle={{ color: 'rgba(255,255,255,0.25)', paddingRight: '1rem', minWidth: '2.2em', textAlign: 'right', userSelect: 'none' }}
                            />
                          </div>
                        ) : (
                          <code
                            {...props}
                            className={clsx(className, 'bg-[var(--surface)] border border-[var(--border)] px-1.5 py-0.5 rounded-md text-primary text-[0.88em]')}
                          >
                            {children}
                          </code>
                        );
                      },
                      p(props: any) {
                        return <p className="leading-relaxed whitespace-pre-wrap">{props.children}</p>;
                      },
                    }}
                  >
                    {visibleContent}
                  </ReactMarkdown>
                ) : null
              )}
              {isGenerating && <TypingIndicator />}
            </div>
          )}

          {message.tokenUsage && (
            <div className="mt-3 pt-2 border-t border-[var(--border)] flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-gray-500">
              <span>Tokens: <span className="text-gray-400 font-medium">{message.tokenUsage.totalTokens.toLocaleString()}</span></span>
              <span>Prompt: <span className="text-gray-400">{message.tokenUsage.promptTokens.toLocaleString()}</span></span>
              <span>Completion: <span className="text-gray-400">{message.tokenUsage.completionTokens.toLocaleString()}</span></span>
            </div>
          )}
        </div>

        <div className={clsx(
          'flex items-center gap-1 px-1 transition-opacity duration-200',
          message.role === 'user' ? 'justify-end' : 'justify-start',
          showActions || isLast ? 'opacity-100' : 'opacity-0'
        )}>
          <ActionBar
            message={message}
            isGenerating={isGenerating}
            onCopy={onCopy}
            copied={copied}
            onRegenerate={onRegenerate}
            onRetry={onRetry}
            onEdit={() => setEditing(true)}
            onDelete={onDelete}
            onShare={onShare}
            bookmarked={bookmarked}
            onBookmark={() => { setBookmarked(b => !b); notify(!bookmarked ? 'success' : 'info', !bookmarked ? 'Bookmarked' : 'Removed bookmark'); }}
            onPin={() => { pinChat(chatId); notify('info', 'Toggled pin'); }}
          />
        </div>
      </div>

      {message.role === 'user' && (
        <UserAvatar />
      )}

      {contextMenu && (
        <ContextMenuOverlay
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={() => setContextMenu(null)}
          items={[
            { icon: <Copy className="w-3.5 h-3.5" />, label: 'Copy', onClick: onCopy },
            ...(message.role === 'user' ? [{ icon: <Edit2 className="w-3.5 h-3.5" />, label: 'Edit', onClick: () => setEditing(true) }] : []),
            ...(message.role === 'assistant' ? [{ icon: <RefreshCw className="w-3.5 h-3.5" />, label: 'Regenerate', onClick: onRegenerate }] : []),
            { icon: <Trash2 className="w-3.5 h-3.5" />, label: 'Delete', onClick: onDelete, danger: true },
          ]}
        />
      )}
    </div>
  );
}

function ActionBar({
  message, isGenerating, onCopy, copied, onRegenerate, onRetry, onEdit, onDelete, onShare, bookmarked, onBookmark, onPin
}: any) {
  return (
    <div className="flex items-center gap-0.5 p-1 rounded-lg bg-[var(--card)] border border-[var(--border)] shadow-sm">
      <IconBtn label="Copy" onClick={onCopy}>
        {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
      </IconBtn>
      {message.role === 'assistant' && !isGenerating && (
        <>
          <IconBtn label="Regenerate" onClick={onRegenerate}>
            <RefreshCw className="w-3.5 h-3.5" />
          </IconBtn>
          <IconBtn label="Retry" onClick={onRetry}>
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>
          </IconBtn>
          <IconBtn label={bookmarked ? 'Remove bookmark' : 'Bookmark'} onClick={onBookmark}>
            <Bookmark className={clsx('w-3.5 h-3.5', bookmarked && 'fill-primary text-primary')} />
          </IconBtn>
        </>
      )}
      {message.role === 'user' && (
        <IconBtn label="Edit" onClick={onEdit}>
          <Edit2 className="w-3.5 h-3.5" />
        </IconBtn>
      )}
      <IconBtn label="Share" onClick={onShare}>
        <Share2 className="w-3.5 h-3.5" />
      </IconBtn>
      <IconBtn label="Pin chat" onClick={onPin}>
        <Pin className="w-3.5 h-3.5" />
      </IconBtn>
      <div className="w-px h-4 bg-[var(--border)] mx-0.5" />
      <IconBtn label="Good response" onClick={() => {}}>
        <ThumbsUp className="w-3.5 h-3.5" />
      </IconBtn>
      <IconBtn label="Bad response" onClick={() => {}}>
        <ThumbsDown className="w-3.5 h-3.5" />
      </IconBtn>
      <div className="w-px h-4 bg-[var(--border)] mx-0.5" />
      <IconBtn label="Delete" onClick={onDelete} danger>
        <Trash2 className="w-3.5 h-3.5" />
      </IconBtn>
    </div>
  );
}

function IconBtn({ children, onClick, label, danger }: { children: React.ReactNode; onClick: () => void; label: string; danger?: boolean }) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className={clsx(
        'p-1.5 rounded-md transition-colors',
        danger ? 'text-gray-500 hover:text-danger hover:bg-danger/10' : 'text-gray-500 hover:text-[var(--foreground)] hover:bg-white/10'
      )}
    >
      {children}
    </button>
  );
}

function TypingIndicator() {
  return (
    <span className="inline-flex items-center gap-1 ml-1 align-middle">
      <span className="w-1.5 h-1.5 bg-primary rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
      <span className="w-1.5 h-1.5 bg-primary rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
      <span className="w-1.5 h-1.5 bg-primary rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
    </span>
  );
}

function UserAvatar() {
  const { profile } = useApp();
  const initials = profile.name.split(' ').map(n => n[0]).filter(Boolean).slice(0, 2).join('').toUpperCase();
  return (
    <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-primary to-accent flex-shrink-0 flex items-center justify-center text-white font-medium text-sm shadow-md">
      {profile.avatar ? (
        <img src={profile.avatar} alt="" className="w-full h-full rounded-full object-cover" />
      ) : initials || 'U'}
    </div>
  );
}

function ContextMenuOverlay({ x, y, items, onClose }: { x: number; y: number; items: Array<{ icon?: React.ReactNode; label: string; onClick: () => void; danger?: boolean }>; onClose: () => void }) {
  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        style={{ top: y, left: x }}
        className="fixed z-50 min-w-[200px] bg-[var(--card)] border border-[var(--border)] rounded-xl shadow-2xl py-1.5 animate-in fade-in zoom-in-95 duration-100"
      >
        {items.map((item, i) => (
          <button
            key={i}
            onClick={() => { item.onClick(); onClose(); }}
            className={clsx(
              'w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left transition-colors',
              item.danger ? 'text-danger hover:bg-danger/10' : 'text-gray-200 hover:bg-white/10'
            )}
          >
            <span className="w-4 h-4 flex items-center justify-center">{item.icon}</span>
            {item.label}
          </button>
        ))}
      </div>
    </>
  );
}

export type { Message };
